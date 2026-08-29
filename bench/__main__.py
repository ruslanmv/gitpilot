# bench/__main__.py
"""``python -m bench`` — Batch V4-G4.

Runs the matrix and prints the table. Exits non-zero when the gate is not met, so
it can be a step in a release check rather than something someone reads.

    python -m bench --out bench-results.json
    python -m bench --tiers local_small --tasks fix_failing_test --engines loop
    python -m bench --list

It refuses to invent numbers. A tier whose model is not reachable is reported as
missing and makes the report incomplete, which fails the gate — the alternative is
a green report for a matrix that was never run.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .matrix import ENGINES, TIERS, available_tiers, missing_tiers, tier_for
from .report import REGRESSION_BUDGET, meets_gate, render_table, summarise
from .runner import BenchEngine, CellRun, LegacyEngine, LoopEngine, run_matrix
from .tasks import TASKS


def _parse(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="bench", description=__doc__)
    parser.add_argument(
        "--tiers", nargs="*", default=None,
        help=f"model tiers to run (default: all available). Known: {', '.join(t.label for t in TIERS)}",
    )
    parser.add_argument(
        "--engines", nargs="*", default=list(ENGINES),
        help=f"engines to compare (default: {' '.join(ENGINES)})",
    )
    parser.add_argument("--tasks", nargs="*", default=None, help="task ids (default: all)")
    parser.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    parser.add_argument(
        "--budget", type=float, default=REGRESSION_BUDGET,
        help="wall-time and token regression budget per tier",
    )
    parser.add_argument(
        "--repo", default="",
        help="owner/name for the legacy engine, which needs a GitHub binding",
    )
    parser.add_argument("--token", default="", help="GitHub token for the legacy engine")
    parser.add_argument("--timeout", type=float, default=300.0, help="per-cell timeout")
    parser.add_argument("--list", action="store_true", help="list tasks and tiers, run nothing")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def _listing() -> str:
    lines = ["tasks:"]
    for task in TASKS:
        flags = []
        if task.read_only:
            flags.append("read-only")
        if task.min_tier != "lite":
            flags.append(f"needs ≥{task.min_tier}")
        suffix = f"   [{', '.join(flags)}]" if flags else ""
        lines.append(f"  {task.id:<20} {task.category:<9} {task.title}{suffix}")
    lines.append("")
    lines.append("tiers:")
    for tier in TIERS:
        state = "available" if tier in available_tiers() else f"missing ({tier.requires})"
        lines.append(f"  {tier.label:<14} {tier.family}/{tier.model:<22} {tier.expected_dialect:<11} {state}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.list:
        print(_listing())
        return 0

    if args.tiers:
        requested = [tier_for(label) for label in args.tiers]
        unknown = [label for label, tier in zip(args.tiers, requested) if tier is None]
        if unknown:
            print(f"unknown tier(s): {', '.join(unknown)}", file=sys.stderr)
            return 2
        tiers = [tier for tier in requested if tier is not None]
    else:
        tiers = available_tiers()

    if not tiers:
        print(
            "no model tier is available here. Start Ollama for the local tiers, or "
            "set ANTHROPIC_API_KEY for the frontier one; `python -m bench --list` "
            "shows what is missing.",
            file=sys.stderr,
        )
        return 2

    engines: Dict[str, BenchEngine] = {}
    if "loop" in args.engines:
        engines["loop"] = LoopEngine(timeout_s=args.timeout)
    if "legacy" in args.engines:
        engines["legacy"] = LegacyEngine(
            timeout_s=args.timeout, repo_full_name=args.repo, token=args.token,
        )
    if not engines:
        print(f"no known engine in {args.engines}", file=sys.stderr)
        return 2

    def announce(cell: CellRun) -> None:
        mark = "ok " if cell.ok else ("—  " if (cell.skipped or cell.unavailable) else "FAIL")
        print(
            f"{mark} {cell.tier:<14} {cell.engine:<7} {cell.task_id:<20} "
            f"{cell.metrics.wall_time_s:6.1f}s  {cell.outcome.detail}",
            flush=True,
        )

    results = run_matrix(engines, tiers=tiers, task_ids=args.tasks, on_cell=announce)

    absent = [tier.label for tier in missing_tiers() if tier in tiers or not args.tiers]
    report = summarise(results, missing=absent)

    print()
    print(render_table(report, budget=args.budget))

    if args.out:
        print(f"\nwrote {report.write(args.out, args.budget)}")

    return 0 if meets_gate(report, budget=args.budget) else 1


if __name__ == "__main__":
    raise SystemExit(main())
