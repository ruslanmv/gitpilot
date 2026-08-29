"""test.detect / test.run — Batch V4-A6."""
from __future__ import annotations

import asyncio
import json

import pytest

from gitpilot.toolkit import (
    LocalWorkspace,
    Risk,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
)
from gitpilot.toolkit import testing as testing_tools

from .parity.fixture_repo import build_fixture_repo


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    testing_tools.register(reg)
    return reg


def _run(registry, tool, arguments, ctx):
    return asyncio.run(registry.execute(ToolCall(id="t", tool=tool, arguments=arguments), ctx))


def _ctx(path):
    return ToolExecutionContext(workspace=LocalWorkspace(root=path))


class TestDetect:
    def test_detects_pytest_from_pyproject(self, tmp_path):
        """The fixture repo carries a [tool.pytest.ini_options] section."""
        ws = build_fixture_repo(tmp_path / "repo")
        reg = ToolRegistry()
        testing_tools.register(reg)

        result = _run(reg, "test.detect", {}, _ctx(ws.path))

        assert result.data["framework"] == "pytest"
        assert result.data["command"] == "python -m pytest --tb=short -q 2>&1"
        assert "Single target:" in result.content

    @pytest.mark.parametrize("marker,framework,expected_command", [
        ("jest.config.js", "jest", "npx jest --ci --no-coverage 2>&1"),
        ("vitest.config.ts", "vitest", "npx vitest run 2>&1"),
        ("Cargo.toml", "cargo", "cargo test 2>&1"),
        ("go.mod", "go", "go test ./... 2>&1"),
        ("pytest.ini", "pytest", "python -m pytest --tb=short -q 2>&1"),
    ])
    def test_detects_each_framework_by_marker(
        self, registry, tmp_path, marker, framework, expected_command,
    ):
        (tmp_path / marker).write_text("")
        result = _run(registry, "test.detect", {}, _ctx(tmp_path))

        assert result.data["framework"] == framework
        assert result.data["command"] == expected_command

    def test_detects_npm_test_script(self, registry, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
        result = _run(registry, "test.detect", {}, _ctx(tmp_path))

        assert result.data["command"] == "npm test -- --ci 2>&1"

    def test_ignores_npm_placeholder_script(self, registry, tmp_path):
        """`npm init`'s default script is not a test suite."""
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}})
        )
        result = _run(registry, "test.detect", {}, _ctx(tmp_path))

        assert result.data["command"] is None

    def test_says_what_it_looked_for_when_nothing_matches(self, registry, tmp_path):
        result = _run(registry, "test.detect", {}, _ctx(tmp_path))

        assert result.ok
        assert result.data["framework"] is None
        assert "pytest/jest/vitest" in result.content


class TestRun:
    def test_runs_the_suite_and_reports_counts(self, registry, tmp_path):
        """A real pytest run in a throwaway project, through the sandbox."""
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        (tmp_path / "test_sample.py").write_text(
            "def test_ok():\n    assert True\n\n\ndef test_also_ok():\n    assert 1 == 1\n"
        )

        result = _run(registry, "test.run", {}, _ctx(tmp_path))

        assert result.ok
        assert result.data["passed"] == 2
        assert result.data["failed"] == 0
        assert result.data["green"] is True
        assert result.content.startswith("Tests: 2 passed, 0 failed, 0 skipped")

    def test_a_failing_suite_is_a_successful_call(self, registry, tmp_path):
        """The failure is the observation the model needs, not a tool error."""
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        (tmp_path / "test_sample.py").write_text(
            "def test_ok():\n    assert True\n\n\ndef test_bad():\n    assert False\n"
        )

        result = _run(registry, "test.run", {}, _ctx(tmp_path))

        assert result.ok, "a red suite must not look like a broken tool"
        assert result.data["failed"] == 1
        assert result.data["green"] is False
        assert result.data["exit_code"] != 0

    def test_target_narrows_the_command(self, registry, tmp_path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        (tmp_path / "test_a.py").write_text("def test_a():\n    assert True\n")
        (tmp_path / "test_b.py").write_text("def test_b():\n    assert False\n")

        result = _run(registry, "test.run", {"target": "test_a.py"}, _ctx(tmp_path))

        assert result.data["command"] == "python -m pytest --tb=short -q test_a.py 2>&1"
        assert result.data["passed"] == 1
        assert result.data["failed"] == 0, "test_b.py must not have run"

    def test_refuses_when_no_framework_exists(self, registry, tmp_path):
        result = _run(registry, "test.run", {}, _ctx(tmp_path))

        assert not result.ok and result.error == "no_framework"
        assert "test.detect" in result.content

    def test_counts_come_from_the_shared_parser(self, tmp_path):
        """The validation phase and this tool must not report different numbers."""
        from gitpilot.agent_executor import StreamingAgentExecutor
        from gitpilot.test_detection import parse_test_counts

        output = "3 passed, 1 failed, 2 skipped"
        assert parse_test_counts(output) == StreamingAgentExecutor._parse_test_counts(output)


class TestRiskClassification:
    def test_detect_is_safe_and_run_is_high_risk(self, registry):
        assert registry.spec("test.detect").risk is Risk.SAFE
        assert registry.spec("test.run").risk is Risk.HIGH_RISK
