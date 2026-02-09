"""Tests for gitpilot.smart_model_router module."""
from __future__ import annotations

import pytest

from gitpilot.smart_model_router import (
    DEFAULT_MODEL_MAP,
    Complexity,
    ModelRouter,
    ModelRouterConfig,
    ModelSelection,
    TaskTier,
    UsageRecord,
)


# ---------------------------------------------------------------------------
# estimate_complexity
# ---------------------------------------------------------------------------


class TestEstimateComplexity:
    def test_high_architect(self):
        router = ModelRouter()
        assert router.estimate_complexity("architect a new microservices system") == Complexity.HIGH

    def test_high_security_audit(self):
        router = ModelRouter()
        assert router.estimate_complexity("perform a security audit on auth") == Complexity.HIGH

    def test_high_refactor(self):
        router = ModelRouter()
        assert router.estimate_complexity("refactor the database layer") == Complexity.HIGH

    def test_high_race_condition(self):
        router = ModelRouter()
        assert router.estimate_complexity("debug a race condition in worker") == Complexity.HIGH

    def test_high_rewrite(self):
        router = ModelRouter()
        assert router.estimate_complexity("rewrite the payment module") == Complexity.HIGH

    def test_low_list(self):
        router = ModelRouter()
        assert router.estimate_complexity("list open issues") == Complexity.LOW

    def test_low_status(self):
        router = ModelRouter()
        assert router.estimate_complexity("show status of the repo") == Complexity.LOW

    def test_low_rename(self):
        router = ModelRouter()
        assert router.estimate_complexity("rename variable foo to bar") == Complexity.LOW

    def test_low_git_status(self):
        router = ModelRouter()
        assert router.estimate_complexity("run git status") == Complexity.LOW

    def test_low_typo(self):
        router = ModelRouter()
        assert router.estimate_complexity("fix this typo in the readme") == Complexity.LOW

    def test_medium_default(self):
        router = ModelRouter()
        # 15+ words, no high/low keywords => MEDIUM
        assert router.estimate_complexity(
            "write a function that calculates the fibonacci sequence"
            " given an integer input and returns the full series of numbers as a result"
        ) == Complexity.MEDIUM

    def test_high_from_long_text(self):
        router = ModelRouter()
        long_text = " ".join(["word"] * 101)
        assert router.estimate_complexity(long_text) == Complexity.HIGH

    def test_low_from_short_text(self):
        router = ModelRouter()
        assert router.estimate_complexity("help me") == Complexity.LOW

    def test_high_implement_system(self):
        router = ModelRouter()
        assert router.estimate_complexity("implement auth system for the app") == Complexity.HIGH

    def test_high_memory_leak(self):
        router = ModelRouter()
        assert router.estimate_complexity("debug memory leak in the cache") == Complexity.HIGH


# ---------------------------------------------------------------------------
# select — basic flow
# ---------------------------------------------------------------------------


class TestSelect:
    def test_select_returns_model_selection(self):
        router = ModelRouter(config=ModelRouterConfig(provider="openai"))
        sel = router.select("list issues")
        assert isinstance(sel, ModelSelection)
        assert sel.provider == "openai"

    def test_select_low_complexity_uses_fast(self):
        router = ModelRouter(config=ModelRouterConfig(provider="openai"))
        sel = router.select("show status")
        assert sel.complexity == Complexity.LOW
        assert sel.tier == TaskTier.FAST
        assert sel.model == "gpt-4o-mini"

    def test_select_high_complexity_uses_powerful(self):
        router = ModelRouter(config=ModelRouterConfig(provider="openai"))
        sel = router.select("architect a distributed cache system")
        assert sel.complexity == Complexity.HIGH
        assert sel.tier == TaskTier.POWERFUL
        assert sel.model == "o1"

    def test_select_medium_complexity_uses_balanced(self):
        router = ModelRouter(config=ModelRouterConfig(provider="openai"))
        # 15+ words, no high/low keywords => MEDIUM complexity
        sel = router.select(
            "write a function to parse JSON configuration files"
            " and validate all required fields are present before returning the config object"
        )
        assert sel.complexity == Complexity.MEDIUM
        assert sel.tier == TaskTier.BALANCED
        assert sel.model == "gpt-4o"


# ---------------------------------------------------------------------------
# select — with categories
# ---------------------------------------------------------------------------


class TestSelectWithCategory:
    def test_code_review_uses_powerful(self):
        router = ModelRouter(config=ModelRouterConfig(provider="openai"))
        sel = router.select("review this PR", category="code_review")
        assert sel.tier == TaskTier.POWERFUL

    def test_issue_management_uses_fast(self):
        router = ModelRouter(config=ModelRouterConfig(provider="openai"))
        sel = router.select("create an issue for the bug", category="issue_management")
        assert sel.tier == TaskTier.FAST

    def test_pr_management_uses_balanced(self):
        router = ModelRouter(config=ModelRouterConfig(provider="openai"))
        sel = router.select("update the PR description", category="pr_management")
        assert sel.tier == TaskTier.BALANCED

    def test_high_complexity_upgrades_fast_category(self):
        """When complexity is HIGH but category maps to FAST, upgrade to BALANCED."""
        router = ModelRouter(config=ModelRouterConfig(provider="openai"))
        sel = router.select(
            "architect a new terminal integration",
            category="terminal",
        )
        assert sel.tier == TaskTier.BALANCED

    def test_unknown_category_falls_back_to_complexity(self):
        router = ModelRouter(config=ModelRouterConfig(provider="openai"))
        sel = router.select("list stuff", category="nonexistent_category")
        # Should fall through to pure complexity-based selection
        assert sel.complexity == Complexity.LOW
        assert sel.tier == TaskTier.FAST


# ---------------------------------------------------------------------------
# select — force_model
# ---------------------------------------------------------------------------


class TestForceModel:
    def test_force_model_overrides_everything(self):
        config = ModelRouterConfig(provider="openai", force_model="custom-model-v1")
        router = ModelRouter(config=config)
        sel = router.select("architect a complex distributed system")
        assert sel.model == "custom-model-v1"
        assert sel.reason == "Force model override"
        assert sel.tier == TaskTier.BALANCED
        assert sel.complexity == Complexity.MEDIUM

    def test_force_model_ignores_category(self):
        config = ModelRouterConfig(provider="openai", force_model="locked")
        router = ModelRouter(config=config)
        sel = router.select("review code", category="code_review")
        assert sel.model == "locked"


# ---------------------------------------------------------------------------
# select — category_overrides
# ---------------------------------------------------------------------------


class TestCategoryOverrides:
    def test_category_override_changes_tier(self):
        config = ModelRouterConfig(
            provider="openai",
            category_overrides={"code_review": "fast"},
        )
        router = ModelRouter(config=config)
        sel = router.select("review this", category="code_review")
        # Normally code_review is POWERFUL, but override sets it to FAST
        assert sel.tier == TaskTier.FAST
        assert sel.model == "gpt-4o-mini"

    def test_category_override_to_powerful(self):
        config = ModelRouterConfig(
            provider="openai",
            category_overrides={"conversational": "powerful"},
        )
        router = ModelRouter(config=config)
        sel = router.select("hello", category="conversational")
        assert sel.tier == TaskTier.POWERFUL
        assert sel.model == "o1"


# ---------------------------------------------------------------------------
# DEFAULT_MODEL_MAP resolution
# ---------------------------------------------------------------------------


class TestModelMapResolution:
    def test_claude_provider_models(self):
        router = ModelRouter(config=ModelRouterConfig(provider="claude"))
        sel_low = router.select("list things")
        sel_high = router.select("architect a system")
        assert sel_low.model == "claude-haiku-4-5-20251001"
        assert sel_high.model == "claude-opus-4-6"

    def test_ollama_provider_models(self):
        router = ModelRouter(config=ModelRouterConfig(provider="ollama"))
        sel = router.select("show help")
        assert sel.model == "llama3"

    def test_watsonx_provider_models(self):
        router = ModelRouter(config=ModelRouterConfig(provider="watsonx"))
        sel = router.select("design a system", category="plan_execute")
        assert sel.model == "meta-llama/llama-3-3-70b-instruct"

    def test_unknown_provider_falls_back_to_openai(self):
        router = ModelRouter(config=ModelRouterConfig(provider="gemini"))
        sel = router.select("list issues")
        # Unknown provider falls back to openai defaults
        assert sel.model == DEFAULT_MODEL_MAP["openai"]["fast"]

    def test_custom_model_map_overrides_defaults(self):
        config = ModelRouterConfig(
            provider="openai",
            model_map={"openai": {"fast": "my-fast-model"}},
        )
        router = ModelRouter(config=config)
        sel = router.select("show status")
        assert sel.model == "my-fast-model"

    def test_custom_model_map_partial(self):
        """Custom map for one tier, defaults for others."""
        config = ModelRouterConfig(
            provider="openai",
            model_map={"openai": {"powerful": "my-powerful"}},
        )
        router = ModelRouter(config=config)
        sel_powerful = router.select("architect a system")
        sel_fast = router.select("list items")
        assert sel_powerful.model == "my-powerful"
        assert sel_fast.model == "gpt-4o-mini"  # from defaults


# ---------------------------------------------------------------------------
# Budget tracking
# ---------------------------------------------------------------------------


class TestBudgetTracking:
    def test_record_usage(self):
        router = ModelRouter()
        router.record_usage(UsageRecord(model="gpt-4o", tokens_in=100, tokens_out=50, estimated_cost_usd=0.01))
        assert len(router._usage) == 1

    def test_get_total_cost(self):
        router = ModelRouter()
        router.record_usage(UsageRecord(model="gpt-4o", estimated_cost_usd=0.05))
        router.record_usage(UsageRecord(model="gpt-4o-mini", estimated_cost_usd=0.01))
        assert router.get_total_cost() == pytest.approx(0.06)

    def test_get_total_cost_empty(self):
        router = ModelRouter()
        assert router.get_total_cost() == 0.0

    def test_is_budget_exceeded_no_budget(self):
        router = ModelRouter(config=ModelRouterConfig(budget_usd=0.0))
        router.record_usage(UsageRecord(model="m", estimated_cost_usd=999.0))
        assert router.is_budget_exceeded() is False

    def test_is_budget_exceeded_under(self):
        router = ModelRouter(config=ModelRouterConfig(budget_usd=1.0))
        router.record_usage(UsageRecord(model="m", estimated_cost_usd=0.50))
        assert router.is_budget_exceeded() is False

    def test_is_budget_exceeded_over(self):
        router = ModelRouter(config=ModelRouterConfig(budget_usd=1.0))
        router.record_usage(UsageRecord(model="m", estimated_cost_usd=0.60))
        router.record_usage(UsageRecord(model="m", estimated_cost_usd=0.50))
        assert router.is_budget_exceeded() is True

    def test_is_budget_exceeded_exact(self):
        router = ModelRouter(config=ModelRouterConfig(budget_usd=1.0))
        router.record_usage(UsageRecord(model="m", estimated_cost_usd=1.0))
        assert router.is_budget_exceeded() is True


# ---------------------------------------------------------------------------
# get_usage_summary
# ---------------------------------------------------------------------------


class TestGetUsageSummary:
    def test_empty_summary(self):
        router = ModelRouter(config=ModelRouterConfig(budget_usd=5.0))
        summary = router.get_usage_summary()
        assert summary["total_cost_usd"] == 0.0
        assert summary["budget_usd"] == 5.0
        assert summary["budget_exceeded"] is False
        assert summary["models"] == {}

    def test_summary_aggregates_by_model(self):
        router = ModelRouter(config=ModelRouterConfig(budget_usd=10.0))
        router.record_usage(UsageRecord(
            model="gpt-4o", tokens_in=100, tokens_out=50, estimated_cost_usd=0.03,
        ))
        router.record_usage(UsageRecord(
            model="gpt-4o", tokens_in=200, tokens_out=100, estimated_cost_usd=0.06,
        ))
        router.record_usage(UsageRecord(
            model="gpt-4o-mini", tokens_in=500, tokens_out=200, estimated_cost_usd=0.01,
        ))

        summary = router.get_usage_summary()
        assert summary["total_cost_usd"] == pytest.approx(0.10)
        assert summary["models"]["gpt-4o"]["calls"] == 2
        assert summary["models"]["gpt-4o"]["tokens_in"] == 300
        assert summary["models"]["gpt-4o"]["tokens_out"] == 150
        assert summary["models"]["gpt-4o"]["cost"] == pytest.approx(0.09)
        assert summary["models"]["gpt-4o-mini"]["calls"] == 1

    def test_summary_budget_exceeded_flag(self):
        router = ModelRouter(config=ModelRouterConfig(budget_usd=0.05))
        router.record_usage(UsageRecord(model="m", estimated_cost_usd=0.06))
        summary = router.get_usage_summary()
        assert summary["budget_exceeded"] is True


# ---------------------------------------------------------------------------
# ModelSelection reason / explain
# ---------------------------------------------------------------------------


class TestExplain:
    def test_reason_includes_complexity(self):
        router = ModelRouter(config=ModelRouterConfig(provider="openai"))
        sel = router.select("list things")
        assert "Complexity=low" in sel.reason

    def test_reason_includes_category(self):
        router = ModelRouter(config=ModelRouterConfig(provider="openai"))
        sel = router.select("review code", category="code_review")
        assert "category=code_review" in sel.reason

    def test_reason_includes_tier(self):
        router = ModelRouter(config=ModelRouterConfig(provider="openai"))
        sel = router.select("show help")
        assert "tier=fast" in sel.reason


# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------


class TestDefaultConfig:
    def test_default_router_config(self):
        router = ModelRouter()
        assert router.config.provider == "openai"
        assert router.config.force_model is None
        assert router.config.budget_usd == 0.0
        assert router.config.category_overrides == {}
        assert router.config.model_map == {}
