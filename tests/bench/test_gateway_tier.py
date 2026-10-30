# tests/bench/test_gateway_tier.py
"""The frontier rung behind an OpenAI-compatible gateway.

The three-tier matrix needs a NATIVE tool-calling model, and the only way the
harness knew to reach one was the Anthropic API with ``ANTHROPIC_API_KEY``. A
machine with no key but a local OllaBridge fronting a hosted model could not run
the tier at all — so the model and family are configurable, the claim (NATIVE)
is not, and availability is still probed rather than assumed.
"""
from __future__ import annotations

import logging

import pytest

from bench import matrix as matrix_mod
from bench.matrix import (
    DEFAULT_FRONTIER_FAMILY,
    DEFAULT_FRONTIER_MODEL,
    FRONTIER_FAMILY_ENV,
    FRONTIER_MODEL_ENV,
    ModelTier,
    frontier_tier,
    tier_is_available,
)


@pytest.fixture
def no_override(monkeypatch):
    monkeypatch.delenv(FRONTIER_FAMILY_ENV, raising=False)
    monkeypatch.delenv(FRONTIER_MODEL_ENV, raising=False)


@pytest.mark.usefixtures("no_override")
class TestTheDefaultIsUnchanged:
    def test_without_the_variables_the_tier_is_the_anthropic_one(self):
        tier = frontier_tier()

        assert (tier.family, tier.model) == (DEFAULT_FRONTIER_FAMILY, DEFAULT_FRONTIER_MODEL)
        assert tier.expected_dialect == "native"
        assert tier.requires == "ANTHROPIC_API_KEY"

    def test_the_shipped_matrix_starts_from_that_tier(self):
        """``TIERS`` is built at import, so this pins the shape a fresh process
        gets — the same three dialects the programme has always claimed."""
        assert matrix_mod.TIERS[0].label == "frontier"
        dialects = {tier.expected_dialect for tier in matrix_mod.TIERS}
        assert dialects == {"native", "react_text", "lite"}


class TestPointingTheTierAtAGateway:
    def test_model_and_family_come_from_the_environment(self, monkeypatch):
        monkeypatch.setenv(FRONTIER_FAMILY_ENV, "ollabridge")
        monkeypatch.setenv(FRONTIER_MODEL_ENV, "provider/frontier-model")

        tier = frontier_tier()

        assert tier.label == "frontier"
        assert (tier.family, tier.model) == ("ollabridge", "provider/frontier-model")
        assert tier.expected_dialect == "native", "the claim under test does not move"
        assert tier.requires == "ollabridge", "availability is probed, not assumed"

    def test_a_model_alone_keeps_the_default_family(self, monkeypatch):
        monkeypatch.delenv(FRONTIER_FAMILY_ENV, raising=False)
        monkeypatch.setenv(FRONTIER_MODEL_ENV, "claude-opus-4-1")

        tier = frontier_tier()

        assert (tier.family, tier.model) == ("claude", "claude-opus-4-1")
        assert tier.requires == "ANTHROPIC_API_KEY"

    def test_a_family_without_a_model_is_ignored_with_a_warning(self, monkeypatch, caplog):
        """Guessing a model for an arbitrary gateway would run a tier nobody
        asked for; the default is at least the one the docs describe."""
        monkeypatch.setenv(FRONTIER_FAMILY_ENV, "ollabridge")
        monkeypatch.delenv(FRONTIER_MODEL_ENV, raising=False)

        with caplog.at_level(logging.WARNING, logger="bench.matrix"):
            tier = frontier_tier()

        assert (tier.family, tier.model) == (DEFAULT_FRONTIER_FAMILY, DEFAULT_FRONTIER_MODEL)
        assert FRONTIER_MODEL_ENV in caplog.text

    @pytest.mark.parametrize(
        ("family", "requires"),
        [("openai", "OPENAI_API_KEY"), ("ollama", "ollama"), ("openwebui", "OPENWEBUI_BASE_URL")],
    )
    def test_each_known_family_declares_what_it_needs(self, monkeypatch, family, requires):
        monkeypatch.setenv(FRONTIER_FAMILY_ENV, family)
        monkeypatch.setenv(FRONTIER_MODEL_ENV, "some-model")

        assert frontier_tier().requires == requires


class TestOllaBridgeAvailabilityIsProbed:
    def _tier(self) -> ModelTier:
        return ModelTier(
            label="frontier", family="ollabridge", model="m",
            expected_dialect="native", requires="ollabridge",
        )

    def test_the_tier_is_available_when_the_gateway_answers(self, monkeypatch):
        monkeypatch.setattr(matrix_mod, "_ollabridge_reachable", lambda: True)
        assert tier_is_available(self._tier()) is True

    def test_and_missing_when_it_does_not(self, monkeypatch):
        monkeypatch.setattr(matrix_mod, "_ollabridge_reachable", lambda: False)
        assert tier_is_available(self._tier()) is False

    def test_an_unreachable_gateway_is_not_an_exception(self, monkeypatch):
        monkeypatch.setenv("OLLABRIDGE_BASE_URL", "http://127.0.0.1:1")
        assert matrix_mod._ollabridge_reachable() is False
