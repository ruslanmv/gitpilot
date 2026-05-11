"""Tests for the custom-rules loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from gitpilot.rules import compose_rules, load_rules


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("gitpilot.rules.USER_RULES_ROOT", home)
    return home


def test_loads_workspace_single_file_rules(tmp_path: Path, isolated_home: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".gitpilotrules").write_text("Use 4-space indent.")
    ruleset = load_rules(workspace_path=workspace)
    assert any(r.body == "Use 4-space indent." for r in ruleset.rules)
    assert any(r.source == "workspace" for r in ruleset.rules)


def test_loads_directory_rules(tmp_path: Path, isolated_home: Path) -> None:
    workspace = tmp_path / "ws"
    rules_dir = workspace / ".gitpilot" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "style.md").write_text("Always type-annotate public functions.")
    (rules_dir / "tests.md").write_text("Every bug fix needs a regression test.")
    ruleset = load_rules(workspace_path=workspace)
    names = {r.name for r in ruleset.rules}
    assert {"style", "tests"} <= names


def test_mode_specific_rules_only_load_for_that_mode(tmp_path: Path, isolated_home: Path) -> None:
    workspace = tmp_path / "ws"
    rules_dir = workspace / ".gitpilot" / "rules-coder"
    rules_dir.mkdir(parents=True)
    (rules_dir / "coder.md").write_text("Use the project linter before committing.")
    base = load_rules(workspace_path=workspace)
    coder = load_rules(workspace_path=workspace, mode_slug="coder")
    assert not any(r.name == "coder" for r in base.rules)
    assert any(r.name == "coder" for r in coder.rules)


def test_global_then_workspace_order(tmp_path: Path, isolated_home: Path) -> None:
    global_dir = isolated_home / "rules"
    global_dir.mkdir()
    (global_dir / "policy.md").write_text("Global policy.")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".gitpilotrules").write_text("Workspace addendum.")
    ruleset = load_rules(workspace_path=workspace)
    sources = [r.source for r in ruleset.rules]
    # Global must appear before workspace in iteration order so workspace
    # entries override later in the prompt.
    assert sources.index("global") < sources.index("workspace")


def test_compose_returns_markdown_block(tmp_path: Path, isolated_home: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".gitpilotrules").write_text("Pithy rule.")
    md, ruleset = compose_rules(workspace_path=workspace)
    assert "## Custom rules" in md
    assert "Pithy rule." in md
    assert ruleset.rules
