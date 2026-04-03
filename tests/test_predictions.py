"""Tests for gitpilot.predictions module."""
from __future__ import annotations

import pytest

from gitpilot.predictions import (
    ActionCategory,
    PredictionRule,
    PredictiveEngine,
    SuggestedAction,
    _DEFAULT_RULES,
)


# ---------------------------------------------------------------------------
# ActionCategory enum
# ---------------------------------------------------------------------------

class TestActionCategory:
    def test_all_categories_exist(self):
        expected = {
            "testing", "documentation", "release", "review",
            "issue_management", "cleanup", "security", "deployment",
        }
        actual = {c.value for c in ActionCategory}
        assert actual == expected


# ---------------------------------------------------------------------------
# SuggestedAction
# ---------------------------------------------------------------------------

class TestSuggestedAction:
    def test_defaults(self):
        sa = SuggestedAction(
            title="T", description="D",
            category=ActionCategory.TESTING, prompt="do it",
        )
        assert sa.relevance_score == 0.5
        assert sa.auto_executable is False

    def test_to_dict(self):
        sa = SuggestedAction(
            title="Run Tests", description="Execute pytest",
            category=ActionCategory.TESTING, prompt="pytest",
            relevance_score=0.9, auto_executable=True,
        )
        d = sa.to_dict()
        assert d["title"] == "Run Tests"
        assert d["category"] == "testing"
        assert d["auto_executable"] is True
        assert d["relevance_score"] == 0.9


# ---------------------------------------------------------------------------
# Default rules
# ---------------------------------------------------------------------------

class TestDefaultRules:
    def test_eight_default_rules(self):
        assert len(_DEFAULT_RULES) == 8

    def test_all_default_rule_names(self):
        names = {r.name for r in _DEFAULT_RULES}
        assert "post_merge_changelog" in names
        assert "post_issue_create_label" in names
        assert "post_test_failure_debug" in names
        assert "post_dep_update_test" in names
        assert "pre_release_version" in names
        assert "post_edit_lint" in names
        assert "security_scan_suggestion" in names
        assert "post_commit_pr" in names


# ---------------------------------------------------------------------------
# Trigger pattern matching
# ---------------------------------------------------------------------------

class TestTriggerPatterns:
    def test_post_merge_changelog(self):
        engine = PredictiveEngine()
        results = engine.predict("Just merged the PR for feature X")
        titles = [a.title for a in results]
        assert "Update Changelog" in titles

    def test_post_issue_create_label(self):
        engine = PredictiveEngine()
        results = engine.predict("Created a new issue for the login bug")
        titles = [a.title for a in results]
        assert "Label and Assign Issue" in titles

    def test_post_test_failure_debug(self):
        engine = PredictiveEngine()
        # Use "failed ... test" to match r"\bfailed\b.*\btest\b"
        results = engine.predict("Build failed on the test suite")
        titles = [a.title for a in results]
        assert "Debug Test Failures" in titles

    def test_post_dep_update_test(self):
        engine = PredictiveEngine()
        # Use exact phrase that matches r"\bnpm update\b"
        results = engine.predict("Ran npm update to refresh packages")
        titles = [a.title for a in results]
        assert "Run Full Test Suite" in titles

    def test_pre_release_version(self):
        engine = PredictiveEngine()
        results = engine.predict("Preparing for release 2.0")
        titles = [a.title for a in results]
        assert "Version Bump & Tag" in titles

    def test_post_edit_lint(self):
        engine = PredictiveEngine()
        # Use "wrote ... file" to match r"\bwrote\b.*\bfile\b"
        results = engine.predict("Just wrote the file with updated logic")
        titles = [a.title for a in results]
        assert "Run Linter" in titles

    def test_security_scan_suggestion(self):
        engine = PredictiveEngine()
        results = engine.predict("Changed the authentication handler")
        titles = [a.title for a in results]
        assert "Security Review" in titles

    def test_post_commit_pr(self):
        engine = PredictiveEngine()
        results = engine.predict("Committed changes to the feature branch")
        titles = [a.title for a in results]
        assert "Create Pull Request" in titles

    def test_no_match(self):
        engine = PredictiveEngine()
        results = engine.predict("Just reading the README, nothing special")
        # Should have no matches for this innocuous context
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Scoring and sorting
# ---------------------------------------------------------------------------

class TestScoreAndSort:
    def test_sorted_descending(self):
        engine = PredictiveEngine()
        actions = [
            SuggestedAction(title="Low", description="", category=ActionCategory.CLEANUP, prompt="", relevance_score=0.5),
            SuggestedAction(title="High", description="", category=ActionCategory.TESTING, prompt="", relevance_score=0.95),
            SuggestedAction(title="Med", description="", category=ActionCategory.REVIEW, prompt="", relevance_score=0.7),
        ]
        sorted_actions = engine.score_and_sort(actions)
        scores = [a.relevance_score for a in sorted_actions]
        assert scores == sorted(scores, reverse=True)

    def test_filters_below_min_score(self):
        engine = PredictiveEngine(min_score=0.8)
        actions = [
            SuggestedAction(title="Low", description="", category=ActionCategory.CLEANUP, prompt="", relevance_score=0.3),
            SuggestedAction(title="High", description="", category=ActionCategory.TESTING, prompt="", relevance_score=0.9),
        ]
        results = engine.score_and_sort(actions)
        assert len(results) == 1
        assert results[0].title == "High"

    def test_min_score_default(self):
        engine = PredictiveEngine()
        assert engine.min_score == 0.5


# ---------------------------------------------------------------------------
# Cooldown mechanism
# ---------------------------------------------------------------------------

class TestCooldown:
    def test_recent_suggestion_not_repeated(self):
        engine = PredictiveEngine()
        # "merged" triggers post_merge_changelog
        results1 = engine.predict("Just merged the PR")
        assert any(a.title == "Update Changelog" for a in results1)

        results2 = engine.predict("Another PR was merged")
        # Should be suppressed by cooldown (within last 3)
        assert not any(a.title == "Update Changelog" for a in results2)

    def test_cooldown_expires_after_3_actions(self):
        engine = PredictiveEngine()
        engine.predict("Just merged the PR")  # triggers post_merge_changelog
        # Push 3 other rule triggers to push changelog out of cooldown window
        engine.predict("new issue created for tracking")
        engine.predict("Preparing for release now")
        engine.predict("Wrote the file with the fix")

        results = engine.predict("Another PR was merged today")
        assert any(a.title == "Update Changelog" for a in results)


# ---------------------------------------------------------------------------
# Custom rules
# ---------------------------------------------------------------------------

class TestCustomRules:
    def test_add_rule(self):
        engine = PredictiveEngine()
        initial_count = len(engine.rules)
        rule = PredictionRule(
            name="custom_deploy",
            trigger_patterns=[r"\bdeploy\b"],
            action=SuggestedAction(
                title="Deploy", description="Deploy to prod",
                category=ActionCategory.DEPLOYMENT, prompt="deploy now",
                relevance_score=0.99,
            ),
        )
        engine.add_rule(rule)
        assert len(engine.rules) == initial_count + 1
        # Custom rule at priority position 0
        assert engine.rules[0].name == "custom_deploy"

    def test_custom_rule_triggers(self):
        engine = PredictiveEngine()
        rule = PredictionRule(
            name="custom_deploy",
            trigger_patterns=[r"\bdeploy\b"],
            action=SuggestedAction(
                title="Deploy Now", description="Push to prod",
                category=ActionCategory.DEPLOYMENT, prompt="deploy",
                relevance_score=0.99,
            ),
        )
        engine.add_rule(rule)
        results = engine.predict("Time to deploy the application")
        assert any(a.title == "Deploy Now" for a in results)

    def test_custom_rules_from_init(self):
        custom = [
            PredictionRule(
                name="my_rule",
                trigger_patterns=[r"\bhello\b"],
                action=SuggestedAction(
                    title="Greet", description="Say hello",
                    category=ActionCategory.CLEANUP, prompt="hi",
                    relevance_score=0.9,
                ),
            ),
        ]
        engine = PredictiveEngine(custom_rules=custom)
        results = engine.predict("hello world")
        assert any(a.title == "Greet" for a in results)


# ---------------------------------------------------------------------------
# clear_history and list_rules
# ---------------------------------------------------------------------------

class TestEngineUtilities:
    def test_clear_history(self):
        engine = PredictiveEngine()
        engine.predict("Just merged the PR")
        assert len(engine._recent_suggestions) > 0
        engine.clear_history()
        assert len(engine._recent_suggestions) == 0

    def test_list_rules(self):
        engine = PredictiveEngine()
        rules = engine.list_rules()
        assert len(rules) == 8  # default rules only
        assert all("name" in r for r in rules)
        assert all("trigger_patterns" in r for r in rules)
        assert all("action_title" in r for r in rules)
        assert all("category" in r for r in rules)

    def test_list_rules_includes_custom(self):
        rule = PredictionRule(
            name="extra",
            trigger_patterns=[r"x"],
            action=SuggestedAction(
                title="X", description="", category=ActionCategory.CLEANUP, prompt="",
            ),
        )
        engine = PredictiveEngine()
        engine.add_rule(rule)
        rules = engine.list_rules()
        assert len(rules) == 9
        assert rules[0]["name"] == "extra"
