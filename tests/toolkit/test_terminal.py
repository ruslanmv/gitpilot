"""Terminal tools — Batch V4-A4.

These run real commands through the real subprocess sandbox: mocking the
sandbox would leave the tests proving the mock matches, when the point is that
the workspace jail and the credential scrub genuinely apply.
"""
from __future__ import annotations

import asyncio

import pytest

import gitpilot.local_tools as lt
from gitpilot.toolkit import (
    LocalWorkspace,
    Risk,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
)
from gitpilot.toolkit import terminal as terminal_tools

from .parity.fixture_repo import build_fixture_repo
from .parity.harness import ParityCase, assert_parity, run_case


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    terminal_tools.register(reg)
    return reg


@pytest.fixture()
def local_repo(tmp_path):
    ws = build_fixture_repo(tmp_path / "repo")
    return ws, ToolExecutionContext(workspace=LocalWorkspace(root=ws.path))


def _run(registry, tool, arguments, ctx):
    return asyncio.run(registry.execute(ToolCall(id="t", tool=tool, arguments=arguments), ctx))


class TestRun:
    def test_reports_stdout_and_exit_code(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "terminal.run", {"command": "echo hello"}, ctx)

        assert result.ok
        assert "Exit code: 0" in result.content
        assert "hello" in result.content
        assert result.data["exit_code"] == 0

    def test_failure_exit_code_is_data_not_an_error(self, registry, local_repo):
        """A failing test suite is a successful tool call — the model must see it."""
        _ws, ctx = local_repo
        result = _run(registry, "terminal.run", {"command": "exit 3"}, ctx)

        assert result.ok
        assert result.data["exit_code"] == 3

    def test_runs_inside_the_workspace(self, registry, local_repo):
        ws, ctx = local_repo
        result = _run(registry, "terminal.run", {"command": "ls"}, ctx)

        assert "pyproject.toml" in result.content

    def test_denylisted_command_is_refused_by_the_sandbox(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "terminal.run", {"command": "shutdown -h now"}, ctx)

        assert "Permission denied by sandbox policy" in result.content

    def test_credentials_are_not_visible_to_the_command(self, registry, local_repo, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
        _ws, ctx = local_repo

        result = _run(registry, "terminal.run", {"command": "env"}, ctx)

        assert "ghp_secret" not in result.content
        assert "sk-secret" not in result.content

    @pytest.mark.parametrize("timeout", [0, -5, 99999])
    def test_out_of_range_timeout_is_corrected_not_silently_clamped(
        self, registry, local_repo, timeout,
    ):
        """Rejecting teaches the bound; clamping hides that the request was wrong.

        Validation happens before any command runs, and the message names the
        limit, so the model's next call is right.
        """
        _ws, ctx = local_repo
        result = _run(registry, "terminal.run", {"command": "echo hi", "timeout": timeout}, ctx)

        assert result.error == "invalid_arguments"
        assert "timeout" in result.content

    def test_internal_callers_are_still_protected_by_clamping(self):
        """Non-model callers (settings, adapters) get coercion rather than a refusal."""
        from gitpilot.sandbox_routing import coerce_timeout

        assert coerce_timeout(99999) == 600
        assert coerce_timeout("not a number") == 120
        assert coerce_timeout(-1) == 120

    def test_default_timeout_is_recorded(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "terminal.run", {"command": "echo hi"}, ctx)
        assert result.data["timeout"] == 120

    def test_requires_a_workspace(self, registry):
        result = _run(registry, "terminal.run", {"command": "echo hi"}, ToolExecutionContext())
        assert not result.ok
        assert "no local workspace" in result.content

    def test_sandbox_import_failure_refuses_rather_than_downgrading(
        self, registry, local_repo, monkeypatch,
    ):
        """local_tools falls back to the unjailed executor here; this must not."""
        from gitpilot.sandbox_routing import SandboxFallback

        async def boom(*args, **kwargs):
            raise SandboxFallback("httpx missing")

        monkeypatch.setattr(
            "gitpilot.toolkit.terminal.run_command_in_workspace", boom,
        )
        _ws, ctx = local_repo
        result = _run(registry, "terminal.run", {"command": "echo hi"}, ctx)

        assert result.error == "sandbox_unavailable"
        assert "refusing to run the command outside it" in result.content


class TestBackendDispatch:
    """Which sandbox backend a command lands in, per the settings."""

    def _settings(self, backend, monkeypatch):
        from gitpilot.settings import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings.sandbox, "backend", backend, raising=False)
        monkeypatch.setattr("gitpilot.sandbox_routing.get_settings", lambda: settings, raising=False)
        return settings

    def test_subprocess_backend_is_the_default(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "terminal.run", {"command": "echo hi"}, ctx)

        assert "Sandbox: local subprocess" in result.content

    def test_off_backend_runs_on_the_host(self, registry, local_repo, monkeypatch):
        from gitpilot.settings import get_settings

        monkeypatch.setattr(get_settings().sandbox, "backend", "off", raising=False)
        _ws, ctx = local_repo

        result = _run(registry, "terminal.run", {"command": "echo hi"}, ctx)

        assert "Sandbox: pass-through (host)" in result.content

    def test_matrixlab_backend_reroutes_as_a_bash_snippet(self, registry, local_repo, monkeypatch):
        """MatrixLab's /repo/run wants a repo_url; a workspace command is not that."""
        from gitpilot.settings import get_settings

        monkeypatch.setattr(get_settings().sandbox, "backend", "matrixlab", raising=False)
        seen: dict = {}

        async def fake_snippet(language, code, timeout):
            seen.update(language=language, code=code, timeout=timeout)
            return "Sandbox: MatrixLab\nCommand: bash <snippet>\nExit code: 0\n"

        monkeypatch.setattr("gitpilot.sandbox_routing.run_snippet", fake_snippet)
        _ws, ctx = local_repo

        result = _run(registry, "terminal.run", {"command": "pytest -q"}, ctx)

        assert seen == {"language": "bash", "code": "pytest -q", "timeout": 120}
        assert "MatrixLab" in result.content

    def test_oversized_output_is_truncated_with_a_warning(self, registry, local_repo):
        """The 512 KB cap is what keeps one chatty command from eating the context.

        Genuinely exceeded rather than monkeypatched: the cap is a
        ``SandboxPolicy`` field default, and ``sandbox_routing`` does not expose
        it, so patching the module constant would prove nothing.
        """
        from gitpilot.sandbox import MAX_OUTPUT_BYTES

        _ws, ctx = local_repo
        overflow = MAX_OUTPUT_BYTES + 50_000

        result = _run(
            registry, "terminal.run",
            {"command": f"python3 -c \"print('x' * {overflow})\""}, ctx,
        )

        assert "WARNING: Output was truncated" in result.content
        assert len(result.content) < overflow


class TestWhichAndEnv:
    def test_which_finds_a_present_program(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "terminal.which", {"program": "sh"}, ctx)

        assert result.data["found"] is True
        assert result.data["path"].endswith("sh")

    def test_which_reports_a_missing_program(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "terminal.which", {"program": "definitely_not_installed_xyz"}, ctx)

        assert result.data["found"] is False
        assert result.content.endswith("not found")

    def test_env_lists_names_without_values(self, registry, local_repo, monkeypatch):
        """Names only, so an unrelated secret cannot land in the transcript."""
        monkeypatch.setenv("MY_MARKER", "marker-value")
        _ws, ctx = local_repo

        result = _run(registry, "terminal.env", {}, ctx)

        assert "MY_MARKER" in result.data["names"]
        assert "marker-value" not in result.content

    def test_env_reads_one_variable_by_name(self, registry, local_repo, monkeypatch):
        monkeypatch.setenv("MY_MARKER", "marker-value")
        _ws, ctx = local_repo

        result = _run(registry, "terminal.env", {"name": "MY_MARKER"}, ctx)

        assert result.content == "MY_MARKER=marker-value"

    def test_env_reports_a_stripped_credential_as_unset(self, registry, local_repo, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
        _ws, ctx = local_repo

        result = _run(registry, "terminal.env", {"name": "GITHUB_TOKEN"}, ctx)

        assert result.data["set"] is False


class TestSnippet:
    def test_unsupported_language_refused_before_any_call(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "terminal.run_snippet", {"language": "rust", "code": "fn main(){}"}, ctx)

        assert result.error == "invalid_arguments"
        assert "must be one of" in result.content


class TestRiskClassification:
    def test_execution_is_high_risk_and_inspection_is_safe(self, registry):
        assert registry.spec("terminal.run").risk is Risk.HIGH_RISK
        assert registry.spec("terminal.run_snippet").risk is Risk.HIGH_RISK
        assert registry.spec("terminal.which").risk is Risk.SAFE
        assert registry.spec("terminal.env").risk is Risk.SAFE


PARITY_CASES = [
    ParityCase(
        name="terminal.run/echo",
        tool="terminal.run",
        arguments={"command": "echo parity"},
        legacy=lambda ws: lt.run_command.func("echo parity", "120"),
        # Duration is wall-clock; the shape either side of it is the contract.
        normalise=lambda text: "\n".join(
            line for line in text.splitlines() if not line.startswith("Duration:")
        ),
    ),
    ParityCase(
        name="terminal.run/nonzero-exit",
        tool="terminal.run",
        arguments={"command": "sh -c 'echo out; echo err >&2; exit 4'"},
        legacy=lambda ws: lt.run_command.func("sh -c 'echo out; echo err >&2; exit 4'", "120"),
        normalise=lambda text: "\n".join(
            line for line in text.splitlines() if not line.startswith("Duration:")
        ),
    ),
    ParityCase(
        name="terminal.run/denied",
        tool="terminal.run",
        arguments={"command": "mkfs /dev/sda"},
        legacy=lambda ws: lt.run_command.func("mkfs /dev/sda", "120"),
    ),
]


@pytest.mark.parametrize("case", PARITY_CASES, ids=lambda c: c.name)
def test_registry_output_matches_the_crewai_tool(case, registry, tmp_path):
    ws = build_fixture_repo(tmp_path / "repo")
    lt.set_active_workspace(ws)
    try:
        outcome = run_case(
            case, ws, registry, ToolExecutionContext(workspace=LocalWorkspace(root=ws.path)),
        )
    finally:
        lt._current_workspace = None

    assert_parity(outcome)
