"""The comparator — Batch V4-A2.

A :class:`ParityCase` names one behaviour and two ways of invoking it: the
legacy CrewAI tool, and the canonical registry tool.  :func:`run_case` runs both
against the same fixture and reports whether the strings a model would see are
identical.

Cases are contributed by the tool batches (A3–A6) into :data:`CASES`; this
module owns only the mechanism.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from gitpilot.toolkit import ToolCall, ToolExecutionContext, ToolRegistry
from gitpilot.workspace import WorkspaceInfo


class ParityMismatch(AssertionError):
    """Raised when the registry's output differs from the legacy tool's."""


@dataclass(frozen=True)
class ParityCase:
    """One legacy/registry comparison.

    ``legacy`` receives the prepared :class:`WorkspaceInfo` and returns the
    string the CrewAI tool produced.  ``tool``/``arguments`` describe the
    equivalent registry call.  ``normalise`` exists for outputs that legitimately
    carry run-specific noise (a duration, a commit sha) — it is applied to both
    sides, so it can hide noise but never a real difference in shape.
    """

    name: str
    tool: str
    arguments: Dict[str, Any]
    legacy: Callable[[WorkspaceInfo], str]
    normalise: Optional[Callable[[str], str]] = None
    #: Set when a case must run before another mutates the fixture.
    order: int = 100


@dataclass
class ParityOutcome:
    name: str
    tool: str
    legacy_output: str
    registry_output: str
    matched: bool
    normalised_legacy: str = ""
    normalised_registry: str = ""


#: Populated by the tool batches.  Kept as a plain list so each batch appends
#: in its own module without a registration ceremony.
CASES: List[ParityCase] = []


def register_cases(*cases: ParityCase) -> None:
    """Add cases to the shared corpus, rejecting duplicate names."""
    existing = {case.name for case in CASES}
    for case in cases:
        if case.name in existing:
            raise ValueError(f"duplicate parity case {case.name!r}")
        CASES.append(case)
        existing.add(case.name)


def run_case(
    case: ParityCase,
    ws: WorkspaceInfo,
    registry: ToolRegistry,
    ctx: ToolExecutionContext,
) -> ParityOutcome:
    """Invoke both sides of *case* and compare what a model would read."""
    legacy_output = case.legacy(ws)

    result = asyncio.run(
        registry.execute(
            ToolCall(id=f"parity-{case.name}", tool=case.tool, arguments=dict(case.arguments)),
            ctx,
        )
    )
    registry_output = result.content

    normalise = case.normalise or (lambda text: text)
    left, right = normalise(legacy_output), normalise(registry_output)

    return ParityOutcome(
        name=case.name,
        tool=case.tool,
        legacy_output=legacy_output,
        registry_output=registry_output,
        matched=left == right,
        normalised_legacy=left,
        normalised_registry=right,
    )


def assert_parity(outcome: ParityOutcome) -> None:
    if outcome.matched:
        return
    raise ParityMismatch(
        f"{outcome.name} ({outcome.tool}) diverged.\n"
        f"--- legacy ---\n{outcome.normalised_legacy!r}\n"
        f"--- registry ---\n{outcome.normalised_registry!r}"
    )


def record_corpus(outcomes: List[ParityOutcome], destination: Path) -> Path:
    """Write a reviewable snapshot of every comparison.

    Not the gate — the differential comparison is.  This is for the human
    reading a PR who wants to see what the model will now be shown.

    Normalised strings are recorded, not raw ones: a wall-clock duration in a
    raw sandbox output would rewrite this file on every run, and a committed
    artifact that churns stops being read.  The normalised text is what the gate
    compares, so it is also what a reviewer should be looking at.
    """
    payload = [
        {
            "name": o.name,
            "tool": o.tool,
            "matched": o.matched,
            "legacy": o.normalised_legacy,
            "registry": o.normalised_registry,
        }
        for o in sorted(outcomes, key=lambda o: o.name)
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return destination
