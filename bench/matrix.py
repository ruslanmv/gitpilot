# bench/matrix.py
"""The model tiers and engines the benchmark compares — Batch V4-G4.

Three tiers, one per dialect, because the dialect is the thing under test: the
claim this programme makes is that *one* engine drives a frontier API and a 1.5B
model running locally, and a matrix with two frontier models in it would not test
that claim at all.

Availability is probed rather than assumed, and a missing tier is reported as
missing. §18's gate is per-tier, so a run that quietly covered two of three would
produce a green report for a matrix that was never run.

The frontier rung defaults to the Anthropic API and can be pointed at any
OpenAI-compatible gateway instead (:func:`frontier_tier`)::

    export GITPILOT_PROVIDER=ollabridge
    export GITPILOT_BENCH_FRONTIER_FAMILY=ollabridge
    export GITPILOT_BENCH_FRONTIER_MODEL=openwebui/ext-claude-standard
    python -m bench --tiers frontier
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: The two engines under comparison.  ``legacy`` is the CrewAI dispatch that has
#: always served these topologies; ``loop`` is :class:`~gitpilot.agent.loop.AgentLoop`.
ENGINES: Tuple[str, ...] = ("legacy", "loop")


@dataclass(frozen=True)
class ModelTier:
    """One rung of the matrix."""

    label: str
    family: str
    model: str
    #: The dialect this model's profile should resolve to.  Asserted rather than
    #: configured: if ``llama3:8b`` stops resolving to REACT_TEXT, the matrix is no
    #: longer testing the three dialects and the benchmark should say so.
    expected_dialect: str
    #: What the tier needs before it can run at all.
    requires: str = ""

    @property
    def id(self) -> str:
        return self.label


#: The frontier rung can be served by any OpenAI-compatible gateway instead of
#: the Anthropic API — a local OllaBridge fronting a hosted Claude, say. The claim
#: under test does not change (a NATIVE tool-calling model drives the loop); only
#: where the requests go does, and ``GITPILOT_PROVIDER`` has to agree with the
#: family, because :func:`gitpilot.agent.providers.build_provider` reads the
#: configured provider and only takes the model name from the tier.
FRONTIER_FAMILY_ENV = "GITPILOT_BENCH_FRONTIER_FAMILY"
FRONTIER_MODEL_ENV = "GITPILOT_BENCH_FRONTIER_MODEL"
DEFAULT_FRONTIER_FAMILY = "claude"
DEFAULT_FRONTIER_MODEL = "claude-sonnet-4-5"

#: What each provider family needs before its tier can run: an environment
#: variable that must be set, or a service that is probed (see
#: :func:`tier_is_available`).  A family not listed here is assumed reachable,
#: which is the one place this module assumes rather than probes — and it only
#: happens when someone has explicitly pointed the tier somewhere unusual.
_FAMILY_REQUIREMENTS = {
    "claude": "ANTHROPIC_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "ollama": "ollama",
    "ollabridge": "ollabridge",
    "openwebui": "OPENWEBUI_BASE_URL",
}


def frontier_tier() -> ModelTier:
    """The frontier rung, as the environment configures it.

    ``GITPILOT_BENCH_FRONTIER_MODEL`` names the model and
    ``GITPILOT_BENCH_FRONTIER_FAMILY`` the provider family (default ``claude``).
    A family without a model is ignored with a warning rather than guessed at —
    a tier that silently ran the wrong model would be worse than the default.
    """
    family = (os.environ.get(FRONTIER_FAMILY_ENV) or "").strip().lower()
    model = (os.environ.get(FRONTIER_MODEL_ENV) or "").strip()

    if family and not model:
        logger.warning(
            "%s=%r is set but %s is not; running the default frontier tier",
            FRONTIER_FAMILY_ENV, family, FRONTIER_MODEL_ENV,
        )
        family = ""
    if not model:
        family, model = DEFAULT_FRONTIER_FAMILY, DEFAULT_FRONTIER_MODEL
    family = family or DEFAULT_FRONTIER_FAMILY

    return ModelTier(
        label="frontier",
        family=family,
        model=model,
        expected_dialect="native",
        requires=_FAMILY_REQUIREMENTS.get(family, ""),
    )


TIERS: Tuple[ModelTier, ...] = (
    frontier_tier(),
    ModelTier(
        label="local_8b",
        family="ollama",
        model="llama3:8b",
        expected_dialect="react_text",
        requires="ollama",
    ),
    ModelTier(
        label="local_small",
        family="ollama",
        model="qwen2.5:1.5b",
        expected_dialect="lite",
        requires="ollama",
    ),
)


def tier_for(label: str) -> Optional[ModelTier]:
    for tier in TIERS:
        if tier.label == label:
            return tier
    return None


def tier_is_available(tier: ModelTier) -> bool:
    """Whether this tier can actually be run here."""
    if tier.requires == "ollama":
        return _ollama_reachable()
    if tier.requires == "ollabridge":
        return _ollabridge_reachable()
    if tier.requires:
        return bool(os.environ.get(tier.requires))
    return True


def available_tiers() -> List[ModelTier]:
    return [tier for tier in TIERS if tier_is_available(tier)]


def missing_tiers() -> List[ModelTier]:
    return [tier for tier in TIERS if not tier_is_available(tier)]


def _ollama_reachable() -> bool:
    import urllib.error
    import urllib.request

    base = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=1.5):  # noqa: S310
            return True
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("ollama not reachable at %s (%s)", base, exc)
        return False


def _ollabridge_reachable() -> bool:
    import urllib.error
    import urllib.request

    base = os.environ.get("OLLABRIDGE_BASE_URL", "http://localhost:11435").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=1.5):  # noqa: S310
            return True
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("ollabridge not reachable at %s (%s)", base, exc)
        return False


def cells(
    *,
    tiers: Optional[List[ModelTier]] = None,
    engines: Optional[Tuple[str, ...]] = None,
    task_ids: Optional[List[str]] = None,
) -> Iterator[Tuple[ModelTier, str, str]]:
    """Every ``(tier, engine, task_id)`` the run should cover.

    Ordered tier-major so a partial run still produces complete tiers — a
    half-finished matrix that covered every tier's first two tasks would satisfy no
    per-tier comparison at all.
    """
    from .tasks import tasks_for_tier

    for tier in (tiers if tiers is not None else list(TIERS)):
        allowed = [task.id for task in tasks_for_tier(tier.expected_dialect)]
        for engine in (engines or ENGINES):
            for task_id in (task_ids or allowed):
                if task_id in allowed:
                    yield tier, engine, task_id
