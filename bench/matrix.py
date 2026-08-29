# bench/matrix.py
"""The model tiers and engines the benchmark compares — Batch V4-G4.

Three tiers, one per dialect, because the dialect is the thing under test: the
claim this programme makes is that *one* engine drives a frontier API and a 1.5B
model running locally, and a matrix with two frontier models in it would not test
that claim at all.

Availability is probed rather than assumed, and a missing tier is reported as
missing. §18's gate is per-tier, so a run that quietly covered two of three would
produce a green report for a matrix that was never run.
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


TIERS: Tuple[ModelTier, ...] = (
    ModelTier(
        label="frontier",
        family="claude",
        model="claude-sonnet-4-5",
        expected_dialect="native",
        requires="ANTHROPIC_API_KEY",
    ),
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
