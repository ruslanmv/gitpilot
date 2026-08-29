#!/usr/bin/env python3
"""Walk the whole "run a file in the sandbox" path and report where it breaks.

``make sandbox-debug`` (or ``python scripts/sandbox_debug.py``).  Unlike
``tests/test_sandbox_run_file_e2e.py``, which pins the contract offline,
this probes the machine you are actually on: your settings, your Python,
your MatrixLab Runner.

Six stages, each printed with a verdict and — when it fails — the command
that fixes it:

  1. settings      which backend is selected, and what is shadowing it
  2. local         SubprocessSandbox actually executes a Python file
  3. interactive   a script that reads stdin fails fast instead of hanging
  4. approval      the apply path can mint and spend an approval token
  5. routing       "run <file>.py" reaches the deterministic EXECUTE plan
  6. matrixlab     the Runner is reachable, healthy, and honours /run

Exit code is 0 when every applicable stage passed, 1 otherwise, so it can
gate a CI job.  Nothing here writes to your settings.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    ("\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m")
    if sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    else ("", "", "", "", "", "")
)

FAILURES: list[str] = []


def head(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}")


def ok(msg: str, detail: str = "") -> None:
    print(f"  {GREEN}PASS{RESET}  {msg}" + (f"\n        {DIM}{detail}{RESET}" if detail else ""))


def bad(msg: str, fix: str = "") -> None:
    FAILURES.append(msg)
    print(f"  {RED}FAIL{RESET}  {msg}" + (f"\n        {YELLOW}fix:{RESET} {fix}" if fix else ""))


def skip(msg: str, detail: str = "") -> None:
    print(f"  {DIM}SKIP{RESET}  {msg}" + (f"\n        {DIM}{detail}{RESET}" if detail else ""))


def info(key: str, value: Any) -> None:
    print(f"        {DIM}{key}:{RESET} {value}")


# ----------------------------------------------------------------------
# 1. Settings
# ----------------------------------------------------------------------

def stage_settings() -> Any:
    head("1. Settings — which sandbox is selected")
    from gitpilot.settings import get_settings

    cfg = get_settings().sandbox
    info("backend", cfg.backend)
    info("matrixlab_url", cfg.matrixlab_url)
    info("matrixlab_image", cfg.matrixlab_image or "(runner default)")
    info("allow_network", cfg.allow_network)
    info("timeout_sec", cfg.timeout_sec)
    info("has_token", bool(cfg.matrixlab_token))

    shadowing = [
        name
        for name in (
            "GITPILOT_SANDBOX",
            "GITPILOT_MATRIXLAB_URL",
            "GITPILOT_MATRIXLAB_TOKEN",
            "GITPILOT_MATRIXLAB_IMAGE",
        )
        if os.environ.get(name)
    ]
    if shadowing:
        # Not a failure — but it is the usual reason a UI selection
        # appears to be ignored.
        skip(
            f"environment overrides in effect: {', '.join(shadowing)}",
            "these win over Settings → Sandbox runtime",
        )
    else:
        ok("no environment override — the persisted choice is what runs")
    return cfg


# ----------------------------------------------------------------------
# 2 + 3. The local backend
# ----------------------------------------------------------------------

def stage_local() -> None:
    head("2. Local sandbox — execute a Python file")
    from gitpilot.sandbox import SandboxPolicy, SubprocessSandbox

    with tempfile.TemporaryDirectory(prefix="gitpilot-debug-") as tmp:
        workspace = Path(tmp)
        (workspace / "demo.py").write_text(
            "import sys\nprint('hello from the sandbox')\n"
            "print('python', sys.version.split()[0])\n"
        )
        sb = SubprocessSandbox(SandboxPolicy(workspace=workspace, timeout_sec=30))
        result = asyncio.run(sb.run("python3 demo.py", timeout=30))

        if result.exit_code == 0 and "hello from the sandbox" in result.stdout:
            ok("python3 demo.py ran", result.stdout.strip().replace("\n", " | "))
        else:
            bad(
                f"the local sandbox could not run a Python file "
                f"(exit {result.exit_code})",
                "check that `python3` is on PATH for the user running GitPilot; "
                f"stderr: {result.stderr.strip()[:200]}",
            )

    head("3. Interactive script — must fail fast, not hang")
    with tempfile.TemporaryDirectory(prefix="gitpilot-debug-") as tmp:
        workspace = Path(tmp)
        (workspace / "ask.py").write_text(
            "print('before the prompt', flush=True)\n"
            "value = input('Press Enter to begin...')\n"
            "print('after', value)\n"
        )
        sb = SubprocessSandbox(SandboxPolicy(workspace=workspace, timeout_sec=15))
        started = time.monotonic()
        result = asyncio.run(sb.run("python3 ask.py", timeout=15))
        elapsed = time.monotonic() - started

        if result.timed_out or elapsed > 10:
            bad(
                f"a script calling input() blocked for {elapsed:.1f}s",
                "the sandbox is handing the process the server's stdin; "
                "expected DEVNULL so input() raises EOFError immediately",
            )
        elif "before the prompt" not in result.stdout:
            bad(
                "output printed before the prompt was lost",
                "the run's stdout is being discarded on failure",
            )
        else:
            ok(
                f"EOFError after {elapsed:.2f}s, output before the prompt kept",
                "scripts written for a terminal report an error instead of hanging",
            )


# ----------------------------------------------------------------------
# 4. Approval
# ----------------------------------------------------------------------

def stage_approval() -> None:
    head("4. Approval — the apply path can spend a token")
    from fastapi import HTTPException

    from gitpilot.sandbox_api import SandboxRunRequest, api_sandbox_run
    from gitpilot.sandbox_routing import _mint_internal_approval

    code = "print('approved run')"
    token = _mint_internal_approval("python", code)
    if not token:
        bad(
            "could not mint an approval token",
            "the sandbox token store is unavailable; /api/sandbox/run will 403",
        )
        return
    try:
        result = asyncio.run(
            api_sandbox_run(
                SandboxRunRequest(language="python", code=code, approval_token=token)
            )
        )
    except HTTPException as exc:
        bad(
            f"the approved run was refused: {exc.status_code} {exc.detail}",
            "apply_plan's EXECUTE branch must send an approval token",
        )
        return
    if result.exit_code == 0 and "approved run" in result.stdout:
        ok(f"run executed on {result.backend} with a minted approval")
    else:
        bad(f"approved run failed: exit {result.exit_code} {result.stderr[:200]}")

    # And the gate still holds without one.
    try:
        asyncio.run(
            api_sandbox_run(SandboxRunRequest(language="python", code="print('nope')"))
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            ok("an unapproved run is still refused (403)")
        else:
            bad(f"unapproved run returned {exc.status_code}, expected 403")
    else:
        bad(
            "an unapproved run executed",
            "the approval gate is off — check GITPILOT_SANDBOX_REQUIRE_APPROVAL",
        )


# ----------------------------------------------------------------------
# 5. Routing
# ----------------------------------------------------------------------

def stage_routing() -> None:
    head("5. Routing — 'run <file>' has to become an EXECUTE plan")
    from gitpilot.agentic import _classify_lite_intent, try_execute_short_circuit
    from gitpilot.query_router import classify

    repo_files = ["README.md", "nuclear_shell_demo.py"]
    goals = [
        "run nuclear_shell_demo.py",
        "please run nuclear_shell_demo.py",
        "python nuclear_shell_demo.py",
        "execute nuclear_shell_demo.py",
        "run the script",
    ]
    broken = []
    for goal in goals:
        decision = classify(goal, repo_files=repo_files)
        plan = try_execute_short_circuit(
            goal=goal,
            intent=decision.intent,
            target_files=decision.target_files,
            repo_files=repo_files,
        )
        state = "EXECUTE" if plan else f"intent={decision.intent} → LLM"
        print(f"        {goal!r:45} {state}")
        if plan is None:
            broken.append(goal)

    if broken:
        bad(
            f"{len(broken)} run request(s) never reach the sandbox: {broken}",
            "they fall through to the LLM planner, which answers them as prose",
        )
    else:
        ok("every run phrasing produced a deterministic EXECUTE plan")

    if _classify_lite_intent("run nuclear_shell_demo.py") == "execute":
        ok("Lite Mode (small local models) agrees it is an execute request")
    else:
        bad(
            "Lite Mode classifies 'run <file>' as something other than execute",
            "small models will answer with instructions instead of running it",
        )

    # Non-run goals must be left alone.
    hijacked = [
        goal
        for goal, expected in (
            ("what does nuclear_shell_demo.py do", "info"),
            ("delete nuclear_shell_demo.py", "delete"),
            ("fix the bug in nuclear_shell_demo.py", "fix"),
        )
        if classify(goal, repo_files=repo_files).intent != expected
    ]
    if hijacked:
        bad(f"non-run goals misrouted: {hijacked}")
    else:
        ok("talking *about* a runnable file is not treated as a run request")


# ----------------------------------------------------------------------
# 6. MatrixLab
# ----------------------------------------------------------------------

def stage_matrixlab(cfg: Any, force: bool) -> None:
    head("6. MatrixLab Runner — container isolation")
    import httpx

    from gitpilot.sandbox import MatrixLabSandbox, SandboxPolicy

    url = (cfg.matrixlab_url or "http://localhost:8765").rstrip("/")
    selected = (cfg.backend or "").lower() == "matrixlab"
    if not selected and not force:
        skip(
            f"backend is {cfg.backend!r}, not matrixlab",
            f"pass --matrixlab to probe {url} anyway",
        )
        return

    info("runner url", url)

    async def _probe_health() -> dict:
        # Probe and close in one loop: the client is lazily built on first
        # use and must not be closed from a different event loop.
        sb = MatrixLabSandbox(
            SandboxPolicy(timeout_sec=30),
            base_url=url,
            token=cfg.matrixlab_token or None,
        )
        try:
            return await sb.health()
        finally:
            await sb.aclose()

    health = asyncio.run(_probe_health())

    if not health.get("ok"):
        error = str(health.get("error", ""))
        if "docker" in error.lower():
            bad(
                "the Runner answers but cannot execute (its Docker is down)",
                "start Docker on the Runner host, then re-check "
                "Settings → Sandbox runtime",
            )
        else:
            bad(
                f"the Runner is not reachable at {url}: {error[:200]}",
                "start it with `make install-matrixlab` (or check the port: "
                "MatrixLab's compose maps host 8765 → container 8000, and "
                "GitPilot's own backend is on 8000) — `make fix-matrixlab-url` "
                "detects the right one",
            )
        return
    ok("Runner is healthy", json.dumps(health.get("remote", {}))[:200])

    # Capabilities tell us whether this Runner speaks the contract we use.
    try:
        with httpx.Client(timeout=10) as client:
            headers = (
                {"Authorization": f"Bearer {cfg.matrixlab_token}"}
                if cfg.matrixlab_token
                else {}
            )
            caps = client.get(f"{url}/capabilities", headers=headers).json()
        endpoints = caps.get("endpoints", [])
        missing = [e for e in ("/run", "/code/run") if e not in endpoints]
        if missing:
            bad(
                f"the Runner does not advertise {missing}",
                "GitPilot needs POST /run (file + workspace commands) and "
                "POST /code/run (chat snippets); upgrade the Runner image",
            )
        else:
            ok("Runner advertises /run and /code/run")
    except Exception as exc:  # noqa: BLE001
        skip(f"could not read /capabilities: {exc}")

    # A real snippet through the same endpoint the chat Run button uses.
    from gitpilot.sandbox_api import SandboxRunRequest, api_sandbox_run
    from gitpilot.sandbox_routing import _mint_internal_approval
    from fastapi import HTTPException

    code = "print('hello from matrixlab')"
    token = _mint_internal_approval("python", code)
    try:
        result = asyncio.run(
            api_sandbox_run(
                SandboxRunRequest(
                    language="python", code=code, approval_token=token or None,
                )
            )
        )
    except HTTPException as exc:
        bad(f"POST /code/run failed: {exc.status_code} {str(exc.detail)[:300]}")
        return
    if result.exit_code == 0 and "hello from matrixlab" in result.stdout:
        ok(
            f"snippet executed in a container (sandbox_id={result.sandbox_id})",
            f"{result.duration_ms} ms",
        )
    else:
        bad(
            f"the Runner ran the snippet but returned exit {result.exit_code}",
            result.stderr[:300],
        )

    # And a workspace command, which is the contract that was broken.
    with tempfile.TemporaryDirectory(prefix="gitpilot-debug-ml-") as tmp:
        workspace = Path(tmp)
        (workspace / "demo.py").write_text("print('workspace command ok')\n")
        async def _workspace_command():
            sb = MatrixLabSandbox(
                SandboxPolicy(workspace=workspace, timeout_sec=60),
                base_url=url,
                token=cfg.matrixlab_token or None,
            )
            try:
                return await sb.run("python demo.py", timeout=60)
            finally:
                await sb.aclose()

        try:
            result2 = asyncio.run(_workspace_command())
        except Exception as exc:  # noqa: BLE001
            bad(
                f"POST /run (workspace command) failed: {str(exc)[:300]}",
                "GitPilot ships the workspace as a zip to the native /run "
                "contract; a 422 here means the Runner expects a different shape",
            )
            return
        if result2.exit_code == 0 and "workspace command ok" in result2.stdout:
            ok("workspace command ran inside the container")
        else:
            bad(
                f"workspace command returned exit {result2.exit_code}",
                result2.stderr[:300],
            )


# ----------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--matrixlab",
        action="store_true",
        help="probe the MatrixLab Runner even when it is not the selected backend",
    )
    parser.add_argument(
        "--skip-matrixlab", action="store_true", help="skip stage 6 entirely",
    )
    args = parser.parse_args(argv)

    print(f"{BOLD}GitPilot sandbox diagnostics{RESET}")
    print(f"{DIM}python {sys.version.split()[0]} · {REPO_ROOT}{RESET}")

    cfg = stage_settings()
    stage_local()
    stage_approval()
    stage_routing()
    if not args.skip_matrixlab:
        stage_matrixlab(cfg, force=args.matrixlab)

    print()
    if FAILURES:
        print(f"{RED}{len(FAILURES)} check(s) failed:{RESET}")
        for failure in FAILURES:
            print(f"  · {failure}")
        return 1
    print(f"{GREEN}All checks passed — the sandbox can run code.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
