# gitpilot/agent/resume.py
"""Rebuilding a run from its journal — Batch V4-F1.

The journal has been written since Batch V4-C1 with exactly this in mind, and
this is what makes that investment pay: a process that died mid-run can be picked
up where it stopped.

The important design decision is that resume **replays facts, not messages**. The
journal records what the model asked for and what came back; it does not record
the provider-shaped transcript, because the transcript belongs to the dialect and
the dialect can change between the crash and the resume. So a run started on a
frontier model and resumed after the operator switched to a 1.5B one is rebuilt
through the *new* dialect's rendering of the same history. Storing the transcript
would have made that a merge conflict.

Three things it restores that a naive "re-run the task" would lose:

* **The ledger and the file sets**, so verification and the safety invariants can
  still answer "what did this run change?" across the gap.
* **The budgets**, so a run that had spent 30 of 40 iterations does not get 40
  more. A resume that reset the budget would be a way to run forever.
* **A pending approval.** A run that died while waiting on a human is re-prompted
  rather than silently executing the call or hanging on a future nobody holds.
  This is the case Batch V4-D3 explicitly deferred, and it is the reason
  ``awaiting_approval`` was journaled as a state rather than awaited as a
  coroutine.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .journal import (
    LineType,
    RunJournal,
    SeedContext,
    read_journal,
    read_spill,
)

logger = logging.getLogger(__name__)

FLAG_AGENT_RESUME = "agent_resume"

#: How much of a journaled tool result is fed back into a rebuilt transcript.
#: The full text is still in the journal; the model does not need all of it to
#: carry on, and a resume that re-inflated every observation would be over budget
#: before it took a turn.
MAX_REPLAYED_RESULT_CHARS = 4_000


class ResumeError(RuntimeError):
    """The run cannot be resumed, with a reason a caller can show a user."""


@dataclass
class ReplayedCall:
    """One call and its outcome, as the journal recorded them."""

    call_id: str
    tool: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    iteration: int = 0
    verdict: str = ""
    ok: Optional[bool] = None
    denied: bool = False
    content: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        """Whether this call reached a result at all."""
        return self.ok is not None

    @property
    def paths_written(self) -> List[str]:
        return [str(item) for item in (self.data.get("paths_written") or [])]


@dataclass
class ReplayedRun:
    """Everything a resume needs, read out of one journal.

    Deliberately a plain data object with no loop, no adapter and no provider:
    reading a journal is something a session listing does too, and it should not
    require building a model client to find out whether a run can be resumed.
    """

    path: Path
    run_id: str
    seed: Optional[SeedContext] = None
    status: Optional[str] = None
    last_state: str = ""
    last_seq: int = 0
    iteration: int = 0
    turns: List[Dict[str, Any]] = field(default_factory=list)
    calls: List[ReplayedCall] = field(default_factory=list)
    todos: List[Dict[str, Any]] = field(default_factory=list)
    files_read: List[str] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)
    tests: List[Dict[str, Any]] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=dict)
    #: The call that was awaiting a decision when the process went away.
    pending_approval: Optional[ReplayedCall] = None
    #: Set when the journal's own dialect differs from the one resuming.
    recorded_dialect: str = ""

    @property
    def resumable(self) -> bool:
        """A run is resumable when its journal never reached a terminal state.

        The definition is deliberately the *absence* of a finish line rather than
        a stored flag: a flag would have to be written by the process that died,
        which is the one thing a crash guarantees did not happen.
        """
        return self.status is None and self.seed is not None

    @property
    def awaiting_approval(self) -> bool:
        return self.pending_approval is not None

    def summary(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "journal": str(self.path),
            "task": self.seed.task if self.seed else "",
            "status": self.status,
            "state": self.last_state,
            "iteration": self.iteration,
            "tool_calls": len(self.calls),
            "files_modified": list(self.files_modified),
            "awaiting_approval": self.awaiting_approval,
            "pending_tool": self.pending_approval.tool if self.pending_approval else "",
            "resumable": self.resumable,
            "dialect": self.recorded_dialect,
        }


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def replay(path: Path) -> ReplayedRun:
    """Read one journal into a :class:`ReplayedRun`."""
    path = Path(path)
    run = ReplayedRun(path=path, run_id=path.stem)

    by_id: Dict[str, ReplayedCall] = {}
    decided: Dict[str, str] = {}
    asked: List[str] = []
    answered: set[str] = set()

    for line in read_journal(path):
        run.last_seq = max(run.last_seq, int(line.get("seq") or 0))
        kind = line.get("type")

        if kind == LineType.RUN_STARTED:
            run.seed = SeedContext.from_dict(line.get("seed") or {})
            run.recorded_dialect = str(line.get("seed", {}).get("dialect") or "")

        elif kind == LineType.STATE_CHANGE:
            run.last_state = str(line.get("state") or run.last_state)
            if line.get("iteration"):
                run.iteration = int(line["iteration"])
            if line.get("dialect_downgraded"):
                run.recorded_dialect = str(line["dialect_downgraded"])

        elif kind == LineType.TURN:
            run.turns.append(dict(line))
            for key in ("prompt_tokens", "completion_tokens"):
                usage = line.get("usage") or {}
                if key in usage:
                    run.usage[key] = run.usage.get(key, 0) + int(usage[key] or 0)

        elif kind == LineType.TOOL_CALL:
            call = ReplayedCall(
                call_id=str(line.get("call_id") or ""),
                tool=str(line.get("tool") or ""),
                arguments=dict(line.get("arguments") or {}),
                iteration=int(line.get("iteration") or 0),
            )
            by_id[call.call_id] = call
            run.calls.append(call)

        elif kind == LineType.POLICY_DECISION:
            decided_id = str(line.get("call_id") or "")
            decided[decided_id] = str(line.get("verdict") or "")
            if decided_id in by_id:
                by_id[decided_id].verdict = decided[decided_id]
            if decided[decided_id] == "ask":
                asked.append(decided_id)

        elif kind == LineType.APPROVAL:
            answered.add(str(line.get("call_id") or ""))

        elif kind == LineType.TOOL_RESULT:
            resolved = by_id.get(str(line.get("call_id") or ""))
            if resolved is None:
                continue
            resolved.ok = bool(line.get("ok"))
            resolved.denied = bool(line.get("denied"))
            resolved.data = dict(line.get("data") or {})
            resolved.content = _content_of(line, path)

        elif kind == LineType.TODO:
            run.todos = [dict(item) for item in (line.get("items") or [])]

        elif kind == LineType.RUN_FINISHED:
            run.status = str(line.get("status") or "")

    _derive_effects(run)

    # A pending approval is one that was asked and never answered, whose call
    # never produced a result. Both halves matter: an answered-then-executed call
    # is finished, and an asked-then-denied one already has its observation.
    for call_id in asked:
        candidate = by_id.get(call_id)
        if candidate is not None and call_id not in answered and not candidate.resolved:
            run.pending_approval = candidate

    return run


def _content_of(line: Dict[str, Any], journal_path: Path) -> str:
    """A result's text, following a spill reference when there is one."""
    content = line.get("content")
    if isinstance(content, str):
        return content
    reference = line.get("content_ref")
    if isinstance(reference, str) and reference:
        try:
            return read_spill(journal_path, reference)
        except Exception as exc:  # noqa: BLE001 - a missing spill is not fatal
            logger.debug("could not read spilled content %s: %s", reference, exc)
    return ""


def _derive_effects(run: ReplayedRun) -> None:
    """What the run read, wrote, ran and tested — from the calls, not the prose."""
    read: List[str] = []
    modified: List[str] = []

    for call in run.calls:
        if not call.ok:
            continue
        if call.tool in ("fs.read",):
            path = call.arguments.get("path")
            if isinstance(path, str) and path not in read:
                read.append(path)
        for written in call.paths_written:
            if written not in modified:
                modified.append(written)
        if call.tool in ("terminal.run", "terminal.run_snippet"):
            command = call.arguments.get("command") or call.arguments.get("code")
            if isinstance(command, str):
                run.commands.append(command)
        if call.tool == "test.run" and "passed" in call.data:
            run.tests.append({
                "command": str(call.data.get("command") or ""),
                "passed": int(call.data.get("passed") or 0),
                "failed": int(call.data.get("failed") or 0),
                "skipped": int(call.data.get("skipped") or 0),
                "green": bool(call.data.get("green")),
                "iteration": call.iteration,
            })

    run.files_read = read
    run.files_modified = modified


# ---------------------------------------------------------------------------
# Rebuilding the transcript
# ---------------------------------------------------------------------------


def rebuild_messages(run: ReplayedRun, adapter: Any) -> List[Dict[str, Any]]:
    """Re-render the run's history through *adapter*'s dialect.

    This is why a resume survives a model change. The journal holds calls and
    results; the transcript that goes to a provider is the dialect's business, and
    a NATIVE transcript is not a REACT_TEXT one. Rebuilding through the adapter
    that will actually run means the messages are shaped for the model that is
    about to see them, not for the one that died.

    A LITE run gets its injected file contents back the same way: they were
    journaled as ``fs.read`` results, so replaying those results replays the
    context the dialect had prefetched.
    """
    from ..toolkit.registry import ToolCall, ToolResult

    messages: List[Dict[str, Any]] = []
    if run.seed is not None:
        messages.extend(dict(message) for message in run.seed.messages)
        messages.append({"role": "user", "content": run.seed.task})

    for turn in run.turns:
        text = str(turn.get("text") or "")
        calls_in_turn = [
            call for call in run.calls
            if call.iteration == int(turn.get("iteration") or 0)
        ]
        if not text and not calls_in_turn:
            continue

        from .contracts import AgentTurn, Finality

        rendered = adapter.assistant_message(AgentTurn(
            text=text,
            tool_calls=[
                ToolCall(id=call.call_id, tool=call.tool, arguments=call.arguments)
                for call in calls_in_turn
            ],
            finality=Finality.CONTINUE,
        ))
        if rendered:
            messages.append(rendered)

        for call in calls_in_turn:
            if not call.resolved:
                continue
            tool_call = ToolCall(
                id=call.call_id, tool=call.tool, arguments=call.arguments,
            )
            content = call.content[:MAX_REPLAYED_RESULT_CHARS]
            result = (
                ToolResult.success(tool_call, content, data=call.data)
                if call.ok
                else ToolResult.failure(tool_call, content or "failed", error="replayed")
            )
            messages.extend(adapter.result_messages(tool_call, result))

    return messages


def resumable_runs(
    session_id: str, root: Optional[Path] = None,
) -> List[ReplayedRun]:
    """Every run in a session whose journal never reached a terminal state.

    What the session UI lists. Cheap: reading journals, no model client.
    """
    from .journal import list_runs

    found: List[ReplayedRun] = []
    for path in list_runs(session_id, root):
        try:
            run = replay(path)
        except Exception as exc:  # noqa: BLE001 - one bad journal is not fatal
            logger.debug("could not read journal %s: %s", path, exc)
            continue
        if run.resumable:
            found.append(run)
    return found


def restore(run: ReplayedRun, ctx: Any, adapter: Any) -> None:
    """Put a replayed run's state back onto a fresh context.

    The order matters only in that budgets go last: restoring them before the
    ledger would let a re-derived counter overwrite the journaled one.
    """
    from .context import CommandRecord, TestRecord, TodoItem
    from ..toolkit.registry import ToolCall, ToolResult

    ctx.messages = rebuild_messages(run, adapter)
    ctx.iteration = run.iteration
    ctx.files_read = set(run.files_read)
    ctx.files_modified = set(run.files_modified)
    ctx.todos = [
        TodoItem(
            id=str(item.get("id") or f"t{index + 1}"),
            text=str(item.get("text") or ""),
            status=str(item.get("status") or "pending"),
        )
        for index, item in enumerate(run.todos)
    ]
    ctx.commands = [
        CommandRecord(command=command, exit_code=None, iteration=run.iteration)
        for command in run.commands
    ]
    ctx.tests = [
        TestRecord(
            command=record["command"], passed=record["passed"],
            failed=record["failed"], skipped=record["skipped"],
            green=record["green"], iteration=record["iteration"],
        )
        for record in run.tests
    ]

    for call in run.calls:
        if not call.resolved:
            continue
        tool_call = ToolCall(
            id=call.call_id, tool=call.tool, arguments=call.arguments,
        )
        result = (
            ToolResult.success(tool_call, call.content, data=call.data)
            if call.ok
            else ToolResult.failure(tool_call, call.content or "failed", error="replayed")
        )
        ctx.ledger.record(tool_call, result, call.iteration)

    # Budgets last, and *restored* rather than reset: a resume that handed back a
    # full iteration budget would be a way to run forever by crashing.
    ctx.budgets.iterations = run.iteration
    ctx.budgets.tool_calls = len([call for call in run.calls if call.resolved])
    ctx.budgets.prompt_tokens = int(run.usage.get("prompt_tokens") or 0)
    ctx.budgets.completion_tokens = int(run.usage.get("completion_tokens") or 0)
