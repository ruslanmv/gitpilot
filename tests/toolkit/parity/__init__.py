"""Legacy-vs-registry parity harness — Batch V4-A2.

Phase A's exit criterion is that a tool called through the canonical registry
produces the same output as the CrewAI-decorated tool it replaces.  That claim
needs a comparator, and it needs to exist *before* the tools do, which is why
this batch precedes A3–A6.

The gate is differential, not a committed golden file: each case runs the
legacy callable and the registry tool against the same fixture in the same
process, and compares.  A stored corpus would have to be re-recorded every time
the fixture changed, and a stale corpus is worse than none — it asserts
yesterday's behaviour with today's confidence.  Recording is still available
(:func:`record_corpus`) as a review artifact when a diff needs eyeballing.
"""
from __future__ import annotations

from .harness import (
    CASES,
    ParityCase,
    ParityMismatch,
    ParityOutcome,
    record_corpus,
    run_case,
)

__all__ = [
    "CASES",
    "ParityCase",
    "ParityMismatch",
    "ParityOutcome",
    "record_corpus",
    "run_case",
]
