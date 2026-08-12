# gitpilot/sandbox_routing.py
"""Routing a shell command to the configured sandbox — extracted in Batch V4-A4.

Moved out of :mod:`gitpilot.local_tools` (which imports CrewAI, and litellm
behind it) so the canonical ``terminal.*`` tools can reach the same backends
without dragging that dependency into the agentic loop's process.  The logic,
the dispatch precedence and the rendered output are unchanged;
``local_tools`` now calls these functions, so the two surfaces cannot diverge.

Async is the primitive here and the sync wrappers are the adapters, which is the
opposite of how ``local_tools`` had it — CrewAI tools must be synchronous, but
the loop is async, and bridging every registry call through a thread pool would
be needless overhead in the direction we are heading.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Coroutine, Optional, TypeVar

logger = logging.getLogger(__name__)

#: Clamp for every timeout accepted from a model or a settings file.
MIN_TIMEOUT_SEC = 1
MAX_TIMEOUT_SEC = 600
DEFAULT_TIMEOUT_SEC = 120


_T = TypeVar("_T")


class SandboxFallback(Exception):
    """The sandbox path is unusable; the caller may fall back to the legacy executor.

    Raised only for *import* failures (a stripped runtime without httpx, say) —
    never for a policy refusal or a backend error, which are real answers and
    must not be quietly downgraded to a weaker executor.
    """


def coerce_timeout(value: object) -> int:
    """Clamp anything into [1, 600] seconds, defaulting on nonsense."""
    try:
        seconds = int(str(value))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SEC
    if seconds <= 0:
        return DEFAULT_TIMEOUT_SEC
    return min(max(seconds, MIN_TIMEOUT_SEC), MAX_TIMEOUT_SEC)


def run_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run *coro* from synchronous code, even inside a live event loop.

    The thread-pool branch exists because CrewAI invokes tools from a thread
    that may already own a loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def format_sandbox_output(result: Any, label: str) -> str:
    """Render a sandbox result as the text block agents already read.

    Kept byte-identical to the previous ``local_tools._format_sandbox_output``:
    prompts and the parity gate both depend on this shape, and the backend line
    is what tells a reader which sandbox actually ran the command.
    """
    backend = getattr(result, "backend", None) or "subprocess"
    pretty = {
        "subprocess": "local subprocess",
        "matrixlab": "MatrixLab",
        "off": "pass-through (host)",
    }.get(backend, backend)
    lines = [f"Sandbox: {pretty}", f"Command: {label}", f"Exit code: {result.exit_code}"]
    if getattr(result, "duration_ms", None) is not None:
        lines.append(f"Duration: {result.duration_ms} ms")
    if result.stdout:
        lines.append("--- stdout ---")
        lines.append(result.stdout)
    if result.stderr:
        lines.append("--- stderr ---")
        lines.append(result.stderr)
    if getattr(result, "timed_out", False):
        lines.append("WARNING: Command timed out")
    if getattr(result, "truncated", False):
        lines.append("WARNING: Output was truncated")
    sandbox_id = getattr(result, "sandbox_id", None)
    if sandbox_id:
        lines.append(f"sandbox_id: {sandbox_id}")
    return "\n".join(lines) + "\n"


class SnippetResult:
    """The shape :func:`format_sandbox_output` expects, from the HTTP snippet API."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.backend = data.get("backend")
        self.exit_code = data.get("exit_code")
        self.stdout = data.get("stdout", "")
        self.stderr = data.get("stderr", "")
        self.duration_ms = data.get("duration_ms")
        self.timed_out = data.get("timed_out", False)
        self.truncated = data.get("truncated", False)
        self.sandbox_id = data.get("sandbox_id")


async def run_command_in_workspace(
    command: str, timeout: int, workspace_path: Path | str,
) -> str:
    """Execute *command* in the workspace through the configured backend.

    Dispatch precedence is the settings' ``sandbox.backend``:

    * ``matrixlab`` — rerouted as a ``bash`` snippet through GitPilot's own
      ``/api/sandbox/run``.  MatrixLab's ``/repo/run`` contract wants a real
      ``repo_url`` for cloning and running CI, which is not what an
      in-workspace command is.
    * ``off`` — :class:`~gitpilot.sandbox.NullSandbox`, still denylist-checked.
    * anything else — :class:`~gitpilot.sandbox.SubprocessSandbox`, cwd jailed
      to the workspace with credentials scrubbed from the environment.
    """
    try:
        from .sandbox import (
            BACKEND_MATRIXLAB,
            BACKEND_OFF,
            BACKEND_SUBPROCESS,
            NullSandbox,
            SandboxPolicy,
            SandboxRunError,
            SandboxUnavailableError,
            SubprocessSandbox,
        )
        from .settings import get_settings
    except Exception as exc:  # noqa: BLE001
        raise SandboxFallback(str(exc)) from exc

    cfg = get_settings().sandbox
    backend = (cfg.backend or BACKEND_SUBPROCESS).strip().lower()

    if backend == BACKEND_MATRIXLAB:
        return await run_snippet(("bash"), command, timeout)

    policy = SandboxPolicy(
        workspace=Path(workspace_path),
        timeout_sec=timeout,
        allow_network=cfg.allow_network,
        image=cfg.matrixlab_image or None,
    )
    sandbox = NullSandbox(policy) if backend == BACKEND_OFF else SubprocessSandbox(policy)

    try:
        try:
            result = await sandbox.run(command, timeout=timeout)
        finally:
            # Run and close in one loop: a backend that lazily builds an
            # httpx.AsyncClient must not have it closed from a different loop.
            aclose = getattr(sandbox, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # noqa: BLE001
                    pass
    except SandboxUnavailableError as exc:
        return f"Error: sandbox backend {backend!r} unreachable: {exc}\n"
    except SandboxRunError as exc:
        return f"Error: sandbox backend {backend!r} reported an error: {exc}\n"
    except PermissionError as exc:
        return f"Permission denied by sandbox policy: {exc}\n"

    return format_sandbox_output(result, command)


def _mint_internal_approval(language: str, code: str) -> str:
    """An approval token for a run the policy engine already authorized.

    Returns ``""`` when the token store is unavailable, in which case the request
    goes without one and the endpoint decides — failing closed rather than
    inventing a bypass.
    """
    try:
        from .sandbox_tokens import get_store

        store = get_store()
        plan_id = f"internal-{uuid.uuid4().hex[:12]}"
        store.remember(
            plan_id=plan_id, session_id="", language=language, code=code,
            plan={"source": "agent", "language": language},
        )
        token = store.approve(plan_id, "")
        return token.token if token else ""
    except Exception as exc:  # noqa: BLE001 - the endpoint refuses; that is correct
        logger.debug("could not mint an internal sandbox approval: %s", exc)
        return ""


async def run_snippet(language: str, code: str, timeout: int) -> str:
    """Run a self-contained snippet via GitPilot's own ``/api/sandbox/run``.

    Going through the HTTP surface rather than reaching into ``sandbox_api``
    keeps the agent and the chat UI's Run button on one code path, including
    lifecycle and cleanup.
    """
    try:
        import httpx
    except Exception as exc:  # noqa: BLE001
        raise SandboxFallback(str(exc)) from exc

    port = os.environ.get("GITPILOT_PORT") or "8765"
    base = os.environ.get("GITPILOT_INTERNAL_URL") or f"http://127.0.0.1:{port}"
    body: dict[str, Any] = {"language": language, "code": code, "timeout_sec": timeout}

    # `/api/sandbox/run` requires proof of approval since Batch V4-D6 (F7). This
    # caller has already been authorized — `terminal.run_snippet` is a high-risk
    # tool, so the PolicyEngine asked before we got here — but the endpoint has
    # no way to know that, and an internal-caller escape hatch would be the same
    # "trust whoever asks" defect F7 was. So the approval is recorded properly:
    # the store lives in this process, so it costs a function call rather than
    # two more HTTP round-trips.
    token = _mint_internal_approval(language, code)
    if token:
        body["approval_token"] = token

    try:
        async with httpx.AsyncClient(timeout=timeout + 10) as client:
            response = await client.post(f"{base}/api/sandbox/run", json=body)
    except httpx.HTTPError as exc:
        return f"Error: could not reach the in-process sandbox API: {exc}\n"

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except Exception:  # noqa: BLE001
            detail = response.text
        return f"Sandbox error ({response.status_code}): {detail}\n"

    return format_sandbox_output(SnippetResult(response.json()), f"{language} <snippet>")


# ---------------------------------------------------------------------------
# Synchronous adapters for the CrewAI tools
# ---------------------------------------------------------------------------


def run_command_in_workspace_sync(
    command: str, timeout: int, workspace_path: Path | str,
) -> str:
    return run_sync(run_command_in_workspace(command, timeout, workspace_path))


def run_snippet_sync(language: str, code: str, timeout: int) -> str:
    return run_sync(run_snippet(language, code, timeout))
