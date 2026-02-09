"""Tests for gitpilot.memory module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gitpilot.memory import (
    MAX_CONVENTIONS_CHARS,
    MAX_PATTERNS,
    MAX_RULE_CHARS,
    MemoryManager,
    ProjectContext,
)


# ---------------------------------------------------------------------------
# ProjectContext
# ---------------------------------------------------------------------------

class TestProjectContext:
    def test_empty_context(self):
        ctx = ProjectContext()
        assert ctx.is_empty is True
        assert ctx.to_system_prompt() == ""

    def test_conventions_only(self):
        ctx = ProjectContext(conventions="Use black for formatting")
        assert ctx.is_empty is False
        prompt = ctx.to_system_prompt()
        assert "Project Conventions" in prompt
        assert "Use black" in prompt

    def test_rules_only(self):
        ctx = ProjectContext(rules=["Rule 1", "Rule 2"])
        prompt = ctx.to_system_prompt()
        assert "Project Rules" in prompt
        assert "Rule 1" in prompt
        assert "Rule 2" in prompt

    def test_patterns_in_auto_memory(self):
        ctx = ProjectContext(auto_memory={"patterns": ["always use type hints"]})
        prompt = ctx.to_system_prompt()
        assert "Learned Patterns" in prompt
        assert "always use type hints" in prompt

    def test_is_empty_with_empty_auto_memory(self):
        ctx = ProjectContext(auto_memory={})
        assert ctx.is_empty is True

    def test_full_context(self):
        ctx = ProjectContext(
            conventions="PEP 8",
            rules=["No print() in prod"],
            auto_memory={"patterns": ["prefer pathlib"]},
        )
        prompt = ctx.to_system_prompt()
        assert "PEP 8" in prompt
        assert "No print" in prompt
        assert "prefer pathlib" in prompt


# ---------------------------------------------------------------------------
# MemoryManager.load_context
# ---------------------------------------------------------------------------

class TestLoadContext:
    def test_empty_workspace(self, tmp_path):
        mgr = MemoryManager(tmp_path)
        ctx = mgr.load_context()
        assert ctx.is_empty is True

    def test_loads_conventions(self, tmp_path):
        gp = tmp_path / ".gitpilot"
        gp.mkdir()
        (gp / "GITPILOT.md").write_text("# Conventions\nUse pytest")
        mgr = MemoryManager(tmp_path)
        ctx = mgr.load_context()
        assert "Use pytest" in ctx.conventions

    def test_truncates_long_conventions(self, tmp_path):
        gp = tmp_path / ".gitpilot"
        gp.mkdir()
        (gp / "GITPILOT.md").write_text("x" * (MAX_CONVENTIONS_CHARS + 1000))
        mgr = MemoryManager(tmp_path)
        ctx = mgr.load_context()
        assert len(ctx.conventions) == MAX_CONVENTIONS_CHARS

    def test_loads_rules(self, tmp_path):
        gp = tmp_path / ".gitpilot"
        rules = gp / "rules"
        rules.mkdir(parents=True)
        (rules / "style.md").write_text("Use black")
        (rules / "testing.md").write_text("100% coverage")

        mgr = MemoryManager(tmp_path)
        ctx = mgr.load_context()
        assert len(ctx.rules) == 2
        assert any("style" in r for r in ctx.rules)
        assert any("testing" in r for r in ctx.rules)

    def test_loads_auto_memory(self, tmp_path):
        gp = tmp_path / ".gitpilot"
        gp.mkdir()
        data = {"patterns": ["use async/await"], "extra": "value"}
        (gp / "memory.json").write_text(json.dumps(data))

        mgr = MemoryManager(tmp_path)
        ctx = mgr.load_context()
        assert ctx.auto_memory["patterns"] == ["use async/await"]
        assert ctx.auto_memory["extra"] == "value"

    def test_bad_json_in_auto_memory(self, tmp_path):
        gp = tmp_path / ".gitpilot"
        gp.mkdir()
        (gp / "memory.json").write_text("not json{{{")

        mgr = MemoryManager(tmp_path)
        ctx = mgr.load_context()
        assert ctx.auto_memory == {}


# ---------------------------------------------------------------------------
# save_auto_memory / add_learned_pattern
# ---------------------------------------------------------------------------

class TestAutoMemory:
    def test_save_auto_memory(self, tmp_path):
        mgr = MemoryManager(tmp_path)
        mgr.save_auto_memory({"patterns": ["a"]})
        data = json.loads((tmp_path / ".gitpilot" / "memory.json").read_text())
        assert data["patterns"] == ["a"]

    def test_add_learned_pattern(self, tmp_path):
        mgr = MemoryManager(tmp_path)
        mgr.add_learned_pattern("prefer pathlib")
        mgr.add_learned_pattern("use type hints")

        data = json.loads((tmp_path / ".gitpilot" / "memory.json").read_text())
        assert "prefer pathlib" in data["patterns"]
        assert "use type hints" in data["patterns"]

    def test_duplicate_pattern_not_added(self, tmp_path):
        mgr = MemoryManager(tmp_path)
        mgr.add_learned_pattern("same")
        mgr.add_learned_pattern("same")

        data = json.loads((tmp_path / ".gitpilot" / "memory.json").read_text())
        assert data["patterns"].count("same") == 1

    def test_max_patterns_enforced(self, tmp_path):
        mgr = MemoryManager(tmp_path)
        for i in range(MAX_PATTERNS + 20):
            mgr.add_learned_pattern(f"pattern-{i}")

        data = json.loads((tmp_path / ".gitpilot" / "memory.json").read_text())
        assert len(data["patterns"]) == MAX_PATTERNS
        # The oldest patterns should have been trimmed
        assert "pattern-0" not in data["patterns"]


# ---------------------------------------------------------------------------
# init_project
# ---------------------------------------------------------------------------

class TestInitProject:
    def test_creates_template(self, tmp_path):
        mgr = MemoryManager(tmp_path)
        md_path = mgr.init_project()
        assert md_path.exists()
        content = md_path.read_text()
        assert "GitPilot" in content
        assert "Code Style" in content

    def test_creates_rules_dir(self, tmp_path):
        mgr = MemoryManager(tmp_path)
        mgr.init_project()
        assert (tmp_path / ".gitpilot" / "rules").is_dir()

    def test_does_not_overwrite_existing(self, tmp_path):
        gp = tmp_path / ".gitpilot"
        gp.mkdir()
        md = gp / "GITPILOT.md"
        md.write_text("custom content")

        mgr = MemoryManager(tmp_path)
        mgr.init_project()
        assert md.read_text() == "custom content"


# ---------------------------------------------------------------------------
# get / set conventions
# ---------------------------------------------------------------------------

class TestConventionsText:
    def test_get_conventions_empty(self, tmp_path):
        mgr = MemoryManager(tmp_path)
        assert mgr.get_conventions_text() == ""

    def test_set_and_get_conventions(self, tmp_path):
        mgr = MemoryManager(tmp_path)
        mgr.set_conventions_text("My rules")
        assert mgr.get_conventions_text() == "My rules"
