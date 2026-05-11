"""Tests for AGENTS.md loader, /init, and memory imports."""
from __future__ import annotations

from pathlib import Path

import pytest

from gitpilot.agents_md import (
    AgentsLoader,
    load_for_session,
    run_init,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "Makefile").write_text("test:\n\tpytest -q\n")
    (tmp_path / "README.md").write_text("# demo\n")
    return tmp_path


def test_run_init_creates_starter_doc(workspace: Path) -> None:
    report = run_init(workspace)
    assert report.created is True
    assert report.path.exists()
    content = report.path.read_text(encoding="utf-8")
    assert "# AGENTS.md" in content
    assert "Python" in content  # stack detection


def test_run_init_is_idempotent(workspace: Path) -> None:
    run_init(workspace)
    second = run_init(workspace)
    assert second.created is False
    assert second.skipped_reason == "exists"


def test_loader_returns_empty_when_no_file(workspace: Path) -> None:
    doc = AgentsLoader(workspace).load()
    assert doc.is_empty


def test_loader_resolves_includes(workspace: Path) -> None:
    (workspace / "AGENTS.md").write_text("# Root\n\n@./fragments/db.md\n")
    (workspace / "fragments").mkdir()
    (workspace / "fragments" / "db.md").write_text("## DB\nPostgres notes.\n")
    doc = AgentsLoader(workspace).load()
    assert "Postgres notes" in doc.content


def test_loader_detects_circular_includes(workspace: Path) -> None:
    (workspace / "AGENTS.md").write_text("@./a.md\n")
    (workspace / "a.md").write_text("@./b.md\n")
    (workspace / "b.md").write_text("@./a.md\n")
    doc = AgentsLoader(workspace).load()
    assert doc.circular  # at least one circular reference detected


def test_mode_specific_overrides_are_appended(workspace: Path) -> None:
    (workspace / "AGENTS.md").write_text("# Root rules\n")
    (workspace / ".gitpilot").mkdir(exist_ok=True)
    (workspace / ".gitpilot" / "AGENTS.coder.md").write_text("# Coder-only rules\n")
    text = load_for_session(workspace, mode_slug="coder")
    assert "Root rules" in text
    assert "Coder-only rules" in text


def test_include_outside_workspace_is_blocked(workspace: Path, tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.md"
    outside.write_text("secret\n")
    (workspace / "AGENTS.md").write_text(f"@{outside}\n")
    doc = AgentsLoader(workspace).load()
    assert "secret" not in doc.content
