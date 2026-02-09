# gitpilot/predictions.py
"""Predictive workflow engine — suggest next actions proactively.

Analyses the current session state and recent actions to predict what
the user likely needs next.  Suggestions are scored by relevance and
presented as actionable prompts.

Based on the concept of *proactive assistance* from HCI research
(Horvitz, 1999) and GitHub's own next-action prediction patterns.

Trigger rules::

    After merging a PR     → suggest updating changelog
    After creating issue   → suggest assigning and labeling
    After test failure     → suggest debugging approach
    After dep update       → suggest full test suite
    Before release         → suggest version bump
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ActionCategory(str, Enum):
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    RELEASE = "release"
    REVIEW = "review"
    ISSUE_MGMT = "issue_management"
    CLEANUP = "cleanup"
    SECURITY = "security"
    DEPLOYMENT = "deployment"


@dataclass
class SuggestedAction:
    """A suggested next action for the user."""

    title: str
    description: str
    category: ActionCategory
    prompt: str  # Ready-to-use prompt if the user accepts
    relevance_score: float = 0.5  # 0.0 - 1.0
    auto_executable: bool = False  # Can be run without confirmation

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "category": self.category.value,
            "prompt": self.prompt,
            "relevance_score": self.relevance_score,
            "auto_executable": self.auto_executable,
        }


# ---------------------------------------------------------------------------
# Prediction rules
# ---------------------------------------------------------------------------

@dataclass
class PredictionRule:
    """A rule that fires when certain conditions are met."""

    name: str
    trigger_patterns: List[str]  # Regex patterns to match against context
    action: SuggestedAction
    cooldown_actions: int = 3  # Don't re-suggest within N actions


_DEFAULT_RULES: List[PredictionRule] = [
    PredictionRule(
        name="post_merge_changelog",
        trigger_patterns=[r"\bmerge\b.*\bpr\b", r"\bmerged\b", r"\bpull request.*merged\b"],
        action=SuggestedAction(
            title="Update Changelog",
            description="A PR was just merged. Consider updating the changelog.",
            category=ActionCategory.DOCUMENTATION,
            prompt="Update the CHANGELOG.md with the latest merged PR changes.",
            relevance_score=0.85,
        ),
    ),
    PredictionRule(
        name="post_issue_create_label",
        trigger_patterns=[r"\bcreate.*issue\b", r"\bnew issue\b", r"\bissue.*created\b"],
        action=SuggestedAction(
            title="Label and Assign Issue",
            description="A new issue was created. Consider adding labels and assignees.",
            category=ActionCategory.ISSUE_MGMT,
            prompt="Add appropriate labels and assign the newly created issue.",
            relevance_score=0.75,
        ),
    ),
    PredictionRule(
        name="post_test_failure_debug",
        trigger_patterns=[r"\btest.*fail\b", r"\bfailed\b.*\btest\b", r"\berror.*pytest\b"],
        action=SuggestedAction(
            title="Debug Test Failures",
            description="Tests failed. Let me help identify the root cause.",
            category=ActionCategory.TESTING,
            prompt="Analyze the test failures and suggest fixes for each failing test.",
            relevance_score=0.95,
        ),
    ),
    PredictionRule(
        name="post_dep_update_test",
        trigger_patterns=[r"\bdependenc\w+.*update\b", r"\bupgrade\b.*\bpackage\b", r"\bnpm update\b"],
        action=SuggestedAction(
            title="Run Full Test Suite",
            description="Dependencies were updated. Run the full test suite to verify compatibility.",
            category=ActionCategory.TESTING,
            prompt="Run the complete test suite to verify no regressions from the dependency update.",
            relevance_score=0.90,
        ),
    ),
    PredictionRule(
        name="pre_release_version",
        trigger_patterns=[r"\brelease\b", r"\bversion bump\b", r"\btag\b.*\brelease\b"],
        action=SuggestedAction(
            title="Version Bump & Tag",
            description="Preparing a release. Consider bumping the version number.",
            category=ActionCategory.RELEASE,
            prompt="Bump the version number, update the changelog, and create a release tag.",
            relevance_score=0.80,
        ),
    ),
    PredictionRule(
        name="post_edit_lint",
        trigger_patterns=[r"\bedit\b.*\bfile\b", r"\bmodif\w+\b.*\bcode\b", r"\bwrote\b.*\bfile\b"],
        action=SuggestedAction(
            title="Run Linter",
            description="Files were modified. Run the linter to check for style issues.",
            category=ActionCategory.CLEANUP,
            prompt="Run the project linter on the modified files and fix any issues.",
            relevance_score=0.65,
            auto_executable=True,
        ),
    ),
    PredictionRule(
        name="security_scan_suggestion",
        trigger_patterns=[r"\bauth\w*\b", r"\bpassword\b", r"\bsecret\b", r"\btoken\b.*\bhandl\b"],
        action=SuggestedAction(
            title="Security Review",
            description="Security-sensitive code was touched. Consider a security review.",
            category=ActionCategory.SECURITY,
            prompt="Review the security-sensitive changes for potential vulnerabilities.",
            relevance_score=0.88,
        ),
    ),
    PredictionRule(
        name="post_commit_pr",
        trigger_patterns=[r"\bcommit\w*\b.*\bbranch\b", r"\bpush\w*\b.*\bfeature\b"],
        action=SuggestedAction(
            title="Create Pull Request",
            description="Changes were committed to a feature branch. Create a PR for review.",
            category=ActionCategory.REVIEW,
            prompt="Create a pull request for the current feature branch with a description of changes.",
            relevance_score=0.70,
        ),
    ),
]


class PredictiveEngine:
    """Predict what the user needs next based on context.

    Usage::

        engine = PredictiveEngine()
        suggestions = engine.predict("Tests failed in auth module")
        for s in suggestions:
            print(f"[{s.relevance_score}] {s.title}: {s.prompt}")
    """

    def __init__(
        self,
        custom_rules: Optional[List[PredictionRule]] = None,
        min_score: float = 0.5,
    ) -> None:
        self.rules = (custom_rules or []) + _DEFAULT_RULES
        self.min_score = min_score
        self._recent_suggestions: List[str] = []

    def predict(self, context: str) -> List[SuggestedAction]:
        """Predict next actions based on the given context string.

        The context can be:
        - The last user message
        - A summary of recent session activity
        - An agent's output/result
        """
        matches: List[SuggestedAction] = []
        context_lower = context.lower()

        for rule in self.rules:
            # Skip recently suggested actions
            if rule.name in self._recent_suggestions[-3:]:
                continue
            for pattern in rule.trigger_patterns:
                if re.search(pattern, context_lower):
                    matches.append(rule.action)
                    self._recent_suggestions.append(rule.name)
                    break

        return self.score_and_sort(matches)

    def score_and_sort(
        self, actions: List[SuggestedAction],
    ) -> List[SuggestedAction]:
        """Filter by minimum score and sort by relevance (descending)."""
        filtered = [a for a in actions if a.relevance_score >= self.min_score]
        return sorted(filtered, key=lambda a: a.relevance_score, reverse=True)

    def add_rule(self, rule: PredictionRule) -> None:
        """Add a custom prediction rule."""
        self.rules.insert(0, rule)  # Custom rules take priority

    def clear_history(self) -> None:
        """Clear the recent suggestion history."""
        self._recent_suggestions.clear()

    def list_rules(self) -> List[Dict[str, Any]]:
        """List all prediction rules."""
        return [
            {
                "name": r.name,
                "trigger_patterns": r.trigger_patterns,
                "action_title": r.action.title,
                "category": r.action.category.value,
            }
            for r in self.rules
        ]
