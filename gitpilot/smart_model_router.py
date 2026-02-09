# gitpilot/smart_model_router.py
"""Smart multi-model routing for GitPilot.

Routes different tasks to different LLM models based on complexity,
task type, and cost constraints.  This allows GitPilot to use cheap
models for simple tasks and powerful models for complex reasoning.

Complexity is estimated from the request text using heuristics:

- **low**    — simple queries, listings, status checks → fast/cheap model
- **medium** — code generation, edits, reviews → balanced model
- **high**   — complex reasoning, architecture, security analysis → strongest model

The router respects a configurable cost budget and tracks usage.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskTier(str, Enum):
    """Maps complexity to model tier."""

    FAST = "fast"      # cheap, low-latency
    BALANCED = "balanced"  # good quality/cost ratio
    POWERFUL = "powerful"  # strongest available


# Default model mapping per provider + tier
DEFAULT_MODEL_MAP: Dict[str, Dict[str, str]] = {
    "openai": {
        "fast": "gpt-4o-mini",
        "balanced": "gpt-4o",
        "powerful": "o1",
    },
    "claude": {
        "fast": "claude-haiku-4-5-20251001",
        "balanced": "claude-sonnet-4-5-20250929",
        "powerful": "claude-opus-4-6",
    },
    "ollama": {
        "fast": "llama3",
        "balanced": "llama3",
        "powerful": "llama3:70b",
    },
    "watsonx": {
        "fast": "meta-llama/llama-3-1-8b-instruct",
        "balanced": "meta-llama/llama-3-3-70b-instruct",
        "powerful": "meta-llama/llama-3-3-70b-instruct",
    },
}

# Patterns for complexity estimation
_HIGH_COMPLEXITY_PATTERNS = [
    re.compile(r"\b(architect|design|refactor|migrate|security audit|threat model)\b", re.I),
    re.compile(r"\b(analyze .*across|cross-repo|monorepo|dependency graph)\b", re.I),
    re.compile(r"\b(implement .*system|build .*from scratch|rewrite)\b", re.I),
    re.compile(r"\b(debug.*complex|race condition|memory leak|deadlock)\b", re.I),
]

_LOW_COMPLEXITY_PATTERNS = [
    re.compile(r"\b(list|show|status|version|help|what is|how do)\b", re.I),
    re.compile(r"\b(rename|typo|comment|format|lint)\b", re.I),
    re.compile(r"\b(git status|git log|git diff)\b", re.I),
]

# Category -> default tier
_CATEGORY_TIER_MAP: Dict[str, TaskTier] = {
    "plan_execute": TaskTier.POWERFUL,
    "issue_management": TaskTier.FAST,
    "pr_management": TaskTier.BALANCED,
    "code_search": TaskTier.FAST,
    "code_review": TaskTier.POWERFUL,
    "learning": TaskTier.BALANCED,
    "conversational": TaskTier.FAST,
    "local_edit": TaskTier.BALANCED,
    "terminal": TaskTier.FAST,
}


@dataclass
class ModelSelection:
    """Result of model selection."""

    model: str
    tier: TaskTier
    complexity: Complexity
    provider: str
    reason: str


@dataclass
class UsageRecord:
    """Track model usage for budgeting."""

    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    estimated_cost_usd: float = 0.0


@dataclass
class ModelRouterConfig:
    """Configuration for the model router."""

    provider: str = "openai"
    model_map: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # Override: force a specific model for all tasks
    force_model: Optional[str] = None
    # Budget: max estimated cost per session (USD, 0 = unlimited)
    budget_usd: float = 0.0
    # Override tier for specific categories
    category_overrides: Dict[str, str] = field(default_factory=dict)


class ModelRouter:
    """Route tasks to optimal models based on complexity and cost.

    Usage::

        router = ModelRouter(config=ModelRouterConfig(provider="openai"))
        selection = router.select("Implement authentication system")
        # → ModelSelection(model="o1", tier=POWERFUL, complexity=HIGH)

        selection = router.select("list open issues")
        # → ModelSelection(model="gpt-4o-mini", tier=FAST, complexity=LOW)
    """

    def __init__(self, config: Optional[ModelRouterConfig] = None) -> None:
        self.config = config or ModelRouterConfig()
        self._usage: List[UsageRecord] = []

    def select(
        self,
        request: str,
        category: Optional[str] = None,
    ) -> ModelSelection:
        """Select the best model for a given request.

        Args:
            request: The user's request text.
            category: Optional RequestCategory value (e.g. "code_review").
        """
        # Force model override
        if self.config.force_model:
            return ModelSelection(
                model=self.config.force_model,
                tier=TaskTier.BALANCED,
                complexity=Complexity.MEDIUM,
                provider=self.config.provider,
                reason="Force model override",
            )

        complexity = self.estimate_complexity(request)
        tier = self._select_tier(complexity, category)
        model = self._resolve_model(tier)

        return ModelSelection(
            model=model,
            tier=tier,
            complexity=complexity,
            provider=self.config.provider,
            reason=self._explain(complexity, tier, category),
        )

    def estimate_complexity(self, request: str) -> Complexity:
        """Estimate the complexity of a request using heuristics."""
        text = request.strip()

        # Check high complexity patterns
        for pattern in _HIGH_COMPLEXITY_PATTERNS:
            if pattern.search(text):
                return Complexity.HIGH

        # Check low complexity patterns
        for pattern in _LOW_COMPLEXITY_PATTERNS:
            if pattern.search(text):
                return Complexity.LOW

        # Length-based heuristic
        word_count = len(text.split())
        if word_count > 100:
            return Complexity.HIGH
        if word_count < 15:
            return Complexity.LOW

        return Complexity.MEDIUM

    def record_usage(self, record: UsageRecord) -> None:
        """Record a model usage for budget tracking."""
        self._usage.append(record)

    def get_total_cost(self) -> float:
        """Get total estimated cost across all recorded usage."""
        return sum(r.estimated_cost_usd for r in self._usage)

    def is_budget_exceeded(self) -> bool:
        """Check if the session budget has been exceeded."""
        if self.config.budget_usd <= 0:
            return False
        return self.get_total_cost() >= self.config.budget_usd

    def get_usage_summary(self) -> Dict[str, Any]:
        """Get a summary of model usage."""
        by_model: Dict[str, Dict[str, Any]] = {}
        for r in self._usage:
            if r.model not in by_model:
                by_model[r.model] = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0}
            by_model[r.model]["calls"] += 1
            by_model[r.model]["tokens_in"] += r.tokens_in
            by_model[r.model]["tokens_out"] += r.tokens_out
            by_model[r.model]["cost"] += r.estimated_cost_usd
        return {
            "total_cost_usd": self.get_total_cost(),
            "budget_usd": self.config.budget_usd,
            "budget_exceeded": self.is_budget_exceeded(),
            "models": by_model,
        }

    def _select_tier(self, complexity: Complexity, category: Optional[str]) -> TaskTier:
        # Check category overrides first
        if category and category in self.config.category_overrides:
            return TaskTier(self.config.category_overrides[category])

        # Check category defaults
        if category and category in _CATEGORY_TIER_MAP:
            category_tier = _CATEGORY_TIER_MAP[category]
            # Upgrade tier based on complexity
            if complexity == Complexity.HIGH and category_tier == TaskTier.FAST:
                return TaskTier.BALANCED
            return category_tier

        # Pure complexity-based
        return {
            Complexity.LOW: TaskTier.FAST,
            Complexity.MEDIUM: TaskTier.BALANCED,
            Complexity.HIGH: TaskTier.POWERFUL,
        }[complexity]

    def _resolve_model(self, tier: TaskTier) -> str:
        # Check user-configured model map first
        provider = self.config.provider
        if self.config.model_map.get(provider, {}).get(tier.value):
            return self.config.model_map[provider][tier.value]
        # Fall back to defaults
        provider_models = DEFAULT_MODEL_MAP.get(provider, DEFAULT_MODEL_MAP["openai"])
        return provider_models.get(tier.value, provider_models["balanced"])

    def _explain(
        self, complexity: Complexity, tier: TaskTier, category: Optional[str],
    ) -> str:
        parts = [f"Complexity={complexity.value}"]
        if category:
            parts.append(f"category={category}")
        parts.append(f"→ tier={tier.value}")
        return ", ".join(parts)
