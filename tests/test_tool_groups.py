"""Tests for tool category classification and ToolPolicy."""
from __future__ import annotations

from gitpilot.tool_groups import (
    EditGuard,
    MCPGuard,
    ToolCategory,
    ToolPolicy,
    classify,
    register_category,
)


def test_classify_known_tools() -> None:
    assert classify("read_local_file") is ToolCategory.READ
    assert classify("write_local_file") is ToolCategory.EDIT
    assert classify("run_command") is ToolCategory.COMMAND


def test_classify_mcp_tool_pattern() -> None:
    assert classify("postgres.query") is ToolCategory.MCP
    assert classify("mcp__github__search_code") is ToolCategory.MCP


def test_register_custom_category() -> None:
    register_category("super_custom_tool", ToolCategory.COMMAND)
    assert classify("super_custom_tool") is ToolCategory.COMMAND


def test_empty_policy_allows_everything() -> None:
    policy = ToolPolicy.permissive()
    assert policy.allow_tool("write_local_file", target_path="anywhere.py") is True


def test_restrictive_policy_blocks_unlisted_category() -> None:
    policy = ToolPolicy.from_mode_groups(["read"])
    assert policy.allow_tool("read_local_file") is True
    assert policy.allow_tool("write_local_file") is False


def test_edit_guard_enforces_file_regex() -> None:
    policy = ToolPolicy.from_mode_groups(
        [{"edit": {"fileRegex": r"^migrations/.*\.sql$"}}]
    )
    assert policy.allow_tool("write_local_file", target_path="migrations/0001.sql") is True
    assert policy.allow_tool("write_local_file", target_path="src/app.py") is False


def test_mcp_guard_allow_and_always() -> None:
    policy = ToolPolicy.from_mode_groups(
        [{"mcp": {
            "allow": ["postgres.*"],
            "alwaysAllow": ["postgres.explain"],
        }}]
    )
    assert policy.allow_tool("postgres.query") is True
    assert policy.allow_tool("github.search_code") is False
    assert policy.always_allowed("postgres.explain") is True
    assert policy.always_allowed("postgres.query") is False


def test_disabled_server_blocks_all_its_tools() -> None:
    guard = MCPGuard(disabled_servers={"github"})
    assert guard.is_enabled("github.search_code") is False
    assert guard.is_enabled("postgres.query") is True


def test_edit_guard_handles_invalid_regex_gracefully() -> None:
    guard = EditGuard(file_regex="[invalid")
    # Invalid regex must not crash, just reject.
    assert guard.matches("anything") is False
