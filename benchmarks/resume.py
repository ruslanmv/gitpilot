"""Resume: keep every task a model has already answered, re-run only the rest.

Long runs against a rate-limited provider get interrupted, and a task that ended
in a serving error (a 429, a timeout) has no answer to score. ``--resume``
collects, per model and per (task, repeat), the newest *answered* row for the
current task set — from finished result files, and from transcripts that an
interrupted run left behind — and the runner asks the model only for what is
still missing. The new run file then holds the complete set for that model, so
it stands on its own in the report.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .client import ChatResult
from .suites import Task

Key = Tuple[str, int]


def _transcripts(path: Path) -> Dict[Tuple[str, str, int], Dict[str, Any]]:
    entries = {}
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        entries[(entry.get("model"), entry.get("task"), entry.get("repeat", 1))] = entry
    return entries


def answered(results_dir: Path, digest: str, model: str,
             tasks: List[Task]) -> Dict[Key, Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]:
    """``{(task id, repeat): (row, transcript entry)}`` for every answered task, newest wins."""
    by_id = {task.id: task for task in tasks}
    found: Dict[Key, Tuple[Dict[str, Any], Optional[Dict[str, Any]]]] = {}
    runs = sorted(p for p in results_dir.glob("*.json"))
    current = [p for p in runs if _digest(p) == digest]
    first_current = current[0].stem if current else ""

    # Finished runs and orphaned transcripts, oldest first, so newer answers win.
    sources: List[Tuple[str, Optional[Path], Path]] = []
    for path in current:
        sources.append((path.stem, path, path.with_name(f"{path.stem}.transcripts.jsonl")))
    for transcript in results_dir.glob("*.transcripts.jsonl"):
        run_id = transcript.name[: -len(".transcripts.jsonl")]
        # An orphan has no manifest; trust it only if it is no older than the
        # first run of this task set (an interrupted run of an earlier task set
        # must not supply answers to different prompts).
        if not (results_dir / f"{run_id}.json").exists() and run_id >= first_current:
            sources.append((run_id, None, transcript))
    sources.sort(key=lambda item: item[0])

    for _run_id, result_path, transcript_path in sources:
        entries = _transcripts(transcript_path)
        if result_path is not None:
            for row in json.loads(result_path.read_text(encoding="utf-8")).get("results", []):
                if row.get("model") != model or row.get("task") not in by_id:
                    continue
                if row.get("passed") is None or row.get("infra_error"):
                    continue
                key = (row["task"], int(row.get("repeat", 1)))
                found[key] = (row, entries.get((model, row["task"], key[1])))
            continue
        for (entry_model, task_id, repeat), entry in entries.items():
            task = by_id.get(task_id)
            if entry_model != model or task is None or entry.get("error_kind"):
                continue
            reply = ChatResult(text=entry.get("text") or "", tool_calls=entry.get("tool_calls") or [],
                               latency_s=float(entry.get("latency_s") or 0.0))
            verdict = task.grade(reply)
            tokens, estimated = reply.output_tokens()
            row = {"task": task.id, "suite": task.suite, "category": task.category, "repeat": repeat,
                   "passed": verdict.passed, "infra_error": False, "detail": verdict.detail,
                   "latency_s": reply.latency_s, "attempts": 1, "error_kind": None, "served_model": "",
                   "output_tokens": tokens, "tokens_estimated": estimated,
                   "tool_calls_made": len(reply.tool_calls)}
            found[(task_id, int(repeat))] = (row, entry)
    return found


def _digest(path: Path) -> Optional[str]:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("manifest", {}).get("task_set_digest")
    except (OSError, ValueError):
        return None
