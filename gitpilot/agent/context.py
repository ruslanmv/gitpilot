# gitpilot/agent/context.py
"""Run state — Batch V4-C1.

:class:`AgentContext` is everything one run knows: the messages, what has been
read and written, which commands ran, the TODO list, the budgets, and the journal
it all gets recorded to.  The loop holds one and mutates it; nothing else does.

The ledger is worth its weight.  A model's context is lossy by design — it gets
compacted, truncated and re-rendered — so questions like "which files has this
run modified?" cannot be answered by grepping the transcript.  The ledger answers
them structurally, which is what the verification policy (Batch V4-E3) and the
safety invariants (Batch V4-D7) check against.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set

from ..toolkit.registry import (
    ALL_CAPABILITIES,
    CapabilitySet,
    ToolCall,
    ToolExecutionContext,
    ToolResult,
)
from .contracts import AgentTurn, TokenUsage
from .journal import RunJournal, SeedContext
from .model_profile import ModelProfile

logger = logging.getLogger(__name__)


class State(str, Enum):
    """The loop's state machine (§5.4).

    ``AWAITING_APPROVAL`` is a real, journaled state rather than a transient
    ``await``: if the process dies while a user is deciding, a resume must know
    it was waiting rather than silently re-running the call.  Batch V4-D3 drives
    it; here it is unreachable because the stub policy never asks.
    """

    CREATED = "created"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPACTING = "compacting"
    PAUSED = "paused"
    COMPLETED = "completed"
    DEGRADED = "degraded"
    BUDGET_EXCEEDED = "budget_exceeded"
    CANCELLED = "cancelled"
    FAILED = "failed"


TERMINAL_STATES = frozenset({
    State.COMPLETED, State.DEGRADED, State.BUDGET_EXCEEDED,
    State.CANCELLED, State.FAILED,
})


class BudgetExceeded(RuntimeError):
    """A limit was reached.  Not a failure — the run returns what it has."""

    def __init__(self, limit: str, value: int) -> None:
        super().__init__(f"{limit} limit reached ({value})")
        self.limit = limit
        self.value = value


@dataclass
class BudgetState:
    """Limits and counters.

    Defaults match §5.5.  A topology tightens them (Batch V4-G1); the engine caps
    are what stop a misconfigured topology from running forever.
    """

    max_iterations: int = 40
    max_tool_calls: int = 120
    max_runtime_seconds: int = 1800
    max_tokens: Optional[int] = None

    iterations: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: ``None`` until the run starts.  Not ``0.0``: a falsy sentinel makes
    #: ``if self.started_at`` skip the wall-clock check whenever the clock
    #: happens to read zero, which ``time.monotonic`` can do shortly after boot.
    started_at: Optional[float] = None

    def start(self, now: float) -> None:
        self.started_at = now

    def check(self, now: float) -> None:
        """Raise :class:`BudgetExceeded` when a limit is reached."""
        if self.iterations >= self.max_iterations:
            raise BudgetExceeded("max_iterations", self.max_iterations)
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExceeded("max_tool_calls", self.max_tool_calls)
        if (
            self.started_at is not None
            and (now - self.started_at) >= self.max_runtime_seconds
        ):
            raise BudgetExceeded("max_runtime_seconds", self.max_runtime_seconds)
        if self.max_tokens is not None and self.total_tokens >= self.max_tokens:
            raise BudgetExceeded("max_tokens", self.max_tokens)

    def record_usage(self, usage: Optional[TokenUsage]) -> None:
        if usage is None:
            return
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iterations": self.iterations,
            "tool_calls": self.tool_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "max_iterations": self.max_iterations,
            "max_tool_calls": self.max_tool_calls,
            "max_runtime_seconds": self.max_runtime_seconds,
        }


@dataclass
class LedgerEntry:
    call: ToolCall
    result: ToolResult
    iteration: int


@dataclass
class ToolLedger:
    """Every call and result, structurally.

    Not derived from the transcript: the transcript is compacted and truncated,
    and "did this run modify anything?" has to survive that.
    """

    entries: List[LedgerEntry] = field(default_factory=list)

    def record(self, call: ToolCall, result: ToolResult, iteration: int) -> None:
        self.entries.append(LedgerEntry(call=call, result=result, iteration=iteration))

    def calls_to(self, tool: str) -> List[LedgerEntry]:
        return [entry for entry in self.entries if entry.call.tool == tool]

    def any_call_to(self, *tools: str) -> bool:
        wanted = set(tools)
        return any(entry.call.tool in wanted for entry in self.entries)

    @property
    def failures(self) -> List[LedgerEntry]:
        return [entry for entry in self.entries if not entry.result.ok]

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[LedgerEntry]:
        return iter(self.entries)


@dataclass
class CommandRecord:
    command: str
    exit_code: Optional[int]
    iteration: int


@dataclass
class TestRecord:
    command: str
    passed: int
    failed: int
    skipped: int
    green: bool
    iteration: int


@dataclass
class ErrorRecord:
    where: str
    message: str
    iteration: int


@dataclass
class TodoItem:
    id: str
    text: str
    status: str = "pending"     # pending | in_progress | completed | blocked

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "text": self.text, "status": self.status}


@dataclass
class AgentContext:
    """One run's state.

    Field-for-field the §9.1 shape, including ``capabilities`` and
    ``tool_context`` — the two the design review caught missing from the first
    draft's dataclass while its algorithm already dereferenced them.
    """

    run_id: str
    task: str
    journal: RunJournal
    model_profile: ModelProfile
    tool_context: ToolExecutionContext
    session_id: str = "ephemeral"
    topology_id: str = ""
    capabilities: CapabilitySet = ALL_CAPABILITIES
    system_payload: Any = None

    messages: List[Dict[str, Any]] = field(default_factory=list)
    ledger: ToolLedger = field(default_factory=ToolLedger)
    files_read: Set[str] = field(default_factory=set)
    files_modified: Set[str] = field(default_factory=set)
    commands: List[CommandRecord] = field(default_factory=list)
    tests: List[TestRecord] = field(default_factory=list)
    todos: List[TodoItem] = field(default_factory=list)
    errors: List[ErrorRecord] = field(default_factory=list)

    state: State = State.CREATED
    iteration: int = 0
    #: Set false when the verification policy gave up (Batch V4-E3).
    verified: bool = True
    budgets: BudgetState = field(default_factory=BudgetState)
    #: Set when the run degrades a dialect or skips work it could not do, so the
    #: final answer can say which steps were dropped instead of implying success.
    skipped: List[str] = field(default_factory=list)

    # -- lifecycle --------------------------------------------------------

    def begin(self, seed: SeedContext, now: float) -> None:
        self.budgets.start(now)
        self.journal.run_started(seed)
        self.set_state(State.RUNNING)

    def set_state(self, state: State, **detail: Any) -> None:
        if state is self.state:
            return
        self.state = state
        self.journal.state_change(state.value, **detail)

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL_STATES

    # -- recording --------------------------------------------------------

    def add_message(self, message: Optional[Dict[str, Any]]) -> None:
        if message:
            self.messages.append(message)

    def record_turn(self, turn: AgentTurn) -> None:
        self.budgets.record_usage(turn.usage)
        self.journal.turn(
            text=turn.text,
            iteration=self.iteration,
            finality=turn.finality.value,
            usage=turn.usage,
        )

    def record(self, call: ToolCall, result: ToolResult) -> None:
        """Ledger, derived sets and journal, in that order.

        The derived sets come from ``result.data`` rather than from parsing
        ``content`` — that structured payload exists precisely so no consumer has
        to read prose (§7.4).
        """
        self.ledger.record(call, result, self.iteration)
        self.budgets.tool_calls += 1
        data = result.data or {}

        if call.tool.startswith("fs.") and result.ok:
            path = data.get("path") or call.arguments.get("path")
            if path:
                if data.get("paths_written"):
                    self.files_modified.add(str(path))
                else:
                    self.files_read.add(str(path))
        for written in data.get("paths_written") or []:
            self.files_modified.add(str(written))

        if call.tool.startswith("terminal.") and result.ok:
            self.commands.append(CommandRecord(
                command=str(data.get("command") or call.arguments.get("command") or ""),
                exit_code=data.get("exit_code"),
                iteration=self.iteration,
            ))

        if call.tool == "test.run" and result.ok:
            self.tests.append(TestRecord(
                command=str(data.get("command") or ""),
                passed=int(data.get("passed") or 0),
                failed=int(data.get("failed") or 0),
                skipped=int(data.get("skipped") or 0),
                green=bool(data.get("green")),
                iteration=self.iteration,
            ))

        if not result.ok:
            self.errors.append(ErrorRecord(
                where=call.tool,
                message=result.error or "failed",
                iteration=self.iteration,
            ))

        self.journal.tool_result(call, result)

    def record_error(self, where: str, message: str) -> None:
        self.errors.append(ErrorRecord(where=where, message=message, iteration=self.iteration))

    def set_todos(self, items: Sequence[TodoItem]) -> None:
        self.todos = list(items)
        self.journal.todo([item.to_dict() for item in self.todos])

    def replace_todos(self, items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Swap in a new list and return the transitions — Batch V4-E1.

        The whole list is submitted every time, so the diff is computed here
        rather than trusted from the caller: the model is describing where it
        thinks it is, and what *changed* is our inference from that, not its
        claim.  Ids are assigned positionally and kept stable across rewrites for
        an item whose text has not changed, so a UI can animate a status change
        instead of redrawing the panel.
        """
        from ..toolkit.todo import diff as diff_todos

        transitions = [t.to_dict() for t in diff_todos(self.todos, list(items))]
        existing = {item.text: item.id for item in self.todos}
        self.todos = [
            TodoItem(
                id=existing.get(entry["text"]) or f"t{index + 1}",
                text=entry["text"],
                status=entry["status"],
            )
            for index, entry in enumerate(items)
        ]
        self.journal.todo(
            [item.to_dict() for item in self.todos], transitions=transitions,
        )
        return transitions

    def note_skipped(self, what: str) -> None:
        if what not in self.skipped:
            self.skipped.append(what)

    # -- reporting --------------------------------------------------------

    @property
    def mutated(self) -> bool:
        """Whether this run has changed anything on disk or in a repository."""
        return bool(self.files_modified)

    def summary(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state.value,
            "iterations": self.iteration,
            "tool_calls": len(self.ledger),
            "files_read": sorted(self.files_read),
            "files_modified": sorted(self.files_modified),
            "commands": [record.command for record in self.commands],
            "tests": [
                {"command": t.command, "passed": t.passed, "failed": t.failed, "green": t.green}
                for t in self.tests
            ],
            "errors": [{"where": e.where, "message": e.message} for e in self.errors],
            "skipped": list(self.skipped),
            "budgets": self.budgets.to_dict(),
        }


@dataclass
class AgentResult:
    """What a run returns."""

    answer: str
    status: str
    context: AgentContext
    #: Populated when the run ended without doing everything it was asked.
    skipped: List[str] = field(default_factory=list)
    #: ``False`` when the run changed files without a passing test run
    #: (Batch V4-E3).  Carried separately from ``status`` because "it worked but
    #: nobody checked" is a different thing from "it did not work", and a caller
    #: showing a green tick either way is the outcome verification exists to stop.
    verified: bool = True

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "answer": self.answer,
            "status": self.status,
            "skipped": list(self.skipped),
            "verified": self.verified,
            **self.context.summary(),
        }
