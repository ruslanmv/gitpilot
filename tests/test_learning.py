"""Tests for gitpilot.learning module."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from gitpilot.learning import (
    Evaluation,
    LearningEngine,
    RepoInsights,
)


# ---------------------------------------------------------------------------
# Evaluation dataclass
# ---------------------------------------------------------------------------

class TestEvaluation:
    def test_default_fields(self):
        ev = Evaluation(task_description="fix bug", success=True)
        assert ev.task_description == "fix bug"
        assert ev.success is True
        assert ev.outcome_type == ""
        assert ev.details == ""
        assert ev.confidence == 0.8
        assert ev.timestamp  # auto-populated

    def test_to_dict(self):
        ev = Evaluation(
            task_description="deploy", success=False,
            outcome_type="error", details="timeout", confidence=0.5,
        )
        d = ev.to_dict()
        assert d["task_description"] == "deploy"
        assert d["success"] is False
        assert d["outcome_type"] == "error"
        assert d["details"] == "timeout"
        assert d["confidence"] == 0.5
        assert "timestamp" in d

    def test_custom_timestamp(self):
        ev = Evaluation(task_description="t", success=True, timestamp="2025-01-01T00:00:00Z")
        assert ev.timestamp == "2025-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# RepoInsights dataclass
# ---------------------------------------------------------------------------

class TestRepoInsights:
    def test_default_fields(self):
        ri = RepoInsights(repo="owner/repo")
        assert ri.repo == "owner/repo"
        assert ri.patterns == []
        assert ri.preferred_style == {}
        assert ri.common_errors == []
        assert ri.success_rate == 0.0
        assert ri.total_tasks == 0
        assert ri.successful_tasks == 0

    def test_to_dict(self):
        ri = RepoInsights(
            repo="a/b", patterns=["p1"], preferred_style={"indent": "4"},
            common_errors=["e1"], success_rate=0.75, total_tasks=4, successful_tasks=3,
        )
        d = ri.to_dict()
        assert d["repo"] == "a/b"
        assert d["patterns"] == ["p1"]
        assert d["preferred_style"]["indent"] == "4"
        assert d["success_rate"] == 0.75


# ---------------------------------------------------------------------------
# LearningEngine.evaluate_outcome
# ---------------------------------------------------------------------------

class TestEvaluateOutcome:
    def test_success_tests_passed(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        ev = engine.evaluate_outcome("fix login", {"tests_passed": True})
        assert ev.success is True
        assert ev.outcome_type == "tests_passed"
        assert ev.confidence == 0.9

    def test_success_pr_approved(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        ev = engine.evaluate_outcome("refactor", {"pr_approved": True})
        assert ev.success is True
        assert ev.outcome_type == "pr_approved"

    def test_success_error_fixed(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        ev = engine.evaluate_outcome("patch", {"error_fixed": True})
        assert ev.success is True
        assert ev.outcome_type == "error_fixed"

    def test_explicit_success_override(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        ev = engine.evaluate_outcome("task", {"success": True})
        assert ev.success is True
        assert ev.outcome_type == "completed"

    def test_explicit_failure(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        ev = engine.evaluate_outcome("task", {"success": False})
        assert ev.success is False
        assert ev.outcome_type == "unknown"
        assert ev.confidence == 0.6

    def test_failure_with_error(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        ev = engine.evaluate_outcome("task", {"error": "crashed"})
        assert ev.success is False
        assert ev.outcome_type == "error"

    def test_no_result(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        ev = engine.evaluate_outcome("task", None)
        assert ev.success is False
        assert ev.outcome_type == "unknown"

    def test_empty_result(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        ev = engine.evaluate_outcome("task", {})
        assert ev.success is False

    def test_details_passed_through(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        ev = engine.evaluate_outcome("task", {"tests_passed": True, "details": "all green"})
        assert ev.details == "all green"


# ---------------------------------------------------------------------------
# LearningEngine.extract_patterns
# ---------------------------------------------------------------------------

class TestExtractPatterns:
    def test_success_patterns(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        ev = Evaluation(task_description="fix bug", success=True, outcome_type="tests_passed")
        patterns = engine.extract_patterns(ev)
        assert any("succeeded" in p for p in patterns)
        assert any("Tests are available" in p for p in patterns)

    def test_success_pr_approved_pattern(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        ev = Evaluation(task_description="cleanup", success=True, outcome_type="pr_approved")
        patterns = engine.extract_patterns(ev)
        assert any("PR workflow" in p for p in patterns)

    def test_failure_patterns(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        ev = Evaluation(task_description="deploy", success=False, outcome_type="error", details="timeout")
        patterns = engine.extract_patterns(ev)
        assert any("failed" in p for p in patterns)
        assert any("timeout" in p for p in patterns)

    def test_failure_no_details(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        ev = Evaluation(task_description="task", success=False, outcome_type="unknown")
        patterns = engine.extract_patterns(ev)
        assert len(patterns) == 1
        assert "failed" in patterns[0]


# ---------------------------------------------------------------------------
# LearningEngine.update_strategies
# ---------------------------------------------------------------------------

class TestUpdateStrategies:
    def test_stores_patterns(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        engine.update_strategies("owner/repo", ["pattern_a", "pattern_b"])
        data = json.loads((tmp_path / "learning" / "owner__repo.json").read_text())
        assert "pattern_a" in data["patterns"]
        assert "pattern_b" in data["patterns"]
        assert data["total_tasks"] == 1

    def test_deduplicates_patterns(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        engine.update_strategies("a/b", ["p1", "p2"])
        engine.update_strategies("a/b", ["p2", "p3"])
        data = json.loads((tmp_path / "learning" / "a__b.json").read_text())
        assert len(set(data["patterns"])) == len(data["patterns"])

    def test_increments_total_tasks(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        engine.update_strategies("a/b", ["p1"])
        engine.update_strategies("a/b", ["p2"])
        data = json.loads((tmp_path / "learning" / "a__b.json").read_text())
        assert data["total_tasks"] == 2

    def test_success_rate_calculated(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        engine.update_strategies("a/b", ["Task 'x' succeeded (outcome: tests_passed)"])
        engine.update_strategies("a/b", ["Task 'y' failed (outcome: error)"])
        data = json.loads((tmp_path / "learning" / "a__b.json").read_text())
        assert data["successful_tasks"] == 1
        assert data["total_tasks"] == 2
        assert data["success_rate"] == 0.5


# ---------------------------------------------------------------------------
# LearningEngine.get_repo_insights
# ---------------------------------------------------------------------------

class TestGetRepoInsights:
    def test_no_data(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        insights = engine.get_repo_insights("new/repo")
        assert insights.repo == "new/repo"
        assert insights.patterns == []
        assert insights.success_rate == 0.0

    def test_with_stored_data(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        engine.update_strategies("a/b", ["Task 'fix' succeeded (outcome: tests_passed)"])
        insights = engine.get_repo_insights("a/b")
        assert insights.repo == "a/b"
        assert len(insights.patterns) >= 1
        assert insights.total_tasks == 1
        assert insights.successful_tasks == 1
        assert insights.success_rate == 1.0


# ---------------------------------------------------------------------------
# LearningEngine.record_error
# ---------------------------------------------------------------------------

class TestRecordError:
    def test_records_error(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        engine.record_error("a/b", "import failed")
        insights = engine.get_repo_insights("a/b")
        assert "import failed" in insights.common_errors

    def test_deduplicates_errors(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        engine.record_error("a/b", "crash")
        engine.record_error("a/b", "crash")
        insights = engine.get_repo_insights("a/b")
        assert insights.common_errors.count("crash") == 1

    def test_caps_at_50(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        for i in range(60):
            engine.record_error("a/b", f"error_{i}")
        insights = engine.get_repo_insights("a/b")
        assert len(insights.common_errors) == 50


# ---------------------------------------------------------------------------
# LearningEngine.set_preferred_style
# ---------------------------------------------------------------------------

class TestSetPreferredStyle:
    def test_sets_style(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        engine.set_preferred_style("a/b", "indent", "4spaces")
        insights = engine.get_repo_insights("a/b")
        assert insights.preferred_style["indent"] == "4spaces"

    def test_overwrites_style(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        engine.set_preferred_style("a/b", "indent", "tabs")
        engine.set_preferred_style("a/b", "indent", "2spaces")
        insights = engine.get_repo_insights("a/b")
        assert insights.preferred_style["indent"] == "2spaces"

    def test_multiple_style_keys(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        engine.set_preferred_style("a/b", "indent", "4spaces")
        engine.set_preferred_style("a/b", "quotes", "double")
        insights = engine.get_repo_insights("a/b")
        assert insights.preferred_style["indent"] == "4spaces"
        assert insights.preferred_style["quotes"] == "double"


# ---------------------------------------------------------------------------
# Storage persistence
# ---------------------------------------------------------------------------

class TestStoragePersistence:
    def test_data_persists_across_instances(self, tmp_path):
        engine1 = LearningEngine(storage_dir=tmp_path)
        engine1.update_strategies("a/b", ["pattern_x"])
        engine1.set_preferred_style("a/b", "indent", "tabs")

        engine2 = LearningEngine(storage_dir=tmp_path)
        insights = engine2.get_repo_insights("a/b")
        assert "pattern_x" in insights.patterns
        assert insights.preferred_style["indent"] == "tabs"

    def test_corrupt_json_handled(self, tmp_path):
        engine = LearningEngine(storage_dir=tmp_path)
        repo_file = tmp_path / "learning" / "a__b.json"
        repo_file.parent.mkdir(parents=True, exist_ok=True)
        repo_file.write_text("{broken json")
        insights = engine.get_repo_insights("a/b")
        assert insights.patterns == []
