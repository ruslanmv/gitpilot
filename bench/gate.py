# bench/gate.py
"""The Phase G exit gate — Batch V4-G5.

``default`` now carries the agentic policy document, but ``agent_loop_default``
decides who *executes* it, and §15.4 says that flag turns on "after the benchmark
gate". This module is that gate, as a function rather than as a paragraph in a
release checklist:

    python -m bench.gate bench-results.json

Exit 0 means the matrix supports flipping the default. Anything else means it does
not, and says why.

The one thing this deliberately does not do is decide on partial evidence. A report
missing a tier, a tier where legacy never ran, an empty file — all fail. The
question being answered is "may every GitPilot user's default change?", and the
honest answer to incomplete evidence is no.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .report import FAIL, INCOMPLETE, PASS, REGRESSION_BUDGET


def evaluate(report: Dict[str, Any], *, budget: float = REGRESSION_BUDGET) -> Tuple[bool, List[str]]:
    """Whether *report* clears the gate, and the reasons it does not.

    Takes the serialised report rather than a :class:`~bench.report.Report` so a
    result file produced on someone else's machine — which is where the real
    numbers come from — can be checked here without re-running anything.
    """
    reasons: List[str] = []

    if not isinstance(report, dict) or "tiers" not in report:
        return False, ["not a benchmark report"]

    recorded_budget = report.get("budget")
    if isinstance(recorded_budget, (int, float)) and float(recorded_budget) > budget:
        reasons.append(
            f"the report was scored with a {recorded_budget}× budget, looser than "
            f"the {budget}× this gate requires"
        )

    missing = report.get("missing_tiers") or []
    if missing:
        reasons.append(f"tiers not run: {', '.join(str(name) for name in missing)}")

    tiers = report.get("tiers") or []
    if not tiers:
        reasons.append("the report contains no tiers")

    for tier in tiers:
        name = tier.get("tier", "?")
        verdict = tier.get("verdict")
        if verdict == PASS:
            continue
        detail = "; ".join(str(reason) for reason in (tier.get("reasons") or []))
        label = INCOMPLETE if verdict == INCOMPLETE else FAIL
        reasons.append(f"{name}: {label}" + (f" ({detail})" if detail else ""))

    return (not reasons), reasons


def evaluate_file(path: Path, *, budget: float = REGRESSION_BUDGET) -> Tuple[bool, List[str]]:
    from .report import load

    try:
        report = load(Path(path))
    except FileNotFoundError:
        return False, [f"no benchmark report at {path}"]
    except (ValueError, OSError) as exc:
        return False, [f"could not read {path}: {exc}"]
    return evaluate(report, budget=budget)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.gate", description=__doc__)
    parser.add_argument("report", type=Path, help="a JSON report from `python -m bench --out`")
    parser.add_argument("--budget", type=float, default=REGRESSION_BUDGET)
    args = parser.parse_args(argv)

    passed, reasons = evaluate_file(args.report, budget=args.budget)
    if passed:
        print(f"gate: pass — {args.report} supports flipping the default")
        return 0

    print(f"gate: not met — the default stays on the legacy engine", file=sys.stderr)
    for reason in reasons:
        print(f"  - {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
