"""Sandbox routing extracted from local_tools — Batch V4-A4.

Covers the paths the tool-level tests cannot reach cheaply: the HTTP snippet
surface, and the three ways a backend can fail.  Each failure mode has to come
back as a readable string rather than an exception, because both callers (the
CrewAI tool and the registry tool) hand this text straight to a model.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from gitpilot import sandbox_routing as routing


def _run(coro):
    return asyncio.run(coro)


class TestTimeoutCoercion:
    @pytest.mark.parametrize("value,expected", [
        (30, 30),
        ("45", 45),
        (0, 120),          # nonsense → default
        (-5, 120),
        ("abc", 120),
        (None, 120),
        (99999, 600),      # clamped
    ])
    def test_clamps_into_range(self, value, expected):
        assert routing.coerce_timeout(value) == expected


class TestOutputRendering:
    class _Result:
        backend = "subprocess"
        exit_code = 0
        stdout = "out"
        stderr = ""
        duration_ms = 12
        timed_out = False
        truncated = False
        sandbox_id = None

    def test_names_the_backend_that_actually_ran(self):
        rendered = routing.format_sandbox_output(self._Result(), "echo hi")

        assert rendered.startswith("Sandbox: local subprocess\nCommand: echo hi\nExit code: 0")
        assert "--- stdout ---\nout" in rendered

    def test_unknown_backend_is_passed_through_verbatim(self):
        result = self._Result()
        result.backend = "some-future-runner"

        assert "Sandbox: some-future-runner" in routing.format_sandbox_output(result, "x")

    def test_warnings_are_surfaced(self):
        result = self._Result()
        result.timed_out = True
        result.truncated = True
        result.sandbox_id = "sb-1"

        rendered = routing.format_sandbox_output(result, "x")

        assert "WARNING: Command timed out" in rendered
        assert "WARNING: Output was truncated" in rendered
        assert "sandbox_id: sb-1" in rendered


class TestBackendFailures:
    """Every failure is a string, never an exception reaching the model."""

    def _patch_backend(self, monkeypatch, exception):
        from gitpilot import sandbox as sandbox_mod

        class _Failing(sandbox_mod.SubprocessSandbox):
            async def run(self, *args, **kwargs):
                raise exception

        monkeypatch.setattr(sandbox_mod, "SubprocessSandbox", _Failing)

    def test_unreachable_backend(self, monkeypatch, tmp_path):
        from gitpilot.sandbox import SandboxUnavailableError

        self._patch_backend(monkeypatch, SandboxUnavailableError("connection refused"))
        out = _run(routing.run_command_in_workspace("echo hi", 10, tmp_path))

        assert out.startswith("Error: sandbox backend 'subprocess' unreachable:")
        assert "connection refused" in out

    def test_backend_reported_error(self, monkeypatch, tmp_path):
        from gitpilot.sandbox import SandboxRunError

        self._patch_backend(monkeypatch, SandboxRunError("runner exploded"))
        out = _run(routing.run_command_in_workspace("echo hi", 10, tmp_path))

        assert out.startswith("Error: sandbox backend 'subprocess' reported an error:")

    def test_policy_refusal(self, monkeypatch, tmp_path):
        self._patch_backend(monkeypatch, PermissionError("command blocked"))
        out = _run(routing.run_command_in_workspace("mkfs", 10, tmp_path))

        assert out.startswith("Permission denied by sandbox policy:")

    def test_import_failure_raises_fallback_not_a_string(self, monkeypatch, tmp_path):
        """The one case a caller may legitimately branch on."""
        import builtins

        real_import = builtins.__import__

        def fail_on_sandbox(name, *args, **kwargs):
            if name.endswith("sandbox") or name == "gitpilot.sandbox":
                raise ImportError("no httpx in this runtime")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fail_on_sandbox)

        with pytest.raises(routing.SandboxFallback):
            _run(routing.run_command_in_workspace("echo hi", 10, tmp_path))


class TestSnippetSurface:
    """The snippet path goes through GitPilot's own /api/sandbox/run."""

    def _patch_post(self, monkeypatch, handler):
        class _Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, json=None):
                return handler(url, json)

        monkeypatch.setattr(httpx, "AsyncClient", _Client)

    def test_success_renders_like_a_command(self, monkeypatch):
        captured: dict = {}

        def handler(url, json):
            captured.update(url=url, body=json)
            return httpx.Response(
                200,
                json={"backend": "matrixlab", "exit_code": 0, "stdout": "42\n", "stderr": ""},
            )

        self._patch_post(monkeypatch, handler)
        out = _run(routing.run_snippet("python", "print(42)", 30))

        assert captured["url"].endswith("/api/sandbox/run")
        body = captured["body"]
        assert {k: v for k, v in body.items() if k != "approval_token"} == {
            "language": "python", "code": "print(42)", "timeout_sec": 30,
        }
        # Batch V4-D6 (F7): /api/sandbox/run refuses an unapproved run, and this
        # caller is not exempt — the authorization happened in the policy engine,
        # so the approval is recorded rather than bypassed.
        assert body["approval_token"], "the internal caller must carry a token"
        assert "Sandbox: MatrixLab" in out
        assert "Command: python <snippet>" in out

    def test_http_error_status_surfaces_the_detail(self, monkeypatch):
        self._patch_post(
            monkeypatch,
            lambda url, json: httpx.Response(400, json={"detail": "unsupported language"}),
        )
        out = _run(routing.run_snippet("rust", "fn main(){}", 30))

        assert out == "Sandbox error (400): unsupported language\n"

    def test_non_json_error_body_falls_back_to_text(self, monkeypatch):
        self._patch_post(monkeypatch, lambda url, json: httpx.Response(502, text="bad gateway"))
        out = _run(routing.run_snippet("python", "x", 30))

        assert out == "Sandbox error (502): bad gateway\n"

    def test_unreachable_api_is_reported_not_raised(self, monkeypatch):
        def handler(url, json):
            raise httpx.ConnectError("nothing listening")

        self._patch_post(monkeypatch, handler)
        out = _run(routing.run_snippet("python", "x", 30))

        assert out.startswith("Error: could not reach the in-process sandbox API:")

    def test_endpoint_honours_the_internal_url_override(self, monkeypatch):
        monkeypatch.setenv("GITPILOT_INTERNAL_URL", "http://elsewhere:9999")
        seen: dict = {}

        def handler(url, json):
            seen["url"] = url
            return httpx.Response(200, json={"exit_code": 0, "stdout": "", "stderr": ""})

        self._patch_post(monkeypatch, handler)
        _run(routing.run_snippet("bash", "true", 5))

        assert seen["url"] == "http://elsewhere:9999/api/sandbox/run"


class TestSyncAdapters:
    def test_run_sync_works_with_no_running_loop(self):
        async def answer():
            return 7

        assert routing.run_sync(answer()) == 7

    def test_run_sync_works_inside_a_running_loop(self):
        """CrewAI calls tools from a thread that may already own a loop."""
        async def outer():
            async def inner():
                return 9

            return routing.run_sync(inner())

        assert asyncio.run(outer()) == 9

    def test_command_adapter_delegates(self, monkeypatch, tmp_path):
        async def fake(command, timeout, workspace_path):
            return f"ran {command} in {workspace_path} for {timeout}s"

        monkeypatch.setattr(routing, "run_command_in_workspace", fake)
        out = routing.run_command_in_workspace_sync("echo hi", 5, tmp_path)

        assert out == f"ran echo hi in {tmp_path} for 5s"

    def test_snippet_adapter_delegates(self, monkeypatch):
        async def fake(language, code, timeout):
            return f"{language}:{code}:{timeout}"

        monkeypatch.setattr(routing, "run_snippet", fake)
        assert routing.run_snippet_sync("bash", "true", 5) == "bash:true:5"


class TestMatrixLabRerouting:
    def test_workspace_commands_become_bash_snippets(self, monkeypatch, tmp_path):
        """MatrixLab's /repo/run wants a repo_url; a workspace command is not that."""
        from gitpilot.settings import get_settings

        monkeypatch.setattr(get_settings().sandbox, "backend", "matrixlab", raising=False)
        seen: dict = {}

        async def fake_snippet(language, code, timeout):
            seen.update(language=language, code=code, timeout=timeout)
            return "ok"

        monkeypatch.setattr(routing, "run_snippet", fake_snippet)

        assert _run(routing.run_command_in_workspace("pytest -q", 42, tmp_path)) == "ok"
        assert seen == {"language": "bash", "code": "pytest -q", "timeout": 42}
