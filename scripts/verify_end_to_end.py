#!/usr/bin/env python3
"""End-to-end verification: a model writes hello.py, and we run it.

The smallest task that proves the whole stack, and it proves it by *executing the
result* rather than by inspecting a transcript:

    settings → resolve_endpoint → OpenAICompatibleProvider → HTTP → dialect
      → AgentLoop → PolicyEngine → ToolRegistry → fs.write → journal
      → `python hello.py` → "Hello, world!"

Two modes, one script.

**Against a real model** (what you want, and what CI cannot have)::

    ollama pull qwen2.5:1.5b
    python scripts/verify_end_to_end.py --real --model qwen2.5:1.5b

It talks to whatever is on ``OLLAMA_BASE_URL`` (default ``http://localhost:11434``)
through Ollama's OpenAI-compatible surface at ``/v1`` — the same code path a user's
session takes. Nothing about the run is special-cased for the verification.

**Against a local stub** (the default, and what runs in CI)::

    python scripts/verify_end_to_end.py

A real HTTP server on an ephemeral port, speaking ``POST /v1/chat/completions``,
stands in for the model. Everything else is the same objects doing the same work:
the provider makes a real HTTP request, the dialect parses a real response, the
policy engine authorises a real ``fs.write``, and the file that appears on disk is
run by a real subprocess.

What the stub does **not** verify is the model — whether a 1.5B model can actually
follow the protocol well enough to get here. That is what ``--real`` is for, and it
is why this script exists as a command rather than only as a test: the answer
depends on the model in front of it, so it has to be re-asked per model.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TASK = (
    "Create a file called hello.py in the project root. It should print exactly "
    "Hello, world! when run. Do not create anything else."
)

EXPECTED_OUTPUT = "Hello, world!"

HELLO_SOURCE = 'print("Hello, world!")\n'


# ---------------------------------------------------------------------------
# The stub model
# ---------------------------------------------------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    """One OpenAI-compatible endpoint, answering as a competent small model would.

    It reacts to the request rather than replaying a fixed script: if the request
    carries a ``tools`` array it answers with a tool call, and if it does not — the
    text dialects — it answers in that dialect's grammar. That is what makes this a
    test of the provider and the dialect rather than of a canned string.
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - stdlib hook
        pass   # the harness prints its own progress

    def do_GET(self) -> None:   # noqa: N802 - stdlib hook
        if self.path.startswith("/api/tags"):
            self._json({"models": [{"name": self.server.model}]})   # type: ignore[attr-defined]
            return
        self._json({"object": "list", "data": [{"id": self.server.model}]})  # type: ignore[attr-defined]

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = {}

        self.server.calls.append(body)                    # type: ignore[attr-defined]
        turn = len(self.server.calls)                     # type: ignore[attr-defined]
        message = _stub_turn(body, turn)

        self._json({
            "id": f"stub-{turn}",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model") or self.server.model,   # type: ignore[attr-defined]
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
        })

    def _json(self, payload: Dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _already_written(body: Dict[str, Any]) -> bool:
    """Whether the transcript already shows hello.py having been written.

    The stub keys on *evidence*, not on a request counter, and that is not a
    stylistic choice: an adapter is free to make more than one HTTP call per
    iteration — the REACT_TEXT one does, when it streams and then re-asks — so a
    counter hands the loop's first turn whatever the script had queued second. It is
    also how a real model behaves: emit the action, see the observation, conclude.
    """
    for message in body.get("messages") or []:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        if role in ("tool", "function") and "hello.py" in content:
            return True
        if role == "user" and "hello.py" in content and (
            "Observation" in content or "wrote" in content or '"ok"' in content
        ):
            return True
    return bool(message_has_tool_result(body))


def message_has_tool_result(body: Dict[str, Any]) -> bool:
    return any(
        message.get("role") in ("tool", "function")
        for message in (body.get("messages") or [])
    )


def _stub_turn(body: Dict[str, Any], turn: int) -> Dict[str, Any]:
    """What the stub says, given what it was sent."""
    done = _already_written(body)

    if body.get("tools"):
        # NATIVE: the provider offered function schemas, so answer with a call.
        if done:
            return {
                "role": "assistant",
                "content": "I created hello.py; it prints Hello, world! when run.",
            }
        return {
            "role": "assistant",
            "content": "Creating hello.py.",
            "tool_calls": [{
                "id": f"call_{turn}",
                "type": "function",
                "function": {
                    "name": "fs.write",
                    "arguments": json.dumps({"path": "hello.py", "content": HELLO_SOURCE}),
                },
            }],
        }

    # Match on the whole request, not just the system role. A dialect is free to
    # deliver its phase instructions as a user message — LITE does — and a stub that
    # only reads the system prompt silently answers the wrong phase.
    blob = json.dumps(body)

    if "ACTION is one of" in blob or "ACTION filepath" in blob:
        # LITE's act phase. The prompt asks for the action line *and* the full new
        # content in a fenced block whose info line is the same path — an action with
        # no body is dropped rather than written as an empty file, which is correct
        # and which the first draft of this stub fell foul of.
        return {
            "role": "assistant",
            "content": f"CREATE hello.py\n```hello.py\n{HELLO_SOURCE}```\n",
        }
    if "READ path" in blob:
        # LITE's investigate phase: nothing needs reading to write a new file.
        return {"role": "assistant", "content": ""}
    if "Thought:" in blob or "Action:" in blob:
        # REACT_TEXT.
        if done:
            return {
                "role": "assistant",
                "content": (
                    "Thought: The file is written.\n"
                    "Final Answer: hello.py prints Hello, world! when run."
                ),
            }
        return {
            "role": "assistant",
            "content": (
                "Thought: I need to create the file.\n"
                "Action: fs.write\n"
                'Action Input: {"path": "hello.py", "content": '
                f"{json.dumps(HELLO_SOURCE)}}}"
            ),
        }

    return {"role": "assistant", "content": HELLO_SOURCE}


class _StubServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, model: str) -> None:
        super().__init__(("127.0.0.1", 0), _StubHandler)
        self.model = model
        self.calls: List[Dict[str, Any]] = []


def start_stub(model: str) -> Tuple[_StubServer, str]:
    """A running stub and the base URL to point GitPilot at."""
    server = _StubServer(model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------


def ollama_reachable(base_url: str, timeout: float = 2.0) -> Tuple[bool, str]:
    """Whether something answers ``/api/tags`` at *base_url*."""
    url = base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read() or b"{}")
    except urllib.error.URLError as exc:
        return False, f"{url}: {exc.reason}"
    except (OSError, ValueError) as exc:
        return False, f"{url}: {exc}"
    names = [str(entry.get("name") or "") for entry in (payload.get("models") or [])]
    return True, ", ".join(names) or "no models pulled"


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


class Report:
    """Steps, as they happen, so a failure says which one."""

    def __init__(self) -> None:
        self.steps: List[Tuple[str, bool, str]] = []

    def step(self, name: str, ok: bool, detail: str = "") -> bool:
        self.steps.append((name, ok, detail))
        mark = "ok  " if ok else "FAIL"
        print(f"  {mark} {name}" + (f" — {detail}" if detail else ""), flush=True)
        return ok

    @property
    def ok(self) -> bool:
        return all(ok for _name, ok, _detail in self.steps)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "steps": [
                {"name": name, "ok": ok, "detail": detail}
                for name, ok, detail in self.steps
            ],
        }


def verify(
    *,
    model: str,
    base_url: str,
    real: bool,
    keep: bool = False,
    max_iterations: int = 8,
) -> Report:
    """Run the task and check the result by executing it."""
    import asyncio

    from gitpilot import flags
    from gitpilot.agent.model_profile import resolve_profile
    from gitpilot.agent.providers import build_provider
    from gitpilot.agent.runner import AgentRunner, RunRequest, apply_topology_policy

    report = Report()
    root = Path(tempfile.mkdtemp(prefix="gitpilot-e2e-"))
    workspace = root / "project"
    workspace.mkdir(parents=True)
    (workspace / "README.md").write_text("# e2e\n\nA throwaway project.\n", encoding="utf-8")

    # The engine is flag-gated; a verification runs it as it will ship, not as the
    # defaults happen to be set today.
    for flag in ("agent_loop", "agent_loop_default", "agent_policy",
                 "tool_registry", "model_profiles"):
        flags.set_override(flag, True)

    # Point GitPilot's own settings resolution at the endpoint. Deliberately via
    # the environment rather than by constructing a provider here: the thing under
    # test includes `resolve_endpoint`.
    os.environ["GITPILOT_PROVIDER"] = "ollama"
    os.environ["OLLAMA_BASE_URL"] = base_url
    os.environ["GITPILOT_OLLAMA_MODEL"] = model

    # `_settings` is read from disk once at import, so setting the environment is
    # not enough on its own: a second verification in the same process would build a
    # provider pointing at the first one's port. Found by running the four models as
    # one test rather than four commands.
    from gitpilot.settings import reload_settings

    reload_settings()

    try:
        profile = resolve_profile("ollama", model, cache_path=root / "probe.json")
        report.step(
            "resolved a model profile",
            profile.model == model,
            f"{model} → {profile.dialect.value} dialect, {profile.context_window} ctx",
        )

        try:
            provider = build_provider(model=model)
        except Exception as exc:  # noqa: BLE001
            report.step("built a provider from settings", False, repr(exc))
            return report
        report.step(
            "built a provider from settings",
            True,
            f"{type(provider).__name__} → {base_url}",
        )

        request = apply_topology_policy(RunRequest(
            task=TASK,
            session_id="e2e",
            topology_id="default",
            workspace_root=workspace,
            journal_root=root / "journals",
            session_mode="auto",
            approvals="none",
            max_iterations=max_iterations,
        ), workspace=workspace)
        report.step(
            "applied the default topology's policy",
            bool(request.topology_capabilities),
            f"{len(request.topology_capabilities or {})} capabilities, "
            f"max_iterations={request.max_iterations}",
        )

        started = time.monotonic()
        try:
            result = asyncio.run(
                AgentRunner().run(request, provider=provider, profile=profile),
            )
        except Exception as exc:  # noqa: BLE001 - the failure is the finding
            report.step("the loop completed", False, repr(exc))
            return report
        elapsed = time.monotonic() - started

        report.step(
            "the loop completed",
            result.status in ("completed", "degraded"),
            f"status={result.status} in {elapsed:.1f}s",
        )

        hello = workspace / "hello.py"
        if not report.step("hello.py exists", hello.exists(), str(hello)):
            return report

        # The assertion that matters: not "a file appeared" but "the file works".
        try:
            completed = subprocess.run(   # noqa: S603 - a file this run just wrote
                [sys.executable, "hello.py"],
                cwd=workspace, capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            report.step("hello.py runs", False, "timed out")
            return report

        report.step(
            "hello.py runs",
            completed.returncode == 0,
            f"exit {completed.returncode}" + (
                f", stderr: {completed.stderr.strip()[:200]}" if completed.stderr else ""
            ),
        )
        report.step(
            f"it printed {EXPECTED_OUTPUT!r}",
            EXPECTED_OUTPUT in completed.stdout,
            f"stdout: {completed.stdout.strip()[:200]!r}",
        )

        journal = Path(str(result.context.journal.path))
        lines = journal.read_text(encoding="utf-8").splitlines() if journal.exists() else []
        report.step(
            "the run is journaled",
            len(lines) >= 3,
            f"{len(lines)} lines at {journal.name}",
        )

        kinds = [json.loads(line).get("type") for line in lines if line.strip()]
        report.step(
            "every tool call was authorised before it ran",
            kinds.count("policy_decision") >= kinds.count("tool_result") > 0,
            f"{kinds.count('policy_decision')} decisions, "
            f"{kinds.count('tool_result')} results",
        )

        if real:
            report.step(
                "this was a real model",
                True,
                f"{model} at {base_url}",
            )
        else:
            report.step(
                "this was the local stub, not a model",
                True,
                "the transport, dialect, policy and file write are real; the "
                "model's ability to follow the protocol is not under test",
            )
        return report
    finally:
        flags.clear_all_overrides()
        for key in ("GITPILOT_PROVIDER", "OLLAMA_BASE_URL", "GITPILOT_OLLAMA_MODEL"):
            os.environ.pop(key, None)
        reload_settings()   # leave the process's settings as they were found
        if keep:
            print(f"\n  workspace kept at {workspace}")
        else:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_end_to_end", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model", default="qwen2.5:1.5b", help="the model to drive")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        help="where Ollama is listening",
    )
    parser.add_argument(
        "--real", action="store_true",
        help="require a real model; fail rather than falling back to the stub",
    )
    parser.add_argument(
        "--stub", action="store_true",
        help="use the local stub even if a real Ollama is reachable",
    )
    parser.add_argument("--keep", action="store_true", help="keep the workspace")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--max-iterations", type=int, default=8)
    args = parser.parse_args(argv)

    print(f"GitPilot end-to-end verification — {TASK[:60]}…\n")

    reachable, detail = (False, "") if args.stub else ollama_reachable(args.base_url)
    server: Optional[_StubServer] = None

    if reachable and not args.stub:
        base_url = args.base_url
        print(f"  using the Ollama at {base_url} (models: {detail})\n")
        real = True
    elif args.real:
        print(f"  no Ollama at {args.base_url}: {detail}", file=sys.stderr)
        print(
            "\n  --real was given, so this is a failure rather than a fallback.\n"
            "  Start Ollama and pull the model:\n"
            f"      ollama serve &\n      ollama pull {args.model}\n",
            file=sys.stderr,
        )
        return 2
    else:
        server, base_url = start_stub(args.model)
        print(f"  no Ollama at {args.base_url} ({detail})")
        print(f"  using the local stub at {base_url} — the transport is real, the model is not\n")
        real = False

    try:
        report = verify(
            model=args.model, base_url=base_url, real=real,
            keep=args.keep, max_iterations=args.max_iterations,
        )
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()

    print()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    if report.ok:
        print("PASS — a model wrote hello.py, and hello.py works.")
        if not real:
            print(
                "      Against the stub. Run with --real and a pulled model to "
                "verify the model itself."
            )
        return 0

    failed = [name for name, ok, _detail in report.steps if not ok]
    print(f"FAIL — {len(failed)} step(s): {', '.join(failed)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
