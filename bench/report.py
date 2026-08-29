# bench/report.py
"""Aggregating the matrix, and the §18 gate — Batch V4-G4.

The gate is the part that matters, and it is deliberately not "the loop wins".

    Per model tier: the loop's success rate must be **at least** legacy's, and its
    wall time and total tokens must be within **1.5×** of legacy's.

Requiring the loop to win on all three would block a change that trades some
latency for a lot of capability, which is the trade this programme is making — a
run that reads four files before editing one costs more than a run that edits
blind, and is worth it. Requiring only success rate would let the loop pass while
being ten times slower on a 1.5B model, which is the case the LITE dialect exists
to serve.

Two ways this refuses to produce a green report:

**A tier with no data is ``incomplete``, never ``pass``.** The failure mode that
guards against is running two tiers, seeing green, and flipping the default for the
third.

**A tier where legacy could not run at all is ``incomplete`` too.** "The loop won
because legacy scored zero on a machine where it cannot run" is not evidence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .runner import CellRun

#: Public alias — a cell's result as the report speaks of it.
CellResult = CellRun

#: How much slower and how much more expensive the loop may be, per tier, and
#: still pass. From the Phase G exit criteria.
REGRESSION_BUDGET = 1.5

PASS = "pass"
FAIL = "fail"
INCOMPLETE = "incomplete"


@dataclass
class EngineSummary:
    """One engine's numbers within one tier."""

    engine: str
    attempted: int = 0
    succeeded: int = 0
    unavailable: int = 0
    skipped: int = 0
    wall_time_s: float = 0.0
    total_tokens: int = 0
    approvals: int = 0
    denials: int = 0

    @property
    def success_rate(self) -> float:
        return (self.succeeded / self.attempted) if self.attempted else 0.0

    @property
    def mean_wall_time_s(self) -> float:
        return (self.wall_time_s / self.attempted) if self.attempted else 0.0

    @property
    def mean_tokens(self) -> float:
        return (self.total_tokens / self.attempted) if self.attempted else 0.0

    @property
    def ran(self) -> bool:
        return self.attempted > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine": self.engine,
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "unavailable": self.unavailable,
            "skipped": self.skipped,
            "success_rate": round(self.success_rate, 4),
            "mean_wall_time_s": round(self.mean_wall_time_s, 3),
            "mean_tokens": round(self.mean_tokens, 1),
            "approvals": self.approvals,
            "denials": self.denials,
        }


@dataclass
class TierSummary:
    """Both engines within one tier, and the verdict for that tier."""

    tier: str
    engines: Dict[str, EngineSummary] = field(default_factory=dict)

    @property
    def legacy(self) -> Optional[EngineSummary]:
        return self.engines.get("legacy")

    @property
    def loop(self) -> Optional[EngineSummary]:
        return self.engines.get("loop")

    def verdict(self, budget: float = REGRESSION_BUDGET) -> str:
        legacy, loop = self.legacy, self.loop
        if legacy is None or loop is None or not legacy.ran or not loop.ran:
            return INCOMPLETE
        if loop.success_rate < legacy.success_rate:
            return FAIL
        if not _within(loop.mean_wall_time_s, legacy.mean_wall_time_s, budget):
            return FAIL
        return PASS if _within(loop.mean_tokens, legacy.mean_tokens, budget) else FAIL

    def reasons(self, budget: float = REGRESSION_BUDGET) -> List[str]:
        """Why this tier is not a pass, in the words the report prints."""
        legacy, loop = self.legacy, self.loop
        out: List[str] = []
        if legacy is None or not legacy.ran:
            out.append("legacy produced no cells for this tier")
        if loop is None or not loop.ran:
            out.append("loop produced no cells for this tier")
        if legacy is None or loop is None or not legacy.ran or not loop.ran:
            return out

        if loop.success_rate < legacy.success_rate:
            out.append(
                f"success rate {loop.success_rate:.0%} < legacy {legacy.success_rate:.0%}"
            )
        if not _within(loop.mean_wall_time_s, legacy.mean_wall_time_s, budget):
            out.append(
                f"wall time {loop.mean_wall_time_s:.1f}s vs "
                f"{legacy.mean_wall_time_s:.1f}s (budget {budget}×)"
            )
        if not _within(loop.mean_tokens, legacy.mean_tokens, budget):
            out.append(
                f"tokens {loop.mean_tokens:.0f} vs {legacy.mean_tokens:.0f} "
                f"(budget {budget}×)"
            )
        return out

    def to_dict(self, budget: float = REGRESSION_BUDGET) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "verdict": self.verdict(budget),
            "reasons": self.reasons(budget),
            "engines": {name: summary.to_dict() for name, summary in self.engines.items()},
        }


@dataclass
class Report:
    """The whole matrix."""

    cells: List[CellRun] = field(default_factory=list)
    tiers: Dict[str, TierSummary] = field(default_factory=dict)
    #: Tiers the matrix should have covered but could not run here.
    missing: List[str] = field(default_factory=list)

    def verdict(self, budget: float = REGRESSION_BUDGET) -> str:
        if self.missing:
            return INCOMPLETE
        if not self.tiers:
            return INCOMPLETE
        verdicts = {summary.verdict(budget) for summary in self.tiers.values()}
        if FAIL in verdicts:
            return FAIL
        if INCOMPLETE in verdicts:
            return INCOMPLETE
        return PASS

    def by_category(self) -> Dict[str, Dict[str, EngineSummary]]:
        """Success per category, so a regression in one kind of work is visible.

        An average over thirteen tasks can hide "every edit got worse" behind "the
        reads got better", and the reads are the part the old engine was fine at.
        """
        out: Dict[str, Dict[str, EngineSummary]] = {}
        for cell in self.cells:
            if cell.unavailable or cell.skipped:
                continue
            bucket = out.setdefault(cell.category or "uncategorised", {})
            summary = bucket.setdefault(cell.engine, EngineSummary(engine=cell.engine))
            summary.attempted += 1
            summary.succeeded += int(cell.ok)
        return out

    def to_dict(self, budget: float = REGRESSION_BUDGET) -> Dict[str, Any]:
        return {
            "verdict": self.verdict(budget),
            "budget": budget,
            "missing_tiers": list(self.missing),
            "tiers": [summary.to_dict(budget) for summary in self.tiers.values()],
            "categories": {
                category: {name: summary.to_dict() for name, summary in engines.items()}
                for category, engines in self.by_category().items()
            },
            "cells": [cell.to_dict() for cell in self.cells],
        }

    def write(self, path: Path, budget: float = REGRESSION_BUDGET) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(budget), indent=2), encoding="utf-8")
        return path


def _within(candidate: float, baseline: float, budget: float) -> bool:
    """Whether *candidate* is inside *budget* × *baseline*.

    A zero baseline means the metric was never observed for legacy — the legacy
    path keeps no journal, so its token counts are genuinely unknown rather than
    zero. Treating unknown as "budget of zero" would fail every comparison on a
    technicality, so an unobserved baseline does not constrain.
    """
    if baseline <= 0:
        return True
    return candidate <= baseline * budget


def summarise(cells: Sequence[CellRun], *, missing: Optional[Sequence[str]] = None) -> Report:
    """Aggregate cells into per-tier summaries."""
    report = Report(cells=list(cells), missing=[str(name) for name in (missing or [])])
    for cell in cells:
        tier = report.tiers.setdefault(cell.tier, TierSummary(tier=cell.tier))
        summary = tier.engines.setdefault(cell.engine, EngineSummary(engine=cell.engine))
        if cell.unavailable:
            summary.unavailable += 1
            continue
        if cell.skipped:
            summary.skipped += 1
            continue
        summary.attempted += 1
        summary.succeeded += int(cell.ok)
        summary.wall_time_s += cell.metrics.wall_time_s
        summary.total_tokens += cell.metrics.total_tokens
        summary.approvals += cell.metrics.approvals
        summary.denials += cell.metrics.denials
    return report


def meets_gate(report: Report, *, budget: float = REGRESSION_BUDGET) -> bool:
    """Whether this report clears the Phase G exit criteria.

    ``True`` only for a complete matrix in which every tier passes. Anything else —
    a failure, a missing tier, an engine that could not run — is ``False``, because
    the question this answers is "may the default flip?" and the honest answer to
    an incomplete matrix is no.
    """
    return report.verdict(budget) == PASS


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_table(report: Report, *, budget: float = REGRESSION_BUDGET) -> str:
    """The console report."""
    lines: List[str] = []
    header = f"{'tier':<14} {'engine':<8} {'ok':>7} {'rate':>6} {'wall':>8} {'tokens':>8} {'ask':>4}"
    lines.append(header)
    lines.append("-" * len(header))

    for tier_name, tier in report.tiers.items():
        for engine_name in ("legacy", "loop"):
            summary = tier.engines.get(engine_name)
            if summary is None:
                continue
            if not summary.ran:
                lines.append(
                    f"{tier_name:<14} {engine_name:<8} "
                    f"{'—':>7} {'—':>6} {'—':>8} {'—':>8} {'—':>4}"
                    f"   ({summary.unavailable} unavailable, {summary.skipped} skipped)"
                )
                continue
            lines.append(
                f"{tier_name:<14} {engine_name:<8} "
                f"{summary.succeeded:>3}/{summary.attempted:<3} "
                f"{summary.success_rate:>5.0%} "
                f"{summary.mean_wall_time_s:>7.1f}s "
                f"{summary.mean_tokens:>8.0f} "
                f"{summary.approvals:>4}"
            )
        verdict = tier.verdict(budget)
        reasons = tier.reasons(budget)
        suffix = f" — {'; '.join(reasons)}" if reasons else ""
        lines.append(f"{'':<14} → {verdict}{suffix}")
        lines.append("")

    categories = report.by_category()
    if categories:
        lines.append("by category")
        lines.append("-" * 11)
        for category, engines in categories.items():
            parts = [
                f"{name} {summary.succeeded}/{summary.attempted}"
                for name, summary in sorted(engines.items())
            ]
            lines.append(f"  {category:<12} {'   '.join(parts)}")
        lines.append("")

    if report.missing:
        lines.append(f"missing tiers: {', '.join(report.missing)}")
    lines.append(f"gate: {report.verdict(budget)} (budget {budget}×)")
    return "\n".join(lines)


def load(path: Path) -> Dict[str, Any]:
    """Read a previously written report.  Used by the Batch V4-G5 flip check."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a benchmark report")
    return data
