"""Tests for the YAML modes layer."""
from __future__ import annotations

from pathlib import Path

import pytest

from gitpilot.modes import (
    Mode,
    ModeRegistry,
    activate_mode,
)
from gitpilot.tool_groups import ToolCategory


MODES_YAML = """\
customModes:
  - slug: db-pilot
    name: "DB Pilot"
    description: "Postgres assistant"
    roleDefinition: |
      You are a senior DBA.
    whenToUse: |
      Use for schema and migration questions.
    customInstructions: |
      Always EXPLAIN before mutating.
    groups:
      - read
      - mcp:
          allow: ["postgres.query", "postgres.explain"]
          alwaysAllow: ["postgres.explain"]
      - edit:
          fileRegex: "^migrations/.*\\\\.sql$"
    mcpServers:
      postgres:
        command: uvx
        args: [mcp-postgres-server]
        env: { PG_URL: "postgresql://localhost/demo" }
        alwaysAllow: [postgres.explain]
"""


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".gitpilot").mkdir()
    (tmp_path / ".gitpilot" / "modes.yaml").write_text(MODES_YAML)
    return tmp_path


def test_registry_loads_project_modes(workspace: Path) -> None:
    reg = ModeRegistry()
    count = reg.load(workspace_path=workspace)
    assert count >= 1
    assert reg.get("db-pilot") is not None


def test_mode_tool_policy_is_built_from_groups(workspace: Path) -> None:
    reg = ModeRegistry()
    reg.load(workspace_path=workspace)
    mode = reg.get("db-pilot")
    assert mode is not None
    policy = mode.tool_policy()
    assert ToolCategory.READ in policy.enabled_categories
    assert ToolCategory.MCP in policy.enabled_categories
    assert policy.allow_tool("postgres.query") is True
    assert policy.allow_tool("github.search_code") is False


def test_activate_mode_returns_full_context(workspace: Path) -> None:
    reg = ModeRegistry()
    reg.load(workspace_path=workspace)
    ctx = activate_mode(reg, "db-pilot")
    assert ctx is not None
    assert "senior DBA" in ctx.system_prompt_block
    assert ctx.mcp_server_configs and ctx.mcp_server_configs[0]["name"] == "postgres"
    assert ctx.extra_mcp_toggles[0][0] == "postgres"


def test_activate_unknown_mode_returns_none(workspace: Path) -> None:
    reg = ModeRegistry()
    reg.load(workspace_path=workspace)
    assert activate_mode(reg, "no-such-mode") is None


def test_project_modes_override_user_modes(tmp_path: Path, monkeypatch) -> None:
    user_home = tmp_path / "home"
    user_dir = user_home / ".gitpilot"
    user_dir.mkdir(parents=True)
    monkeypatch.setattr("gitpilot.modes.USER_MODES_FILE", user_dir / "modes.yaml")
    (user_dir / "modes.yaml").write_text(
        "customModes:\n"
        "  - slug: clash\n"
        "    name: User Mode\n"
        "    description: from user\n"
        "    groups: [read]\n"
    )
    project = tmp_path / "ws"
    (project / ".gitpilot").mkdir(parents=True)
    (project / ".gitpilot" / "modes.yaml").write_text(
        "customModes:\n"
        "  - slug: clash\n"
        "    name: Project Mode\n"
        "    description: from project\n"
        "    groups: [read, edit]\n"
    )
    reg = ModeRegistry()
    reg.load(workspace_path=project)
    mode = reg.get("clash")
    assert mode is not None
    assert mode.source == "project"
    assert mode.description == "from project"


def test_inline_mode_registration() -> None:
    reg = ModeRegistry()
    reg.register(Mode(slug="custom", name="Custom", groups=["read"]))
    assert reg.get("custom") is not None
