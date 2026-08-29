# bench/__init__.py
"""The legacy-vs-loop benchmark harness — Batch V4-G4.

Phase G ends by flipping ``default`` to the agentic engine, and §18 makes that a
*measured* decision rather than a confident one. This is what does the measuring:
a matrix of tasks × model tiers × engines, reporting success rate, wall time,
tokens and approval count for each cell.

Three decisions in here are worth stating, because they are what separate a gate
from a demo.

**Every task is machine-checked.** A benchmark whose scoring is a human reading
answers cannot gate a release — it gates on whoever read it that day. Each task in
:mod:`bench.tasks` carries a predicate over the workspace and the answer, and a
task that cannot be checked without judgement does not belong in the set.

**The gate is not dominance.** The loop must match legacy on success rate and stay
within a 1.5× budget on wall time and tokens *per model tier*. Requiring it to win
on all three would block a change that trades a little latency for a lot of
capability, which is exactly the trade this programme is making. See
:func:`bench.report.meets_gate`.

**Absent data is not a pass.** A tier with no results reports ``incomplete``, and
an incomplete report cannot satisfy the gate. The failure mode this avoids is
running two tiers, seeing green, and flipping the default for the third.

Not shipped in the wheel (``pyproject`` packages ``gitpilot*`` only): this is a
development harness, run from a checkout as ``python -m bench``. It needs real
models to produce real numbers — Ollama for the local tiers, an API key for the
frontier one — and refuses to invent them when they are missing.

Distinct from ``tests/benchmark_agents.py``, which measures code-generation volume
against a growing project and can simulate its own data. This one compares two
engines on fixed tasks and cannot simulate anything.
"""
from __future__ import annotations

from .matrix import ENGINES, TIERS, ModelTier, available_tiers, cells
from .report import CellResult, Report, TierSummary, meets_gate, render_table, summarise
from .tasks import TASKS, BenchTask, Outcome, task_for

__all__ = [
    "ENGINES",
    "TASKS",
    "TIERS",
    "BenchTask",
    "CellResult",
    "ModelTier",
    "Outcome",
    "Report",
    "TierSummary",
    "available_tiers",
    "cells",
    "meets_gate",
    "render_table",
    "summarise",
    "task_for",
]
