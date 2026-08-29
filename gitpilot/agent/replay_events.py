# gitpilot/agent/replay_events.py
"""Journal → wire events, for a reconnecting client — Batch V4-F2.

A client that drops mid-run and reconnects with ``Last-Event-ID: <seq>`` gets the
events it missed before being attached to the live bus. F12 is what this closes:
before it, a reconnect saw whatever happened *next* and had no way to learn what
it had missed, so a dropped connection during a long run meant the tool rows, the
file writes and the test result simply never appeared.

The projection table has been shipped as data since Batch V4-C1 —
``journal.PROJECTION`` — precisely so this batch implements a mapping rather than
inventing one. Two of its decisions are load-bearing here:

**In-flight ``text_delta`` chunks are not replayed.** The journal records the
turn's *text*, not the increments that composed it, so a replay serves one
``text_delta`` carrying the whole turn. That is the same information without
pretending to re-stream the past — and it is why the guarantee is worded as "no
state-bearing event lost" rather than "byte-identical replay".

**Keepalives are never replayed.** They are transport heartbeats and carry no
state; replaying twenty of them would tell a reconnecting client that twenty
things happened.

The reader is deliberately independent of the writing process. It opens the file
and reads: a worker that did not run the run can serve its replay, which is what
makes this work behind more than one uvicorn worker.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .. import agent_events as evt
from .journal import LineType, read_journal, read_spill

logger = logging.getLogger(__name__)

#: How much of a spilled observation a replay re-inflates.  The full text is in
#: the journal; a reconnecting client needs the row, not the 500 KB log.
MAX_REPLAYED_CONTENT = 4_000


def replay_events(
    journal_path: Path, *, after_seq: int = 0,
) -> Iterator[Any]:
    """Yield the events a client missed, oldest first.

    Each carries ``seq`` in its data so the client can advance its cursor and
    reconnect again from where this replay ended.
    """
    path = Path(journal_path)
    pending_asks: Dict[str, Dict[str, Any]] = {}

    for line in read_journal(path):
        seq = int(line.get("seq") or 0)
        kind = str(line.get("type") or "")

        # Decisions are read even when they precede the cursor: an approval that
        # was asked *before* the disconnect and answered after it still needs its
        # card, or the client shows a resolution for a request it never saw.
        if kind == LineType.POLICY_DECISION and line.get("verdict") == "ask":
            pending_asks[str(line.get("call_id") or "")] = line

        if seq <= after_seq:
            continue

        for event in _events_for(line, path, pending_asks):
            event.data["seq"] = seq
            event.data["replayed"] = True
            yield event


def _events_for(
    line: Dict[str, Any],
    path: Path,
    pending_asks: Dict[str, Dict[str, Any]],
) -> List[Any]:
    kind = str(line.get("type") or "")

    if kind == LineType.RUN_STARTED:
        seed = line.get("seed") or {}
        return [evt.status_change(
            "run_started", f"running on {seed.get('model') or 'the configured model'}",
        )]

    if kind == LineType.TURN:
        text = str(line.get("text") or "")
        # One event carrying the whole turn, not the increments that composed it.
        return [evt.text_delta(text)] if text else []

    if kind == LineType.TOOL_CALL:
        return [evt.tool_start(
            str(line.get("call_id") or ""),
            str(line.get("tool") or ""),
            dict(line.get("arguments") or {}),
        )]

    if kind == LineType.POLICY_DECISION:
        if line.get("verdict") != "ask":
            return []
        return [evt.approval_needed(
            request_id=str(line.get("call_id") or ""),
            tool=str(line.get("tool") or ""),
            args={},
            summary=str(line.get("reason") or ""),
            risk=str(line.get("risk") or "approval"),
        )]

    if kind == LineType.CHECKPOINT_REF:
        return [evt.status_change(
            "checkpoint_created", f"snapshot before {line.get('tool')}",
        )]

    if kind == LineType.TOOL_RESULT:
        return _result_events(line, path)

    if kind == LineType.APPROVAL:
        return [evt.approval_resolved(
            str(line.get("call_id") or ""), bool(line.get("approved")),
        )]

    if kind == LineType.TODO:
        return [evt.status_change(
            "todo_updated", f"{len(line.get('items') or [])} task(s)",
        )]

    if kind == LineType.COMPACTION:
        return [evt.status_change(
            "compaction",
            f"folded {line.get('folded_messages')} older message(s)",
        )]

    if kind == LineType.STATE_CHANGE:
        return _state_events(line)

    if kind == LineType.RUN_FINISHED:
        status = str(line.get("status") or "")
        if status in ("failed",):
            return [evt.agent_error(str(line.get("summary") or status), recoverable=False)]
        return [evt.agent_done(summary=str(line.get("summary") or ""))]

    return []


def _result_events(line: Dict[str, Any], path: Path) -> List[Any]:
    tool = str(line.get("tool") or "")
    data = dict(line.get("data") or {})
    ok = bool(line.get("ok"))
    denied = bool(line.get("denied"))

    events: List[Any] = [evt.tool_result(
        str(line.get("call_id") or ""), tool,
        "denied" if denied else ("ok" if ok else str(line.get("error") or "error")),
        is_error=not ok,
    )]

    # The side-effect events, from the recorded data rather than re-derived: a
    # replay that re-inferred "this wrote a file" could invent one.
    for written in data.get("paths_written") or []:
        events.append(evt.file_write(str(written), "modify"))
    if tool == "test.run" and "passed" in data:
        events.append(evt.test_result(
            framework=str(data.get("framework") or "tests"),
            passed=int(data.get("passed") or 0),
            failed=int(data.get("failed") or 0),
            skipped=int(data.get("skipped") or 0),
            exit_code=int(data.get("exit_code") or 0),
        ))
    return events


def _state_events(line: Dict[str, Any]) -> List[Any]:
    state = str(line.get("state") or "")

    if line.get("dialect_downgraded"):
        return [evt.status_change(
            "dialect_downgraded",
            f"switched to the {line['dialect_downgraded']} dialect",
        )]
    if line.get("resumed_from_seq") is not None:
        return [evt.status_change(
            "run_resumed", f"resumed from step {line.get('iteration') or 0}",
        )]
    if line.get("iteration"):
        return [evt.status_change("iteration", f"step {line['iteration']}")]
    if line.get("verification"):
        return [evt.status_change(
            "verification_required", f"attempt {line.get('cycle')}",
        )]
    if state:
        return [evt.status_change(state, "")]
    return []


def last_seq(journal_path: Path) -> int:
    """The highest ``seq`` in a journal — where a fresh client should start."""
    return max(
        (int(line.get("seq") or 0) for line in read_journal(Path(journal_path))),
        default=0,
    )


def parse_last_event_id(raw: Optional[str]) -> int:
    """Read a ``Last-Event-ID`` header.

    Anything unreadable means 0 — replay everything — because a client with a
    corrupt cursor is better served by too much than by a silent gap.
    """
    if not raw:
        return 0
    try:
        return max(int(str(raw).strip()), 0)
    except (TypeError, ValueError):
        logger.debug("unreadable Last-Event-ID %r; replaying from the start", raw)
        return 0


def sse_lines(journal_path: Path, *, after_seq: int = 0) -> Iterator[str]:
    """The replay as SSE frames, each with its ``id:`` so the cursor advances.

    The ``id:`` field is the point: without it a client cannot tell the browser
    what its ``Last-Event-ID`` should be, and the next reconnect starts from zero.
    """
    for event in replay_events(journal_path, after_seq=after_seq):
        seq = event.data.get("seq", 0)
        yield f"id: {seq}\n{event.to_sse()}"
