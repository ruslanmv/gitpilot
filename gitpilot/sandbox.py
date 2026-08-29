# gitpilot/sandbox.py
"""Sandboxed tool execution — pluggable, additive, non-destructive.

The default behaviour of GitPilot is unchanged: when no sandbox is
configured, callers fall back to the existing :mod:`gitpilot.terminal`
and :mod:`gitpilot.local_tools` modules which run on the host
filesystem.  Opting in requires only a single line::

    from gitpilot.sandbox import get_sandbox
    sb = get_sandbox()                # honours env + settings
    result = await sb.run(["pytest", "-q"])

Sandbox backends
----------------

* :class:`NullSandbox` — passthrough (legacy behaviour, host FS).
* :class:`SubprocessSandbox` — host subprocess with cwd jail, env
  scrub, output cap, blocked-pattern checks.  Always available.
* :class:`MatrixLabSandbox` — delegates execution to a MatrixLab
  Runner over HTTP (default ``http://localhost:8765``).  Containerised
  isolation: ephemeral filesystem, resource limits, no host access.

Selection precedence::

    explicit ``backend=`` argument
    > GITPILOT_SANDBOX env var ("matrixlab" | "subprocess" | "off")
    > settings.json    ``tools.sandbox``
    > "subprocess"      (the safe default for hosted commands)

Configuration is decoupled from the existing :mod:`gitpilot.terminal`
executor so adopting the sandbox is incremental: switch one tool
invocation at a time.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import shlex
import signal
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import httpx

from .shell_safety import BLOCKED_PATTERNS as _SHARED_BLOCKED_PATTERNS
from .shell_safety import NETWORK_ENV_KEYS as _SHARED_NETWORK_ENV_KEYS
from .shell_safety import SECRET_ENV_KEYS as _SHARED_SECRET_ENV_KEYS
from .shell_safety import blocked_reason, strip_secret_env

logger = logging.getLogger(__name__)

# Backend identifiers ---------------------------------------------------
BACKEND_OFF = "off"
BACKEND_SUBPROCESS = "subprocess"
BACKEND_MATRIXLAB = "matrixlab"

DEFAULT_BACKEND = BACKEND_SUBPROCESS

ENV_BACKEND = "GITPILOT_SANDBOX"
ENV_MATRIXLAB_URL = "GITPILOT_MATRIXLAB_URL"
ENV_MATRIXLAB_TOKEN = "GITPILOT_MATRIXLAB_TOKEN"
ENV_MATRIXLAB_IMAGE = "GITPILOT_MATRIXLAB_IMAGE"

DEFAULT_TIMEOUT_SEC = 120
MAX_OUTPUT_BYTES = 512_000
# MatrixLab's docker-compose binds to host :8765 (not :8000) so the
# Runner doesn't collide with GitPilot's own FastAPI backend on :8000.
# Operators with an older MatrixLab install on :8000 can override with
# GITPILOT_MATRIXLAB_URL or via Settings → Sandbox.
DEFAULT_MATRIXLAB_URL = "http://localhost:8765"

# Directories never worth shipping to the Runner in a workspace zip.
_WORKSPACE_ZIP_SKIP = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist", "build",
})

# Conservative deny patterns reused across backends.  Defined in
# :mod:`gitpilot.shell_safety` so the terminal executor cannot drift from it
# (Batch V4-0C); re-exported here because this name is part of the module's
# established surface.
BLOCKED_PATTERNS: Tuple[str, ...] = _SHARED_BLOCKED_PATTERNS


# ----------------------------------------------------------------------
# Result + policy types
# ----------------------------------------------------------------------

@dataclass
class SandboxResult:
    """Outcome of a sandboxed command."""

    backend: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False
    timed_out: bool = False
    artifacts: List[str] = field(default_factory=list)
    sandbox_id: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class SandboxPolicy:
    """Runtime knobs applied uniformly across backends."""

    workspace: Optional[Path] = None
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    max_output_bytes: int = MAX_OUTPUT_BYTES
    extra_env: Dict[str, str] = field(default_factory=dict)
    allow_network: bool = True
    allowed_commands: Optional[List[str]] = None
    blocked_patterns: Tuple[str, ...] = BLOCKED_PATTERNS
    image: Optional[str] = None  # MatrixLab image override

    def validate(self, command_str: str) -> None:
        matched = blocked_reason(command_str, self.blocked_patterns)
        if matched is not None:
            raise PermissionError(f"command blocked by sandbox policy: {matched!r}")
        lower = command_str.lower().strip()
        if self.allowed_commands is not None and lower:
            base = lower.split()[0]
            if base not in self.allowed_commands:
                raise PermissionError(f"command not in allowlist: {base!r}")


# ----------------------------------------------------------------------
# Backend interface
# ----------------------------------------------------------------------

class Sandbox:
    """Abstract sandbox interface.  Subclasses implement :meth:`run`."""

    backend: str = "abstract"

    def __init__(self, policy: Optional[SandboxPolicy] = None) -> None:
        self.policy = policy or SandboxPolicy()

    async def run(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
        stdin: Optional[str] = None,
    ) -> SandboxResult:
        raise NotImplementedError

    async def health(self) -> Dict[str, Any]:
        """Optional liveness probe.  Default: always healthy."""
        return {"backend": self.backend, "ok": True}

    # ------------------------------------------------------------------
    # Helpers shared across backends
    # ------------------------------------------------------------------
    def _resolve_command(self, command: Sequence[str] | str) -> Tuple[str, List[str]]:
        if isinstance(command, str):
            return command, shlex.split(command)
        cmd_list = list(command)
        return shlex.join(cmd_list), cmd_list

    def _truncate(self, data: bytes) -> Tuple[str, bool]:
        cap = self.policy.max_output_bytes
        if len(data) > cap:
            return data[:cap].decode("utf-8", errors="replace"), True
        return data.decode("utf-8", errors="replace"), False


def _kill_process_tree(proc: "asyncio.subprocess.Process") -> None:
    """Kill the whole process group the command was started in.

    ``create_subprocess_shell`` runs ``/bin/sh -c <command>``, and on the
    shells that fork rather than exec, killing that shell leaves the real
    program running: it keeps the host busy forever *and* holds the
    stdout/stderr pipes open, so the sandbox could not even tell that the
    run had ended.  Because the launch asks for ``start_new_session``,
    the whole tree is one process group and one signal reaches all of it.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError, OSError):
        pgid = None
    # Never signal our own group — that would take the server down with it.
    if pgid is not None and pgid != os.getpgid(0):
        try:
            os.killpg(pgid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass


async def _drain(
    stream: Optional[asyncio.StreamReader], buf: bytearray, cap: int,
) -> None:
    """Accumulate *stream* into *buf*, keeping at most *cap* bytes.

    Reading into a buffer the caller owns is what makes partial output
    survivable: when the run is killed at the timeout the bytes already
    read are still in ``buf``, whereas ``communicate()`` loses everything
    it had buffered when :func:`asyncio.wait_for` cancels it.

    Past the cap the stream is still drained, just discarded — a process
    whose output nobody reads blocks on a full pipe and would then be
    killed as a timeout, turning a merely chatty script into a failure.
    Keeping one chunk past the cap is what marks the result truncated.
    """
    if stream is None:
        return
    while True:
        chunk = await stream.read(65_536)
        if not chunk:
            return
        if len(buf) <= cap:
            buf.extend(chunk)


async def _communicate(
    proc: "asyncio.subprocess.Process",
    stdin: Optional[str],
    timeout: float,
    cap: int,
) -> Tuple[bytes, bytes, bool]:
    """Run *proc* to completion, returning ``(stdout, stderr, timed_out)``.

    Replaces ``asyncio.wait_for(proc.communicate(), timeout)``, which
    discarded every byte the process had produced whenever it hit the
    timeout — so a script that printed thirty lines and then blocked
    showed the user an empty result and no clue where it stopped.
    """
    out, err = bytearray(), bytearray()

    if stdin is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin.encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass

    tasks = [
        asyncio.ensure_future(_drain(proc.stdout, out, cap)),
        asyncio.ensure_future(_drain(proc.stderr, err, cap)),
        asyncio.ensure_future(proc.wait()),
    ]

    # ``asyncio.wait`` rather than ``wait_for``: it reports the timeout
    # without cancelling anything.  A cancelled ``proc.wait()`` leaves the
    # subprocess transport unreaped — the process is gone but its
    # destructor still fires later, on a loop that has since closed
    # ("Event loop is closed" from a __del__ nobody can trace).
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    timed_out = bool(pending)

    if timed_out:
        _kill_process_tree(proc)
        # The pipes reach EOF once the process dies, so the same tasks
        # finish on their own; a bounded second wait collects them
        # (and the exit status) without re-hanging the call.
        _, still_pending = await asyncio.wait(pending, timeout=5)
        for task in still_pending:
            task.cancel()
        if still_pending:
            await asyncio.gather(*still_pending, return_exceptions=True)

    return bytes(out), bytes(err), timed_out


# ----------------------------------------------------------------------
# NullSandbox  — explicit passthrough (legacy)
# ----------------------------------------------------------------------

class NullSandbox(Sandbox):
    """No isolation; runs in the current process via ``asyncio.subprocess``.

    Provided so callers can keep a single :class:`Sandbox`-shaped
    interface even when sandboxing is disabled.  This **does not**
    replace :mod:`gitpilot.terminal`; existing terminal sessions
    continue to work unchanged.
    """

    backend = BACKEND_OFF

    async def run(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
        stdin: Optional[str] = None,
    ) -> SandboxResult:
        command_str, _ = self._resolve_command(command)
        self.policy.validate(command_str)
        full_env = {**os.environ, **self.policy.extra_env, **(env or {})}
        start = time.monotonic()
        proc = await asyncio.create_subprocess_shell(
            command_str,
            cwd=str(cwd or self.policy.workspace or Path.cwd()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # DEVNULL, never the parent's stdin: a server started from a
            # terminal would otherwise hand the sandboxed process its tty,
            # so an ``input()`` call blocks until the timeout instead of
            # raising EOFError immediately.
            stdin=(asyncio.subprocess.PIPE if stdin is not None
                   else asyncio.subprocess.DEVNULL),
            env=full_env,
            # Its own session/process group, so a timeout can kill the
            # whole tree rather than just the ``sh -c`` wrapper, and so
            # the command never shares the server's controlling terminal.
            start_new_session=True,
        )
        stdout_b, stderr_b, timed_out = await _communicate(
            proc,
            stdin,
            timeout or self.policy.timeout_sec,
            self.policy.max_output_bytes,
        )
        stdout, truncated_out = self._truncate(stdout_b)
        stderr, truncated_err = self._truncate(stderr_b)
        return SandboxResult(
            backend=self.backend,
            command=command_str,
            exit_code=(proc.returncode if proc.returncode is not None else -1) if not timed_out else -1,
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((time.monotonic() - start) * 1000),
            truncated=truncated_out or truncated_err,
            timed_out=timed_out,
        )


# ----------------------------------------------------------------------
# SubprocessSandbox  — host subprocess with cwd jail
# ----------------------------------------------------------------------

class SubprocessSandbox(Sandbox):
    """Host subprocess constrained to the workspace.

    A pragmatic step up from :class:`NullSandbox`: the cwd is forced
    into ``policy.workspace`` (no escape via ``cd``); the environment
    is scrubbed unless ``allow_network`` is true (``HTTP_PROXY``-style
    vars and ``GITHUB_TOKEN`` are dropped); blocked patterns are
    enforced before launch.

    Real container isolation should use :class:`MatrixLabSandbox`.
    """

    backend = BACKEND_SUBPROCESS

    # Keys removed from the environment when ``allow_network`` is False.
    _NETWORK_ENV_KEYS = _SHARED_NETWORK_ENV_KEYS
    # Always stripped — secrets that shouldn't leak into sandboxed runs.
    _STRIP_ALWAYS = _SHARED_SECRET_ENV_KEYS

    async def run(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
        stdin: Optional[str] = None,
    ) -> SandboxResult:
        command_str, _ = self._resolve_command(command)
        self.policy.validate(command_str)
        workspace = (self.policy.workspace or Path.cwd()).resolve()
        target_cwd = (cwd or workspace).resolve()
        if not str(target_cwd).startswith(str(workspace)):
            target_cwd = workspace
        full_env = self._build_env(env)
        start = time.monotonic()
        proc = await asyncio.create_subprocess_shell(
            command_str,
            cwd=str(target_cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # DEVNULL, never the parent's stdin: a server started from a
            # terminal would otherwise hand the sandboxed process its tty,
            # so an ``input()`` call blocks until the timeout instead of
            # raising EOFError immediately.
            stdin=(asyncio.subprocess.PIPE if stdin is not None
                   else asyncio.subprocess.DEVNULL),
            env=full_env,
            # Its own session/process group, so a timeout can kill the
            # whole tree rather than just the ``sh -c`` wrapper, and so
            # the command never shares the server's controlling terminal.
            start_new_session=True,
        )
        stdout_b, stderr_b, timed_out = await _communicate(
            proc,
            stdin,
            timeout or self.policy.timeout_sec,
            self.policy.max_output_bytes,
        )
        stdout, truncated_out = self._truncate(stdout_b)
        stderr, truncated_err = self._truncate(stderr_b)
        return SandboxResult(
            backend=self.backend,
            command=command_str,
            exit_code=(proc.returncode if proc.returncode is not None else -1) if not timed_out else -1,
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((time.monotonic() - start) * 1000),
            truncated=truncated_out or truncated_err,
            timed_out=timed_out,
        )

    def _build_env(self, overrides: Optional[Mapping[str, str]]) -> Dict[str, str]:
        env = strip_secret_env(allow_network=self.policy.allow_network)
        env.update(self.policy.extra_env)
        if overrides:
            env.update(overrides)
        return env


# ----------------------------------------------------------------------
# MatrixLabSandbox  — containerised execution via Runner HTTP API
# ----------------------------------------------------------------------

class MatrixLabSandbox(Sandbox):
    """Delegate execution to a MatrixLab Runner over HTTP.

    MatrixLab provides containerised, ephemeral execution suitable for
    untrusted code: each ``run`` is dispatched to a disposable
    container, the workspace is mounted read-write into the container's
    scratch directory, and resource limits are enforced by the runner.

    The runner endpoint defaults to ``http://localhost:8765``; override
    via the ``GITPILOT_MATRIXLAB_URL`` environment variable or by
    passing ``base_url`` to the constructor.

    The protocol is the Runner's **native** contract: ``POST /run`` with
    a JSON body ``{cmd, cwd, workspace, env, timeout, image,
    allow_network, stdin}`` and a response containing ``exit_code``,
    ``stdout``, ``stderr``, ``artifacts`` and ``sandbox_id``.  When
    MatrixLab is unreachable, callers should pick a different backend
    (this class deliberately surfaces a clear error instead of silently
    falling back, so security-sensitive runs are never mis-routed).

    This used to POST ``/repo/run`` with ``mount_workspace``.  That is a
    different endpoint on the Runner — it clones a **git repository**
    and its request model requires ``repo_url``, so every workspace
    command came back ``422 Unprocessable Entity`` and the backend
    looked broken rather than misaddressed.  The Runner has no host
    mount either: a workspace travels as a zip, which is what
    :meth:`_workspace_payload` builds.
    """

    #: Cap on the zipped workspace shipped to the Runner.  Well under the
    #: Runner's own limit; a workspace larger than this is a sign the
    #: caller meant to run against a repo checkout, not a snippet dir.
    max_workspace_bytes = 32 * 1024 * 1024

    backend = BACKEND_MATRIXLAB

    def __init__(
        self,
        policy: Optional[SandboxPolicy] = None,
        *,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(policy)
        self.base_url = (base_url or os.environ.get(ENV_MATRIXLAB_URL) or DEFAULT_MATRIXLAB_URL).rstrip("/")
        self.token = token or os.environ.get(ENV_MATRIXLAB_TOKEN)
        self._http = http_client
        self._owns_http = http_client is None

    async def health(self) -> Dict[str, Any]:
        """Probe ``GET /health`` and report the Runner's *own* verdict.

        A 200 is not enough to call the backend usable: the Runner
        answers while its Docker daemon is down, and reporting that as
        healthy sent users to a green pill in Settings followed by a
        failure on every run.  ``ok`` now means "the Runner says it can
        execute", and the reason travels with it.
        """
        try:
            client = await self._client()
            resp = await client.get(f"{self.base_url}/health", timeout=5.0)
            resp.raise_for_status()
            remote = resp.json()
        except Exception as exc:
            return {"backend": self.backend, "ok": False, "error": str(exc)}

        if isinstance(remote, dict) and remote.get("ok") is False:
            docker = remote.get("docker") or {}
            detail = ""
            if isinstance(docker, dict):
                detail = str(docker.get("error") or docker.get("detail") or "")
            return {
                "backend": self.backend,
                "ok": False,
                "remote": remote,
                "error": (
                    "MatrixLab Runner is reachable but reports it cannot "
                    "execute (its Docker daemon is unavailable)"
                    + (f": {detail}" if detail else "")
                ),
            }
        return {"backend": self.backend, "ok": True, "remote": remote}

    def _workspace_payload(self) -> Optional[Dict[str, Any]]:
        """Zip ``policy.workspace`` into the Runner's workspace ref.

        Returns ``None`` when there is nothing to ship (no workspace, an
        empty one, or one over :attr:`max_workspace_bytes`) so the command
        still runs — just against the image's bare filesystem.
        """
        workspace = self.policy.workspace
        if workspace is None:
            return None
        root = Path(workspace)
        if not root.is_dir():
            return None

        buf = io.BytesIO()
        total = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                rel = path.relative_to(root)
                if any(part in _WORKSPACE_ZIP_SKIP for part in rel.parts):
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                total += size
                if total > self.max_workspace_bytes:
                    logger.warning(
                        "workspace %s exceeds %d bytes — running without it",
                        root, self.max_workspace_bytes,
                    )
                    return None
                zf.write(path, arcname=str(rel))

        payload = buf.getvalue()
        if len(payload) <= 22:  # an empty zip is just the end-of-archive record
            return None
        return {
            "type": "zip",
            "zip_base64": base64.b64encode(payload).decode("ascii"),
        }

    async def run(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
        stdin: Optional[str] = None,
    ) -> SandboxResult:
        command_str, _ = self._resolve_command(command)
        self.policy.validate(command_str)
        # ``cwd`` is a path *inside the container*, and the workspace is
        # unpacked at its root — so a host path here (what this used to
        # send) named a directory the container does not have.  An
        # explicit cwd is honoured only when it is relative.
        rel_cwd = "."
        if cwd is not None:
            candidate = Path(cwd)
            if candidate.is_absolute() and self.policy.workspace is not None:
                try:
                    rel_cwd = str(candidate.resolve().relative_to(
                        Path(self.policy.workspace).resolve()
                    )) or "."
                except ValueError:
                    rel_cwd = "."
            elif not candidate.is_absolute():
                rel_cwd = str(candidate)

        body: Dict[str, Any] = {
            "cmd": command_str,
            "cwd": rel_cwd,
            "env": {**self.policy.extra_env, **(env or {})},
            "timeout": timeout or self.policy.timeout_sec,
            "allow_network": self.policy.allow_network,
        }
        # ``image`` is a non-nullable string on the Runner with a working
        # default — send it only when we actually have an override.
        image = self.policy.image or os.environ.get(ENV_MATRIXLAB_IMAGE)
        if image:
            body["image"] = image
        if stdin is not None:
            body["stdin"] = stdin
        workspace_ref = self._workspace_payload()
        if workspace_ref is not None:
            body["workspace"] = workspace_ref

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        client = await self._client()
        start = time.monotonic()
        try:
            resp = await client.post(
                f"{self.base_url}/run",
                json=body,
                headers=headers,
                timeout=(timeout or self.policy.timeout_sec) + 5,
            )
        except httpx.HTTPError as exc:
            raise SandboxUnavailableError(f"MatrixLab unreachable: {exc}") from exc

        duration_ms = int((time.monotonic() - start) * 1000)
        try:
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPStatusError, json.JSONDecodeError) as exc:
            # Carry the Runner's own body into the message: a bare
            # "422 Unprocessable Entity" tells the operator nothing about
            # which field the Runner rejected.
            detail = ""
            try:
                detail = f" — {resp.text[:400]}"
            except Exception:  # noqa: BLE001
                pass
            raise SandboxRunError(
                f"MatrixLab returned an error: {exc}{detail}"
            ) from exc

        return SandboxResult(
            backend=self.backend,
            command=command_str,
            exit_code=int(data.get("exit_code", -1)),
            stdout=str(data.get("stdout", ""))[: self.policy.max_output_bytes],
            stderr=str(data.get("stderr", ""))[: self.policy.max_output_bytes],
            duration_ms=int(data.get("duration_ms", duration_ms)),
            truncated=bool(data.get("truncated", False)),
            timed_out=bool(data.get("timed_out", False)),
            artifacts=list(data.get("artifacts", [])),
            sandbox_id=data.get("sandbox_id"),
        )

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient()
        return self._http


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------

class SandboxError(RuntimeError):
    """Base class for sandbox failures."""


class SandboxUnavailableError(SandboxError):
    """Raised when a backend cannot be reached."""


class SandboxRunError(SandboxError):
    """Raised when the backend processed the request but reported a problem."""


# ----------------------------------------------------------------------
# Resolution / factory
# ----------------------------------------------------------------------

def _resolve_backend_name(explicit: Optional[str], settings: Optional[Mapping[str, Any]]) -> str:
    if explicit:
        return explicit
    env_val = os.environ.get(ENV_BACKEND)
    if env_val:
        return env_val
    if settings:
        configured = settings.get("tools", {}).get("sandbox") if isinstance(settings, Mapping) else None
        if isinstance(configured, str):
            return configured
    return DEFAULT_BACKEND


def get_sandbox(
    backend: Optional[str] = None,
    *,
    policy: Optional[SandboxPolicy] = None,
    settings: Optional[Mapping[str, Any]] = None,
) -> Sandbox:
    """Return an initialised sandbox according to precedence rules."""
    name = _resolve_backend_name(backend, settings)
    name = name.strip().lower()
    if name in {BACKEND_OFF, "false", "0", "none"}:
        return NullSandbox(policy)
    if name == BACKEND_MATRIXLAB:
        return MatrixLabSandbox(policy)
    if name == BACKEND_SUBPROCESS:
        return SubprocessSandbox(policy)
    # Unknown backend → safest default.
    logger.warning("unknown sandbox backend %r, falling back to subprocess", name)
    return SubprocessSandbox(policy)
