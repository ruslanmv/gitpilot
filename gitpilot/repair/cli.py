"""GitPilot repair CLI.

Usage::

    gitpilot repair --repo <url> --plan <repair-plan.json> --sandbox matrixlab --dry-run

    # or, standalone:
    python -m gitpilot.repair.cli --repo <url> --plan <repair-plan.json> --dry-run

Reads the repair-plan JSON (a SelfRepair repair-plan == RepairRequest),
runs the service in dry-run, prints the patch PREVIEW + a summary, writes
``repair-response.json`` and exits 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .report import build_report
from .schema import RepairMode, RepairRequest
from .service import run_repair


def _load_plan(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("repair-plan must be a JSON object")
    return data


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gitpilot repair",
        description="Run the GitPilot generic repair flow (dry-run by default).",
    )
    parser.add_argument("--repo", "-r", help="Repository URL (overrides plan repo_url)")
    parser.add_argument(
        "--plan", "-p", required=True, help="Path to repair-plan.json (RepairRequest)"
    )
    parser.add_argument(
        "--sandbox",
        default=None,
        help="Sandbox provider (e.g. matrixlab). Overrides plan sandbox.provider.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force dry-run mode (no PR, no push). Default behaviour.",
    )
    parser.add_argument(
        "--out",
        "-o",
        default="repair-response.json",
        help="Where to write the repair-response JSON.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Force demo/offline stub mode (no network).",
    )
    args = parser.parse_args(argv)

    plan = _load_plan(args.plan)

    if args.repo:
        plan["repo_url"] = args.repo
    if args.sandbox:
        plan.setdefault("sandbox", {})
        plan["sandbox"]["provider"] = args.sandbox
    if args.dry_run or "mode" not in plan:
        plan["mode"] = RepairMode.dry_run.value

    request = RepairRequest.from_json(plan)

    # In dry-run the CLI never opens a PR; demo flag forces offline stubs.
    demo = True if args.demo else None
    if demo is None and os.getenv("GITPILOT_DEMO_MODE") is None:
        # Default the CLI to demo/offline unless explicitly configured otherwise.
        demo = True

    response = run_repair(request, demo_mode=demo)

    # Write repair-response.json
    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(response.to_json(), indent=2, sort_keys=True), encoding="utf-8"
    )

    # Print report + preview
    print(build_report(response))
    print(f"\nWrote repair-response to: {out_path}")

    return 0


def main() -> None:
    sys.exit(run_cli())


if __name__ == "__main__":  # pragma: no cover
    main()
