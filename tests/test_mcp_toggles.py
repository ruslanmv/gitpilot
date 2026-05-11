"""Tests for MCP per-tool toggles and the tool-output validator."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from gitpilot.mcp_toggles import (
    MCPServerToggles,
    MCPToggleRegistry,
    validate_tool_output,
)


@dataclass
class FakeTool:
    name: str


def test_default_toggle_enables_all_tools() -> None:
    toggles = MCPServerToggles(name="github")
    assert toggles.is_tool_enabled("search_code")


def test_allowlist_filters_tools() -> None:
    toggles = MCPServerToggles(name="github", enabled_tools={"search_code", "list_issues"})
    tools = [FakeTool("search_code"), FakeTool("create_pr"), FakeTool("list_issues")]
    kept = [t.name for t in toggles.filter_tools(tools)]
    assert sorted(kept) == ["list_issues", "search_code"]


def test_disabled_server_blocks_everything() -> None:
    toggles = MCPServerToggles(name="github", disabled=True)
    assert toggles.is_tool_enabled("anything") is False


def test_disabled_tools_take_priority_over_allow() -> None:
    toggles = MCPServerToggles(
        name="github",
        enabled_tools={"*"},
        disabled_tools={"create_pr"},
    )
    assert toggles.is_tool_enabled("search_code") is True
    assert toggles.is_tool_enabled("create_pr") is False


def test_always_allow_marks_tools(tmp_path: Path) -> None:
    toggles = MCPServerToggles(name="github", always_allow={"search_code"})
    assert toggles.is_always_allowed("search_code") is True
    assert toggles.is_always_allowed("create_pr") is False


def test_registry_merges_global_then_project(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("gitpilot.mcp_toggles.GLOBAL_MCP_PATH", home / ".gitpilot" / "mcp.json")
    (home / ".gitpilot").mkdir()
    (home / ".gitpilot" / "mcp.json").write_text(json.dumps({
        "servers": [{"name": "github", "alwaysAllow": ["search_code"]}],
    }))
    workspace = tmp_path / "ws"
    (workspace / ".gitpilot").mkdir(parents=True)
    (workspace / ".gitpilot" / "mcp.json").write_text(json.dumps({
        "servers": [{"name": "github", "disabled": True}],
    }))
    reg = MCPToggleRegistry.load(workspace_path=workspace)
    # Project file disables → overrides the user-global alwaysAllow.
    assert reg.get("github").disabled is True


def test_validator_truncates_large_output() -> None:
    huge = "x" * 1_000_000
    res = validate_tool_output(huge, max_bytes=1024)
    assert res.ok is True
    assert res.reason == "truncated"
    assert res.sanitised is not None
    assert "[truncated]" in res.sanitised


def test_validator_flags_control_characters() -> None:
    bad = "hello\x00world"
    res = validate_tool_output(bad)
    assert res.ok is False
    assert "control" in (res.reason or "")


def test_validator_accepts_normal_text() -> None:
    res = validate_tool_output("plain output\nwith newlines\n")
    assert res.ok is True
    assert res.sanitised is None
