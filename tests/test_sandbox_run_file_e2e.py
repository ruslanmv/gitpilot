"""End-to-end regression suite for "run this file in the sandbox".

Written while debugging a report that GitPilot cannot run a Python file
from chat.  The sandbox backends themselves were fine — every defect was
on the road *to* them, which is why "the sandbox is broken" was so hard
to pin down.  Each test below fails on the pre-fix tree:

1. **Routing.**  ``run nuclear_shell_demo.py`` classified as ``unknown``
   because the execute triggers were a literal-substring table ("run the
   ", "run demo", "run main"...).  A filename the table had not been
   taught by name never reached the EXECUTE short-circuit, so the request
   fell through to the LLM planner, which answered it as a *question* —
   prose telling the user to clone the repo and run it themselves.
2. **Approval.**  The apply path called ``/api/sandbox/run`` with no
   approval token, so the endpoint answered 403 for the very plan the
   user had just approved in chat.
3. **stdin.**  A sandboxed process inherited the server's stdin.  Started
   from a terminal, a script calling ``input()`` blocked on the tty until
   the timeout instead of raising ``EOFError``.
4. **Timeout output.**  On timeout every byte the process had already
   printed was discarded, so a script that printed and then hung showed
   the user nothing at all.
5. **MatrixLab contract.**  ``MatrixLabSandbox`` POSTed ``/repo/run``,
   an endpoint whose request model requires ``repo_url`` — every
   workspace command came back 422.  The native contract is ``POST /run``.

Everything here runs offline: no Docker, no Ollama, no MatrixLab, no
GitHub.  The MatrixLab tests pin the request shape with
``httpx.MockTransport``; ``scripts/sandbox_debug.py`` is the companion
that probes a *live* runner.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from gitpilot.agentic import (
    _classify_lite_intent,
    try_execute_short_circuit,
)
from gitpilot.query_router import classify
from gitpilot.sandbox import (
    MatrixLabSandbox,
    SandboxPolicy,
    SubprocessSandbox,
)
from gitpilot.settings import reload_settings

# The repository that triggered the report: a README and one generated
# demo script, which is also the smallest interesting shape — exactly one
# runnable file, so "run the script" is unambiguous too.
REPO_FILES = ["README.md", "nuclear_shell_demo.py"]


# ----------------------------------------------------------------------
# 1. Routing — "run <file>" has to reach the sandbox, not the LLM
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "goal",
    [
        "run nuclear_shell_demo.py",
        "Run nuclear_shell_demo.py",
        "please run nuclear_shell_demo.py",
        "can you run nuclear_shell_demo.py for me",
        "run nuclear_shell_demo.py and show me the output",
        "execute nuclear_shell_demo.py",
        "python nuclear_shell_demo.py",
        "python3 nuclear_shell_demo.py",
        "run the file nuclear_shell_demo.py",
        "run `nuclear_shell_demo.py`",
    ],
)
def test_run_a_named_file_classifies_as_execute(goal: str) -> None:
    """The regression: any run verb plus any runnable filename."""
    decision = classify(goal, repo_files=REPO_FILES)
    assert decision.intent == "execute", (
        f"{goal!r} routed as {decision.intent!r} — it will be answered as "
        f"prose instead of being run"
    )


@pytest.mark.parametrize(
    "goal,expected_intent",
    [
        ("what does nuclear_shell_demo.py do", "info"),
        ("explain README.md", "info"),
        ("delete nuclear_shell_demo.py", "delete"),
        ("fix the bug in nuclear_shell_demo.py", "fix"),
        ("create a simple python script about what README.md says", "create"),
        ("where is nuclear_shell_demo.py", "find"),
    ],
)
def test_non_run_goals_are_not_hijacked(goal: str, expected_intent: str) -> None:
    """The execute regex must not swallow talking *about* a runnable file."""
    assert classify(goal, repo_files=REPO_FILES).intent == expected_intent


def test_execute_short_circuit_builds_a_runnable_plan() -> None:
    goal = "run nuclear_shell_demo.py"
    decision = classify(goal, repo_files=REPO_FILES)
    plan = try_execute_short_circuit(
        goal=goal,
        intent=decision.intent,
        target_files=decision.target_files,
        repo_files=REPO_FILES,
    )
    assert plan is not None, "no deterministic plan — the LLM would answer instead"
    step = plan.steps[0]
    assert [(f.path, f.action) for f in step.files] == [
        ("nuclear_shell_demo.py", "EXECUTE")
    ]
    # The chat UI renders the approval card from this dict.
    assert plan.execution_plan is not None
    assert plan.execution_plan["command"] == ["python", "nuclear_shell_demo.py"]


def test_lite_mode_classifies_a_run_request_as_execute() -> None:
    """Lite Mode (small local models) has to agree with the router.

    Its own classifier only knew 'question' and 'action', and "run x.py"
    matched neither — so it took the question branch and explained how to
    run the file rather than running it.
    """
    assert _classify_lite_intent("run nuclear_shell_demo.py") == "execute"
    assert _classify_lite_intent("what does nuclear_shell_demo.py do") == "question"
    assert _classify_lite_intent("create demo.py") == "action"


# ----------------------------------------------------------------------
# 2. Approval — the apply path must be able to reach the endpoint
# ----------------------------------------------------------------------

@pytest.fixture()
def isolated_settings(tmp_path, monkeypatch) -> Iterator[None]:
    """Point settings at a tmp dir and clear sandbox env overrides."""
    from gitpilot import settings as settings_module

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings_module, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(settings_module, "CONFIG_FILE", cfg_dir / "settings.json")
    monkeypatch.setenv("GITPILOT_CONFIG_DIR", str(cfg_dir))
    for name in (
        "GITPILOT_SANDBOX",
        "GITPILOT_MATRIXLAB_URL",
        "GITPILOT_MATRIXLAB_TOKEN",
        "GITPILOT_MATRIXLAB_IMAGE",
    ):
        monkeypatch.delenv(name, raising=False)
    reload_settings()
    yield
    reload_settings()


@pytest.fixture()
def client(isolated_settings) -> Iterator[TestClient]:
    from gitpilot.api import app

    with TestClient(app) as c:
        yield c


def test_apply_path_run_is_approved(isolated_settings) -> None:
    """The EXECUTE branch of apply_plan must not 403 on its own request.

    Reproduces the call ``agentic.apply_plan`` makes: in-process, no HTTP
    client, no user-supplied token — it mints one for the plan the user
    already approved in chat.
    """
    from gitpilot.sandbox_api import SandboxRunRequest, api_sandbox_run
    from gitpilot.sandbox_routing import _mint_internal_approval

    code = "print('executed by the apply path')"
    token = _mint_internal_approval("python", code)
    assert token, "no approval token minted — the run will be refused"

    result = asyncio.run(
        api_sandbox_run(
            SandboxRunRequest(
                language="python", code=code, approval_token=token,
            )
        )
    )
    assert result.exit_code == 0
    assert "executed by the apply path" in result.stdout


def test_unapproved_run_is_still_refused(client: TestClient) -> None:
    """The fix must not become a bypass: no token, no run."""
    resp = client.post(
        "/api/sandbox/run",
        json={"language": "python", "code": "print('unapproved')"},
    )
    assert resp.status_code == 403


def test_minted_token_is_single_use(isolated_settings) -> None:
    from gitpilot.sandbox_api import SandboxRunRequest, api_sandbox_run
    from gitpilot.sandbox_routing import _mint_internal_approval
    from fastapi import HTTPException

    code = "print('once')"
    token = _mint_internal_approval("python", code)
    req = SandboxRunRequest(language="python", code=code, approval_token=token)
    assert asyncio.run(api_sandbox_run(req)).exit_code == 0

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            api_sandbox_run(
                SandboxRunRequest(
                    language="python", code=code, approval_token=token,
                )
            )
        )
    assert excinfo.value.status_code == 403


# ----------------------------------------------------------------------
# 3 + 4. The local subprocess backend itself
# ----------------------------------------------------------------------

def test_local_sandbox_runs_a_python_file(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("print('hello from the sandbox')\n")
    sb = SubprocessSandbox(SandboxPolicy(workspace=tmp_path, timeout_sec=30))
    result = asyncio.run(sb.run("python3 demo.py", timeout=30))
    assert result.exit_code == 0
    assert "hello from the sandbox" in result.stdout
    assert result.timed_out is False


def test_interactive_script_fails_fast_instead_of_hanging(tmp_path: Path) -> None:
    """``input()`` must hit EOF immediately — stdin is never inherited.

    The generated demo that prompted this investigation opens with
    ``input("Press Enter to begin...")``.  With the server's stdin
    inherited, that blocked for the full timeout and returned nothing.
    """
    (tmp_path / "ask.py").write_text(
        "print('before', flush=True)\n"
        "value = input('prompt: ')\n"
        "print('after', value)\n"
    )
    sb = SubprocessSandbox(SandboxPolicy(workspace=tmp_path, timeout_sec=20))
    started = time.monotonic()
    result = asyncio.run(sb.run("python3 ask.py", timeout=20))
    elapsed = time.monotonic() - started

    assert result.timed_out is False, "the run blocked on stdin"
    assert elapsed < 10, f"took {elapsed:.1f}s — it waited on stdin"
    assert result.exit_code != 0
    assert "EOFError" in result.stderr
    # And the user still sees what ran before the prompt.
    assert "before" in result.stdout


def test_output_printed_before_a_timeout_is_preserved(tmp_path: Path) -> None:
    """A script that prints and then hangs must not report empty output."""
    (tmp_path / "slow.py").write_text(
        "import time\n"
        "print('partial output kept', flush=True)\n"
        "time.sleep(60)\n"
    )
    sb = SubprocessSandbox(SandboxPolicy(workspace=tmp_path, timeout_sec=3))
    result = asyncio.run(sb.run("python3 slow.py", timeout=3))

    assert result.timed_out is True
    assert "partial output kept" in result.stdout, (
        "output produced before the timeout was discarded — the user sees "
        "an empty result and no clue where the script stopped"
    )


def test_timeout_kills_the_whole_process_tree(tmp_path: Path) -> None:
    """A timed-out run must leave nothing running on the host.

    Commands go through ``sh -c``; on a shell that forks rather than
    execs, killing only the shell left the real program alive — burning
    CPU indefinitely and holding the pipes open, so the sandbox could not
    even see that the run had finished.
    """
    import subprocess

    marker = "gitpilot_runaway_probe"
    (tmp_path / "runaway.py").write_text(
        f"import time\nprint('started', flush=True)\ntime.sleep(300)  # {marker}\n"
    )
    sb = SubprocessSandbox(SandboxPolicy(workspace=tmp_path, timeout_sec=2))
    result = asyncio.run(sb.run("python3 runaway.py", timeout=2))
    assert result.timed_out is True

    survivors = subprocess.run(
        ["pgrep", "-f", marker], capture_output=True, text=True,
    )
    assert survivors.returncode != 0, (
        f"processes survived the sandbox timeout: {survivors.stdout!r}"
    )


def test_timeout_returns_promptly(tmp_path: Path) -> None:
    """Killing the tree closes the pipes, so no reap budget is burned."""
    (tmp_path / "hang.py").write_text("import time\ntime.sleep(300)\n")
    sb = SubprocessSandbox(SandboxPolicy(workspace=tmp_path, timeout_sec=2))
    started = time.monotonic()
    result = asyncio.run(sb.run("python3 hang.py", timeout=2))
    elapsed = time.monotonic() - started
    assert result.timed_out is True
    assert elapsed < 6, f"took {elapsed:.1f}s for a 2s timeout"


def test_a_chatty_script_is_truncated_not_timed_out(tmp_path: Path) -> None:
    """Output past the cap is discarded, not left to block the process.

    A reader that stops at the cap leaves the process blocked on a full
    pipe until the timeout — turning "printed a lot" into "failed".
    """
    (tmp_path / "loud.py").write_text(
        "for i in range(200_000):\n    print(f'line {i} ' + 'x' * 40)\n"
    )
    policy = SandboxPolicy(workspace=tmp_path, timeout_sec=60, max_output_bytes=64_000)
    result = asyncio.run(SubprocessSandbox(policy).run("python3 loud.py", timeout=60))

    assert result.timed_out is False, "the sandbox stopped reading and stalled the process"
    assert result.exit_code == 0
    assert result.truncated is True
    assert len(result.stdout) <= 64_000
    assert "line 0 " in result.stdout


def test_stdin_is_delivered_when_the_caller_supplies_it(tmp_path: Path) -> None:
    """DEVNULL by default must not break callers that *do* pass stdin."""
    (tmp_path / "echo.py").write_text(
        "import sys\nprint('got:', sys.stdin.read().strip())\n"
    )
    sb = SubprocessSandbox(SandboxPolicy(workspace=tmp_path, timeout_sec=20))
    result = asyncio.run(
        sb.run("python3 echo.py", timeout=20, stdin="from the caller\n")
    )
    assert result.exit_code == 0
    assert "got: from the caller" in result.stdout


# ----------------------------------------------------------------------
# 5. HTTP surface — the path the Run button and the agent share
# ----------------------------------------------------------------------

def _approved_run(client: TestClient, language: str, code: str, **extra):
    plan = client.post(
        "/api/sandbox/plan",
        json={"language": language, "code": code, "source": "code_block"},
    )
    assert plan.status_code == 200, plan.text
    approval = client.post(
        "/api/sandbox/approve", json={"plan_id": plan.json()["plan"]["plan_id"]},
    )
    assert approval.status_code == 200, approval.text
    return client.post(
        "/api/sandbox/run",
        json={
            "language": language,
            "code": code,
            "approval_token": approval.json()["approval_token"],
            **extra,
        },
    )


def test_http_run_of_a_generated_demo(client: TestClient) -> None:
    """The shape of the file the agent generated, minus the input() calls."""
    code = (
        "def capacity(level):\n"
        "    return {1: 2, 2: 8, 3: 18}.get(level, 32)\n"
        "for level in (1, 2, 3):\n"
        "    print(f'Level {level}: {capacity(level)}')\n"
    )
    resp = _approved_run(client, "python", code)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["backend"] == "subprocess"
    assert data["exit_code"] == 0
    assert "Level 1: 2" in data["stdout"]
    assert "Level 3: 18" in data["stdout"]


def test_http_run_reports_a_traceback_rather_than_swallowing_it(
    client: TestClient,
) -> None:
    resp = _approved_run(client, "python", "raise SystemExit('boom')")
    assert resp.status_code == 200
    data = resp.json()
    assert data["exit_code"] != 0
    assert "boom" in data["stderr"]


# ----------------------------------------------------------------------
# 6. MatrixLab — same contract, container isolation
# ----------------------------------------------------------------------

def _mock_matrixlab(capture: dict, *, status: int = 200, payload: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        capture["url"] = str(request.url)
        capture["headers"] = dict(request.headers)
        capture["body"] = json.loads(request.content or b"{}")
        return httpx.Response(
            status,
            json=payload
            or {
                "sandbox_id": "sbx-test",
                "exit_code": 0,
                "stdout": "hello from matrixlab\n",
                "stderr": "",
                "duration_ms": 42,
                "artifacts": [],
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_matrixlab_uses_the_native_run_contract(tmp_path: Path) -> None:
    """``POST /run`` with the native body — not ``/repo/run``.

    ``/repo/run`` clones a git repository and its model requires
    ``repo_url``; sending a workspace command there returned 422 on every
    call, which surfaced in the UI as "MatrixLab is installed, but
    GitPilot cannot connect to the runner".
    """
    (tmp_path / "demo.py").write_text("print('hello from matrixlab')\n")
    capture: dict = {}
    sb = MatrixLabSandbox(
        SandboxPolicy(workspace=tmp_path, timeout_sec=30),
        base_url="http://localhost:8765",
        http_client=_mock_matrixlab(capture),
    )
    result = asyncio.run(sb.run("python demo.py", timeout=30))

    assert capture["url"] == "http://localhost:8765/run"
    body = capture["body"]
    # Exactly the fields MatrixLab's NativeRunRequest declares.
    assert set(body) <= {
        "cmd", "cwd", "workspace", "env", "timeout", "image",
        "allow_network", "stdin", "metadata",
        "cpu_limit", "mem_limit_mb", "pids_limit",
    }
    assert body["cmd"] == "python demo.py"
    # cwd is a path *inside the container*, never a host path.
    assert body["cwd"] == "."
    assert "mount_workspace" not in body, "the Runner has no host mount"
    # ``image`` is a non-nullable string on the Runner: omit, don't null.
    assert body.get("image") is not True
    assert "image" not in body or isinstance(body["image"], str)
    # The workspace travels as a zip.
    assert body["workspace"]["type"] == "zip"
    assert body["workspace"]["zip_base64"]

    assert result.backend == "matrixlab"
    assert result.exit_code == 0
    assert "hello from matrixlab" in result.stdout
    assert result.sandbox_id == "sbx-test"


def test_matrixlab_sends_the_bearer_token(tmp_path: Path) -> None:
    capture: dict = {}
    sb = MatrixLabSandbox(
        SandboxPolicy(workspace=tmp_path, timeout_sec=30),
        base_url="http://localhost:8765",
        token="s3cret",
        http_client=_mock_matrixlab(capture),
    )
    asyncio.run(sb.run("echo hi", timeout=30))
    assert capture["headers"]["authorization"] == "Bearer s3cret"


def test_matrixlab_error_body_reaches_the_operator(tmp_path: Path) -> None:
    """A 422 must name the field, not just the status code."""
    from gitpilot.sandbox import SandboxRunError

    capture: dict = {}
    sb = MatrixLabSandbox(
        SandboxPolicy(workspace=tmp_path, timeout_sec=30),
        base_url="http://localhost:8765",
        http_client=_mock_matrixlab(
            capture,
            status=422,
            payload={"detail": [{"loc": ["body", "repo_url"], "msg": "Field required"}]},
        ),
    )
    with pytest.raises(SandboxRunError) as excinfo:
        asyncio.run(sb.run("python demo.py", timeout=30))
    assert "repo_url" in str(excinfo.value)


def test_matrixlab_health_reports_a_runner_that_cannot_execute() -> None:
    """200 from a Runner whose Docker is down is *not* healthy.

    Reporting it as healthy is what produced a green pill in Settings
    followed by a failure on every run.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": False,
                "service": "matrixlab-runner",
                "docker": {"ok": False, "error": "cannot connect to docker daemon"},
            },
        )

    sb = MatrixLabSandbox(
        SandboxPolicy(),
        base_url="http://localhost:8765",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    health = asyncio.run(sb.health())
    assert health["ok"] is False
    assert "docker" in health["error"].lower()


def test_matrixlab_health_ok_when_the_runner_is_ready() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "docker": {"ok": True}})

    sb = MatrixLabSandbox(
        SandboxPolicy(),
        base_url="http://localhost:8765",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    health = asyncio.run(sb.health())
    assert health["ok"] is True


def test_lifecycle_running_probe_honours_the_runners_verdict(
    isolated_settings, monkeypatch,
) -> None:
    """"Running" must mean "can execute", not "answered with 200"."""
    from gitpilot import sandbox_api

    bodies = [{"ok": False, "docker": {"ok": False}}, {"ok": True, "docker": {"ok": True}}]
    seen: list[dict] = []

    class _Client:
        def __init__(self, *a, **kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        async def get(self, url):
            body = bodies[len(seen)]
            seen.append(body)
            return httpx.Response(200, json=body)

    monkeypatch.setattr(sandbox_api.httpx, "AsyncClient", _Client)
    assert asyncio.run(sandbox_api._matrixlab_running()) is False
    assert asyncio.run(sandbox_api._matrixlab_running()) is True


def test_both_backends_produce_the_same_envelope(tmp_path: Path) -> None:
    """Local and MatrixLab must be swappable without the caller noticing."""
    (tmp_path / "demo.py").write_text("print('parity')\n")

    local = asyncio.run(
        SubprocessSandbox(SandboxPolicy(workspace=tmp_path, timeout_sec=30)).run(
            "python3 demo.py", timeout=30,
        )
    )
    remote = asyncio.run(
        MatrixLabSandbox(
            SandboxPolicy(workspace=tmp_path, timeout_sec=30),
            base_url="http://localhost:8765",
            http_client=_mock_matrixlab(
                {},
                payload={
                    "sandbox_id": "sbx-parity",
                    "exit_code": 0,
                    "stdout": "parity\n",
                    "stderr": "",
                    "duration_ms": 5,
                    "artifacts": [],
                },
            ),
        ).run("python demo.py", timeout=30)
    )

    for field in ("exit_code", "stdout", "stderr", "timed_out", "truncated"):
        assert getattr(local, field) == getattr(remote, field), field
    assert local.backend == "subprocess"
    assert remote.backend == "matrixlab"


# ----------------------------------------------------------------------
# 7. Internal URL — the agent's own tool has to find GitPilot
# ----------------------------------------------------------------------

def test_internal_sandbox_url_defaults_to_gitpilots_own_port(monkeypatch) -> None:
    """``terminal.run_snippet`` POSTs to GitPilot, not to MatrixLab.

    The fallback used to be :8765 — MatrixLab's host port — so with no
    GITPILOT_PORT set the agent's snippets were addressed either to a
    Runner with no /api/sandbox/run or to nothing at all.
    """
    from gitpilot.cli import DEFAULT_PORT

    monkeypatch.delenv("GITPILOT_PORT", raising=False)
    monkeypatch.delenv("GITPILOT_INTERNAL_URL", raising=False)

    captured: dict = {}

    class _StubClient:
        def __init__(self, *a, **kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        async def post(self, url, json=None):
            captured["url"] = url
            return httpx.Response(
                200,
                json={
                    "backend": "subprocess", "exit_code": 0, "stdout": "ok\n",
                    "stderr": "", "duration_ms": 1,
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", _StubClient)
    from gitpilot.sandbox_routing import run_snippet

    asyncio.run(run_snippet("python", "print('ok')", 30))
    assert captured["url"] == f"http://127.0.0.1:{DEFAULT_PORT}/api/sandbox/run"
