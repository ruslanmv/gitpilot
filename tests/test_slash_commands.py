"""Tests for the markdown slash-command registry."""
from __future__ import annotations

from pathlib import Path

import pytest

from gitpilot.slash_commands import SlashCommand, SlashCommandRegistry


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    cmds = tmp_path / ".gitpilot" / "commands"
    cmds.mkdir(parents=True)
    (cmds / "review.md").write_text(
        "---\n"
        "description: Code review\n"
        "argument-hint: <path>\n"
        "---\n"
        "Review file $1 and report on $ARGS.\n"
    )
    (cmds / "deploy-check.md").write_text("Check deploy on $1.\n")
    return tmp_path


def test_registry_loads_workspace_commands(workspace: Path) -> None:
    reg = SlashCommandRegistry()
    count = reg.load(workspace_path=workspace)
    assert count == 2
    assert reg.get("review") is not None
    assert reg.get("deploy-check") is not None


def test_render_substitutes_positional_and_args(workspace: Path) -> None:
    reg = SlashCommandRegistry()
    reg.load(workspace_path=workspace)
    cmd = reg.get("review")
    assert cmd is not None
    rendered = cmd.render(["src/app.py", "security"])
    assert "src/app.py" in rendered
    assert "src/app.py security" in rendered


def test_parse_invocation_extracts_args(workspace: Path) -> None:
    reg = SlashCommandRegistry()
    reg.load(workspace_path=workspace)
    parsed = reg.parse_invocation('/review "src/main.py" notes')
    assert parsed is not None
    cmd, args = parsed
    assert cmd.name == "review"
    assert args == ["src/main.py", "notes"]


def test_parse_invocation_returns_none_for_plain_message(workspace: Path) -> None:
    reg = SlashCommandRegistry()
    reg.load(workspace_path=workspace)
    assert reg.parse_invocation("hello there") is None
    assert reg.parse_invocation("/unknown stuff") is None


def test_name_normalisation_handles_messy_filenames(tmp_path: Path) -> None:
    cmds = tmp_path / ".gitpilot" / "commands"
    cmds.mkdir(parents=True)
    (cmds / "My Cool Command!.md").write_text("Hello\n")
    reg = SlashCommandRegistry()
    reg.load(workspace_path=tmp_path)
    assert reg.get("my-cool-command") is not None


def test_inline_register() -> None:
    reg = SlashCommandRegistry()
    reg.register(SlashCommand(name="ping", template="pong $1"))
    cmd = reg.get("ping")
    assert cmd is not None
    assert cmd.render(["world"]) == "pong world"
