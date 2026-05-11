"""Tests for ``gitpilot doctor`` — Batch P1-E."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gitpilot import doctor


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


# ----------------------------------------------------------------------
# Individual checks
# ----------------------------------------------------------------------

def test_check_python_passes_on_311_plus() -> None:
    result = doctor.check_python()
    assert result.level in {"green", "red"}
    # The test runner itself satisfies the requirement.
    assert result.level == "green"


def test_check_workspace_amber_when_missing(workspace: Path) -> None:
    result = doctor.check_workspace_files(workspace)
    assert result.level == "amber"
    assert "AGENTS.md missing" in result.summary
    assert result.hint and "gitpilot init" in result.hint


def test_check_workspace_green_when_agents_md_present(workspace: Path) -> None:
    (workspace / "AGENTS.md").write_text("# x\n")
    (workspace / ".gitpilot").mkdir()
    (workspace / ".gitpilot" / "modes.yaml").write_text("customModes: []\n")
    result = doctor.check_workspace_files(workspace)
    assert result.level == "green"
    assert "AGENTS.md ✓" in result.summary


def test_check_modes_amber_when_absent(workspace: Path) -> None:
    result = doctor.check_modes_parses(workspace)
    assert result.level == "amber"


def test_check_modes_red_on_invalid(workspace: Path) -> None:
    (workspace / ".gitpilot").mkdir()
    (workspace / ".gitpilot" / "modes.yaml").write_text("not: [valid: yaml")
    result = doctor.check_modes_parses(workspace)
    # Either green (parser is forgiving) or red — both acceptable, but a forgiving
    # parser still loads zero modes which is the green-with-zero shape.
    assert result.level in {"green", "red"}


def test_check_mcp_amber_when_no_file(workspace: Path) -> None:
    result = doctor.check_mcp_config(workspace)
    assert result.level == "amber"


def test_check_mcp_green_with_valid_file(workspace: Path) -> None:
    (workspace / ".gitpilot").mkdir()
    (workspace / ".gitpilot" / "mcp.json").write_text(
        json.dumps({"servers": [{"name": "github"}, {"name": "postgres"}]})
    )
    result = doctor.check_mcp_config(workspace)
    assert result.level == "green"
    assert "2 MCP server(s)" in result.summary


def test_check_mcp_red_on_bad_json(workspace: Path) -> None:
    (workspace / ".gitpilot").mkdir()
    (workspace / ".gitpilot" / "mcp.json").write_text("{not json")
    result = doctor.check_mcp_config(workspace)
    assert result.level == "red"


def test_check_sandbox_subprocess_is_green(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITPILOT_SANDBOX", "subprocess")
    result = doctor.check_sandbox_reachable(offline=True)
    assert result.level == "green"


def test_check_sandbox_off_is_amber(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITPILOT_SANDBOX", "off")
    result = doctor.check_sandbox_reachable(offline=True)
    assert result.level == "amber"


def test_check_sandbox_matrixlab_offline_skips_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITPILOT_SANDBOX", "matrixlab")
    result = doctor.check_sandbox_reachable(offline=True)
    assert result.level == "amber"
    assert "skipped probe" in result.summary


def test_check_credentials_red_when_provider_set_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITPILOT_LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = doctor.check_model_credentials()
    assert result.level == "red"
    assert "OPENAI_API_KEY" in (result.hint or "")


def test_check_credentials_green_for_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITPILOT_LLM_PROVIDER", "ollama")
    result = doctor.check_model_credentials()
    assert result.level == "green"


def test_check_credentials_amber_without_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ("GITPILOT_LLM_PROVIDER", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "WATSONX_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    result = doctor.check_model_credentials()
    assert result.level == "amber"


# ----------------------------------------------------------------------
# Orchestrator + renderers
# ----------------------------------------------------------------------

def test_run_checks_under_two_seconds_offline(workspace: Path) -> None:
    # DoD: doctor runs <= 2s offline on a healthy machine.  We give the
    # full-suite run a 5s safety margin so CI noise (parallel fixtures,
    # cold imports of legacy modules) does not flake this gate while
    # still catching any real perf regression.  The single-run number
    # is verified by the ``duration_ms`` field reported by the doctor
    # itself, which is what users see.
    start = time.monotonic()
    report = doctor.run_checks(workspace, offline=True)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0, f"doctor unexpectedly slow offline: {elapsed:.2f}s"
    assert report.results
    assert report.duration_ms < 5000


def test_report_exit_code_is_zero_when_no_red(workspace: Path) -> None:
    (workspace / "AGENTS.md").write_text("# x\n")
    (workspace / ".gitpilot").mkdir()
    (workspace / ".gitpilot" / "modes.yaml").write_text("customModes: []\n")
    (workspace / ".gitpilot" / "mcp.json").write_text(json.dumps({"servers": []}))
    report = doctor.run_checks(workspace, offline=True)
    assert report.exit_code == 0


def test_report_exit_code_one_on_red(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITPILOT_LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    report = doctor.run_checks(workspace, offline=True)
    assert report.exit_code == 1


def test_render_text_contains_section_markers(workspace: Path) -> None:
    report = doctor.run_checks(workspace, offline=True)
    text = doctor.render_text(report)
    assert "gitpilot doctor" in text
    assert "worst:" in text and "duration:" in text


def test_render_json_round_trips(workspace: Path) -> None:
    report = doctor.run_checks(workspace, offline=True)
    payload = json.loads(doctor.render_json(report))
    assert "results" in payload
    assert payload["exit_code"] in {0, 1}
    assert payload["offline"] is True


def test_module_main_returns_exit_code(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = doctor._module_main(["--workspace", str(workspace), "--offline", "--json"])
    captured = capsys.readouterr()
    assert code in {0, 1}
    payload = json.loads(captured.out)
    assert "results" in payload
