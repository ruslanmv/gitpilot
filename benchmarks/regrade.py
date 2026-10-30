"""Re-score saved replies with the current graders, without asking any model again.

    python -m benchmarks.regrade                     # every run in benchmarks/results/
    python -m benchmarks.regrade results/<run>.json

Graders improve (a reply shape nobody anticipated, a stricter check), and the
fair way to apply a fix is to every stored answer at once. The transcripts are
the evidence; only rows that reached a model are re-graded, serving errors stay
as they were. The task set must be unchanged, which the digest proves.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .client import ChatResult
from .suites import load_tasks, task_set_digest

HERE = Path(__file__).resolve().parent


def regrade_run(path: Path) -> int:
    run = json.loads(path.read_text(encoding="utf-8"))
    chat_suites = [s for s in run["manifest"].get("suites", []) if s != "agentic"]
    tasks = {task.id: task for task in load_tasks(chat_suites)}
    if task_set_digest(list(tasks.values())) != run["manifest"].get("task_set_digest"):
        print(f"{path.name}: task set changed since this run; not re-graded (re-run it instead)")
        return 0
    transcript_path = path.with_name(f"{run['run_id']}.transcripts.jsonl")
    if not transcript_path.exists():
        print(f"{path.name}: no transcripts; nothing to re-grade")
        return 0
    replies = {}
    for line in transcript_path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        replies[(entry["model"], entry["task"], entry["repeat"])] = entry

    changed = 0
    for row in run["results"]:
        task = tasks.get(row["task"])
        entry = replies.get((row["model"], row["task"], row.get("repeat", 1)))
        if task is None or entry is None or entry.get("error_kind"):
            continue
        verdict = task.grade(ChatResult(text=entry.get("text") or "", tool_calls=entry.get("tool_calls") or []))
        if verdict.passed != row.get("passed") or verdict.detail != row.get("detail"):
            changed += row.get("passed") != verdict.passed
            row.update(passed=verdict.passed, detail=verdict.detail, infra_error=False)
    run["regraded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{path.name}: {changed} verdict(s) changed")
    return changed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.regrade")
    parser.add_argument("results", nargs="*", type=Path)
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args(argv)
    paths = args.results or sorted(p for p in (HERE / "results").glob("*.json"))
    for path in paths:
        regrade_run(path)
    if not args.no_report:
        from .report import render_reports

        print(render_reports(sorted((HERE / "results").glob("*.json")), HERE / "report.html"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
