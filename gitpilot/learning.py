# gitpilot/learning.py
"""Self-improving agent learning engine.

After each task execution, evaluates outcomes, extracts patterns,
and stores them in the project's auto-memory.  Over time, GitPilot
becomes specialised to each project's patterns and conventions.

Inspired by reinforcement learning from human feedback (RLHF) principles
and the experience-replay mechanism from DeepMind's DQN (Mnih et al., 2015),
adapted for a software engineering context.

Learning loop::

    Execute task → Evaluate outcome → Extract patterns → Store in memory
                                                              ↓
    Future tasks ← Agent reads patterns from memory ← Memory updated
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

LEARNING_DIR = "learning"
MAX_INSIGHTS_PER_REPO = 200
INSIGHT_CATEGORIES = [
    "code_style",
    "testing",
    "architecture",
    "workflow",
    "error_pattern",
    "performance",
    "security",
]


@dataclass
class Evaluation:
    """Result of evaluating a task outcome."""

    task_description: str
    success: bool
    outcome_type: str = ""  # tests_passed, pr_approved, error_fixed, etc.
    details: str = ""
    confidence: float = 0.8  # 0.0 - 1.0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_description": self.task_description,
            "success": self.success,
            "outcome_type": self.outcome_type,
            "details": self.details,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }


@dataclass
class RepoInsights:
    """Accumulated insights for a repository."""

    repo: str
    patterns: List[str] = field(default_factory=list)
    preferred_style: Dict[str, str] = field(default_factory=dict)
    common_errors: List[str] = field(default_factory=list)
    success_rate: float = 0.0
    total_tasks: int = 0
    successful_tasks: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo": self.repo,
            "patterns": self.patterns,
            "preferred_style": self.preferred_style,
            "common_errors": self.common_errors,
            "success_rate": self.success_rate,
            "total_tasks": self.total_tasks,
            "successful_tasks": self.successful_tasks,
        }


class LearningEngine:
    """Learn from task execution outcomes and improve over time.

    Usage::

        engine = LearningEngine(storage_dir=Path("~/.gitpilot"))
        evaluation = engine.evaluate_outcome(
            task="Fix login bug",
            result={"tests_passed": True, "pr_approved": True},
        )
        patterns = engine.extract_patterns(evaluation)
        engine.update_strategies("owner/repo", patterns)
        insights = engine.get_repo_insights("owner/repo")
    """

    def __init__(self, storage_dir: Optional[Path] = None) -> None:
        self.storage_dir = storage_dir or (Path.home() / ".gitpilot")
        self._learning_dir = self.storage_dir / LEARNING_DIR
        self._learning_dir.mkdir(parents=True, exist_ok=True)

    def evaluate_outcome(
        self,
        task: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> Evaluation:
        """Evaluate a task outcome based on result signals.

        Checks for success signals like:
        - tests_passed: True
        - pr_approved: True
        - error_fixed: True
        - build_success: True
        """
        result = result or {}
        success_signals = [
            result.get("tests_passed", False),
            result.get("pr_approved", False),
            result.get("error_fixed", False),
            result.get("build_success", False),
        ]
        explicit_success = result.get("success")

        if explicit_success is not None:
            success = bool(explicit_success)
        else:
            success = any(success_signals)

        # Determine outcome type
        if result.get("tests_passed"):
            outcome_type = "tests_passed"
        elif result.get("pr_approved"):
            outcome_type = "pr_approved"
        elif result.get("error_fixed"):
            outcome_type = "error_fixed"
        elif result.get("error"):
            outcome_type = "error"
            success = False
        else:
            outcome_type = "completed" if success else "unknown"

        confidence = 0.9 if success else 0.6

        return Evaluation(
            task_description=task,
            success=success,
            outcome_type=outcome_type,
            details=result.get("details", ""),
            confidence=confidence,
        )

    def extract_patterns(self, evaluation: Evaluation) -> List[str]:
        """Extract learnable patterns from an evaluation.

        Generates natural-language patterns that can be injected
        into future agent system prompts.
        """
        patterns = []

        if evaluation.success:
            patterns.append(
                f"Task '{evaluation.task_description}' succeeded "
                f"(outcome: {evaluation.outcome_type})"
            )
            if evaluation.outcome_type == "tests_passed":
                patterns.append("Tests are available and should be run after changes")
            if evaluation.outcome_type == "pr_approved":
                patterns.append("PR workflow is active; create PRs for review")
        else:
            patterns.append(
                f"Task '{evaluation.task_description}' failed "
                f"(outcome: {evaluation.outcome_type})"
            )
            if evaluation.details:
                patterns.append(f"Error context: {evaluation.details[:200]}")

        return patterns

    def update_strategies(self, repo: str, patterns: List[str]) -> None:
        """Store learned patterns for a repository."""
        repo_file = self._repo_path(repo)
        data = self._load_repo_data(repo)

        existing = set(data.get("patterns", []))
        for p in patterns:
            existing.add(p)

        data["patterns"] = list(existing)[-MAX_INSIGHTS_PER_REPO:]
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        data.setdefault("total_tasks", 0)
        data["total_tasks"] += 1

        # Update success rate
        if any("succeeded" in p for p in patterns):
            data.setdefault("successful_tasks", 0)
            data["successful_tasks"] += 1

        total = data.get("total_tasks", 1)
        successful = data.get("successful_tasks", 0)
        data["success_rate"] = round(successful / total, 3) if total > 0 else 0.0

        repo_file.write_text(json.dumps(data, indent=2))

    def get_repo_insights(self, repo: str) -> RepoInsights:
        """Get accumulated insights for a repository."""
        data = self._load_repo_data(repo)
        return RepoInsights(
            repo=repo,
            patterns=data.get("patterns", []),
            preferred_style=data.get("preferred_style", {}),
            common_errors=data.get("common_errors", []),
            success_rate=data.get("success_rate", 0.0),
            total_tasks=data.get("total_tasks", 0),
            successful_tasks=data.get("successful_tasks", 0),
        )

    def record_error(self, repo: str, error: str) -> None:
        """Record a common error pattern for a repo."""
        data = self._load_repo_data(repo)
        errors = data.setdefault("common_errors", [])
        if error not in errors:
            errors.append(error)
            data["common_errors"] = errors[-50:]  # Keep last 50
            self._repo_path(repo).write_text(json.dumps(data, indent=2))

    def set_preferred_style(self, repo: str, key: str, value: str) -> None:
        """Set a preferred code style for a repo (e.g., indent: 4spaces)."""
        data = self._load_repo_data(repo)
        data.setdefault("preferred_style", {})[key] = value
        self._repo_path(repo).write_text(json.dumps(data, indent=2))

    def _repo_path(self, repo: str) -> Path:
        safe_name = repo.replace("/", "__")
        return self._learning_dir / f"{safe_name}.json"

    def _load_repo_data(self, repo: str) -> Dict[str, Any]:
        path = self._repo_path(repo)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                return {}
        return {}
