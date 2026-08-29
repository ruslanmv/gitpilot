# gitpilot/agent/journal.py
"""The run journal — Batch V4-C1.

One append-only JSONL file per run at
``~/.gitpilot/sessions/<session>/runs/<run>.jsonl``, one line per thing that
happened, each with a monotonically increasing ``seq``.  It is the durable record
the whole resume story rests on (Batch V4-F1) and the source SSE reconnects
replay from (Batch V4-F2).

Three decisions worth understanding before changing anything here:

**Decisions are journaled before execution.**  The loop writes the
``policy_decision`` line — including a plain ``allow`` — and any
``checkpoint_ref`` *before* the tool runs, then the ``tool_result`` after.  That
ordering is what makes the safety invariant checkable ("no side effect without a
preceding allow") and what leaves a trace when a process dies between
authorising and executing.  Journaling only approvals and denials, as the first
draft of the design did, would have made those invariants unassertable.

**``run_started`` embeds the seed.**  The initial message set — the task plus a
reference into the session transcript — is the one thing replay cannot derive
from later lines.  Without it, a replay reconstructs everything except what the
model was originally asked.

**Nothing here may contain private reasoning.**  Reasoning-tag content is
stripped at the provider boundary; :func:`assert_no_reasoning` enforces that no
journal line carries it anyway, so a future change cannot quietly make the
product depend on storing a model's scratchpad.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

SESSIONS_ROOT = Path.home() / ".gitpilot" / "sessions"

#: Anything longer than this spills to a side file and the line keeps a
#: reference.  A 500 KB test log in the JSONL makes the journal unreadable and
#: slows every replay that only wanted the sequence of events.
MAX_INLINE_CONTENT_BYTES = 16_000


class LineType:
    """The journal's vocabulary.  A closed set — see :data:`PROJECTION`."""

    RUN_STARTED = "run_started"
    TURN = "turn"
    TOOL_CALL = "tool_call"
    POLICY_DECISION = "policy_decision"
    CHECKPOINT_REF = "checkpoint_ref"
    TOOL_RESULT = "tool_result"
    APPROVAL = "approval"
    TODO = "todo"
    COMPACTION = "compaction"
    STATE_CHANGE = "state_change"
    RUN_FINISHED = "run_finished"


ALL_LINE_TYPES = (
    LineType.RUN_STARTED,
    LineType.TURN,
    LineType.TOOL_CALL,
    LineType.POLICY_DECISION,
    LineType.CHECKPOINT_REF,
    LineType.TOOL_RESULT,
    LineType.APPROVAL,
    LineType.TODO,
    LineType.COMPACTION,
    LineType.STATE_CHANGE,
    LineType.RUN_FINISHED,
)

#: Lines whose arrival changes what a resume would do.  These are fsync'd, so a
#: power loss cannot leave the journal claiming a run is still running when it
#: was waiting for an approval, or vice versa.
DURABLE_LINE_TYPES = frozenset({
    LineType.RUN_STARTED,
    LineType.STATE_CHANGE,
    LineType.POLICY_DECISION,
    LineType.CHECKPOINT_REF,
    LineType.APPROVAL,
    LineType.RUN_FINISHED,
})

#: Marks a wire event that has no journal representation on purpose.
EPHEMERAL = "__ephemeral__"

#: **The journal → event projection.**  Shipped as data in this batch so Batch
#: V4-F2 implements a mapping rather than inventing one, and so the coverage test
#: can prove every wire event in §21.2 is either projectable or explicitly
#: ephemeral.
#:
#: The guarantee this encodes is "no *state-bearing* event lost on reconnect".
#: In-flight ``text_delta`` chunks are ephemeral — on replay a client is given
#: the journaled turn text instead of the increments that composed it, which is
#: the same information without pretending to re-stream the past.
PROJECTION: Dict[str, List[str]] = {
    LineType.RUN_STARTED: ["run_started", "status_change"],
    LineType.TURN: ["text_delta"],          # the whole turn text, not the deltas
    LineType.TOOL_CALL: ["tool_start"],
    LineType.POLICY_DECISION: ["approval_needed"],   # only when the verdict is `ask`
    LineType.CHECKPOINT_REF: ["checkpoint_created"],
    LineType.TOOL_RESULT: ["tool_result", "file_write", "terminal_output",
                           "terminal_exit", "test_result", "diagnostics"],
    LineType.APPROVAL: ["approval_resolved"],
    LineType.TODO: ["todo_updated"],
    LineType.COMPACTION: ["compaction"],
    LineType.STATE_CHANGE: ["status_change", "iteration", "dialect_downgraded",
                            "tools_pruned", "run_resumed"],
    LineType.RUN_FINISHED: ["done", "error"],
}

#: Wire events with no journal line, and why.
EPHEMERAL_EVENTS: Dict[str, str] = {
    "text_delta_increment": "streamed increments; replay serves the journaled turn text",
    "keepalive": "transport heartbeat, carries no state",
    "plan_step": "legacy pipelines only; the loop emits todo_updated instead",
}

_REASONING_MARKERS = re.compile(r"</?(?:think|thought|reasoning)\b", re.IGNORECASE)


class JournalError(RuntimeError):
    """Raised when a line would violate the journal's contract."""


def assert_no_reasoning(payload: Any, *, where: str = "journal line") -> None:
    """Refuse to persist a model's private scratchpad.

    A hard rule rather than a convention: the architecture must keep working
    without storing chain-of-thought, and the only way to be sure of that is to
    make storing it fail.  ``reasoning_normalizer`` strips tags at the provider
    boundary, so reaching here means something bypassed it.
    """
    if isinstance(payload, str):
        if _REASONING_MARKERS.search(payload):
            raise JournalError(
                f"{where} contains reasoning-tag content; it is stripped at the "
                "provider boundary and must never be persisted"
            )
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in ("reasoning", "reasoning_content"):
                raise JournalError(f"{where} carries a {key!r} field")
            assert_no_reasoning(value, where=where)
        return
    if isinstance(payload, (list, tuple)):
        for item in payload:
            assert_no_reasoning(item, where=where)


@dataclass
class SeedContext:
    """What the run began with — the part replay cannot reconstruct."""

    task: str
    #: Where the prior conversation lives, rather than a copy of it: the session
    #: file is the source of truth and duplicating it would let the two drift.
    session_id: Optional[str] = None
    transcript_index: int = 0
    #: Messages seeded into the loop that are *not* in the session transcript
    #: (an expanded slash command, injected project context).
    messages: List[Dict[str, Any]] = field(default_factory=list)
    topology_id: str = ""
    model: str = ""
    dialect: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "session_id": self.session_id,
            "transcript_index": self.transcript_index,
            "messages": self.messages,
            "topology_id": self.topology_id,
            "model": self.model,
            "dialect": self.dialect,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SeedContext":
        return cls(
            task=payload.get("task", ""),
            session_id=payload.get("session_id"),
            transcript_index=int(payload.get("transcript_index") or 0),
            messages=list(payload.get("messages") or []),
            topology_id=payload.get("topology_id", ""),
            model=payload.get("model", ""),
            dialect=payload.get("dialect", ""),
        )


class RunJournal:
    """Append-only record of one run."""

    def __init__(
        self,
        run_id: str,
        session_id: str = "ephemeral",
        root: Optional[Path] = None,
        *,
        clock: Optional[Any] = None,
        directory: Optional[Path] = None,
    ) -> None:
        self.run_id = run_id
        self.session_id = session_id
        self._root = Path(root) if root else SESSIONS_ROOT
        #: Overrides the derived location.  A subagent's journal lives at
        #: ``runs/<parent>/sub/<n>.jsonl`` (Batch V4-E2), which is not a session
        #: path — and reaching in to patch ``_root`` after construction would
        #: leave the spill directory pointing somewhere else.
        self._directory = Path(directory) if directory else None
        self._seq = 0
        self._clock = clock or _utc_now
        self._closed = False

    # -- paths ------------------------------------------------------------

    @property
    def directory(self) -> Path:
        if self._directory is not None:
            return self._directory
        return self._root / self.session_id / "runs"

    @property
    def path(self) -> Path:
        return self.directory / f"{self.run_id}.jsonl"

    @property
    def spill_directory(self) -> Path:
        """Where oversized payloads go, alongside the journal."""
        return self.directory / f"{self.run_id}.d"

    @property
    def seq(self) -> int:
        return self._seq

    # -- writing ----------------------------------------------------------

    def append(self, line_type: str, payload: Optional[Dict[str, Any]] = None) -> int:
        """Write one line and return its ``seq``."""
        if line_type not in ALL_LINE_TYPES:
            raise JournalError(f"unknown journal line type {line_type!r}")
        if self._closed:
            raise JournalError(f"journal for run {self.run_id} is closed")

        body = dict(payload or {})
        assert_no_reasoning(body, where=f"{line_type} line")
        body = self._spill_oversized(body)

        self._seq += 1
        record = {
            "seq": self._seq,
            "type": line_type,
            "ts": self._clock(),
            "run_id": self.run_id,
            **body,
        }

        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            if line_type in DURABLE_LINE_TYPES:
                # Cheap, and it is what makes "the journal says awaiting_approval"
                # trustworthy after a hard kill.
                handle.flush()
                os.fsync(handle.fileno())

        return self._seq

    def _spill_oversized(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Move large text out of the line, leaving a reference.

        The full output is never discarded — a truncated observation is what the
        *model* saw, and the journal is supposed to hold what actually happened.
        """
        out = dict(body)
        for key in ("content", "text", "output"):
            value = out.get(key)
            if not isinstance(value, str) or len(value) <= MAX_INLINE_CONTENT_BYTES:
                continue
            self.spill_directory.mkdir(parents=True, exist_ok=True)
            spill_path = self.spill_directory / f"{self._seq + 1}-{key}.txt"
            spill_path.write_text(value, encoding="utf-8")
            out[key] = value[:MAX_INLINE_CONTENT_BYTES] + "\n… spilled"
            out[f"{key}_ref"] = str(spill_path.relative_to(self.directory))
            out[f"{key}_bytes"] = len(value)
        return out

    # -- typed helpers ----------------------------------------------------

    def run_started(self, seed: SeedContext, **extra: Any) -> int:
        return self.append(LineType.RUN_STARTED, {"seed": seed.to_dict(), **extra})

    def turn(self, *, text: str, iteration: int, finality: str, usage: Any = None) -> int:
        return self.append(LineType.TURN, {
            "text": text,
            "iteration": iteration,
            "finality": finality,
            "usage": _usage_dict(usage),
        })

    def tool_call(self, call: Any, *, iteration: int) -> int:
        return self.append(LineType.TOOL_CALL, {
            "call_id": call.id,
            "tool": call.tool,
            "arguments": call.arguments,
            "iteration": iteration,
        })

    def policy_decision(
        self, call: Any, *, verdict: str, reason: str = "", risk: str = "",
        requires_checkpoint: bool = False, command_class: str = "",
    ) -> int:
        """Written before the call runs — including for a plain ``allow``.

        ``command_class`` carries Batch V4-D2's semantic classification, so the
        safety invariants can assert "a DESTRUCTIVE command never reached a
        prompt" from the record rather than by re-deriving it.
        """
        return self.append(LineType.POLICY_DECISION, {
            "call_id": call.id,
            "tool": call.tool,
            "verdict": verdict,
            "reason": reason,
            "risk": risk,
            "requires_checkpoint": requires_checkpoint,
            "command_class": command_class,
        })

    def checkpoint_ref(self, call: Any, checkpoint_id: str) -> int:
        return self.append(LineType.CHECKPOINT_REF, {
            "call_id": call.id, "tool": call.tool, "checkpoint_id": checkpoint_id,
        })

    def tool_result(self, call: Any, result: Any) -> int:
        return self.append(LineType.TOOL_RESULT, {
            "call_id": result.call_id,
            "tool": result.tool,
            "ok": result.ok,
            "denied": result.denied,
            "error": result.error,
            "content": result.content,
            "data": result.data or {},
        })

    def approval(self, call: Any, *, approved: bool, scope: str = "once") -> int:
        return self.append(LineType.APPROVAL, {
            "call_id": call.id, "tool": call.tool, "approved": approved, "scope": scope,
        })

    def todo(
        self,
        items: List[Dict[str, Any]],
        transitions: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """The list, plus what changed to produce it (Batch V4-E1).

        Both, because they answer different questions: the list is what a
        reconnecting client renders, and the transitions are what a reader of the
        journal needs to see the run's progress without diffing every version
        against the last.
        """
        return self.append(LineType.TODO, {
            "items": items,
            "transitions": transitions or [],
        })

    def compaction(self, *, before_tokens: int, after_tokens: int, folded: int) -> int:
        return self.append(LineType.COMPACTION, {
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "folded_messages": folded,
        })

    def state_change(self, state: str, **detail: Any) -> int:
        return self.append(LineType.STATE_CHANGE, {"state": state, **detail})

    def finish(self, status: str, *, summary: str = "", **detail: Any) -> int:
        seq = self.append(LineType.RUN_FINISHED, {
            "status": status, "summary": summary, **detail,
        })
        self._closed = True
        return seq

    # -- reading ----------------------------------------------------------

    def read(self) -> List[Dict[str, Any]]:
        return list(read_journal(self.path))

    def resume_from(self, seq: int = 0) -> List[Dict[str, Any]]:
        """Lines after *seq* — what an SSE reconnect replays (Batch V4-F2)."""
        return [line for line in self.read() if int(line.get("seq") or 0) > seq]

    def reopen(self) -> "RunJournal":
        """Continue an existing journal, continuing its sequence.

        Used by resume: the journal is one record of one run, so a resumed run
        appends rather than starting a second file that would have to be merged.
        """
        existing = self.read()
        self._seq = max((int(line.get("seq") or 0) for line in existing), default=0)
        self._closed = any(
            line.get("type") == LineType.RUN_FINISHED for line in existing
        )
        return self

    def truncate_after(self, seq: int) -> int:
        """Drop every line after *seq*.  Returns how many were removed.

        Used by a rewind (Batch V4-D4): once the files are back as they were, a
        journal still describing the edits that followed would have the run and
        the workspace disagreeing about what happened. The `.rewound-<seq>`
        sidecar keeps the discarded tail, because "we deleted the evidence" is
        not an acceptable answer even when the deletion was asked for.
        """
        lines = self.read()
        keep = [line for line in lines if int(line.get("seq") or 0) <= seq]
        dropped = lines[len(keep):]
        if not dropped:
            return 0

        archive = self.path.with_suffix(f".rewound-{seq}.jsonl")
        with archive.open("a", encoding="utf-8") as handle:
            for line in dropped:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")

        with self.path.open("w", encoding="utf-8") as handle:
            for line in keep:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        self._seq = seq
        self._closed = False
        return len(dropped)


# ---------------------------------------------------------------------------
# Module-level readers
# ---------------------------------------------------------------------------


def read_journal(path: Path) -> Iterator[Dict[str, Any]]:
    """Yield the lines of a journal, skipping any that are unreadable.

    A torn last line — the process died mid-write — must not make the whole run
    unreadable, since resume is exactly the case where that has happened.
    """
    if not Path(path).exists():
        return
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            logger.warning("skipping unreadable journal line in %s", path)
            continue
        if isinstance(line, dict):
            yield line


def read_seed(path: Path) -> Optional[SeedContext]:
    """The seed a run started from, or ``None`` if the journal is unreadable."""
    for line in read_journal(path):
        if line.get("type") == LineType.RUN_STARTED:
            return SeedContext.from_dict(line.get("seed") or {})
    return None


def terminal_status(path: Path) -> Optional[str]:
    """The run's final status, or ``None`` if it never reached one.

    ``None`` is what marks a run resumable.
    """
    for line in reversed(list(read_journal(path))):
        if line.get("type") == LineType.RUN_FINISHED:
            return str(line.get("status") or "")
    return None


def read_spill(journal_path: Path, reference: str) -> str:
    """Recover a spilled payload by its reference."""
    return (Path(journal_path).parent / reference).read_text(encoding="utf-8")


def list_runs(session_id: str, root: Optional[Path] = None) -> List[Path]:
    """Every journal recorded for a session, oldest first."""
    directory = (Path(root) if root else SESSIONS_ROOT) / session_id / "runs"
    if not directory.exists():
        return []
    return sorted(directory.glob("*.jsonl"))


def project_events(line: Dict[str, Any]) -> List[str]:
    """Which wire events a journal line can produce (Batch V4-F2 consumes this)."""
    return list(PROJECTION.get(str(line.get("type")), []))


def _usage_dict(usage: Any) -> Dict[str, int]:
    if usage is None:
        return {}
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
    }


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
