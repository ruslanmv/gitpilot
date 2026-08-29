# gitpilot/agent_events.py
"""
Unified agent event protocol.

Every agent action emits events through an AgentEventBus. Consumers
(WebSocket v2, SSE, VS Code postMessage) subscribe and forward to clients.

Event types mirror Claude Code's streaming model:
  - text_delta         incremental response text
  - tool_start         agent is calling a tool
  - tool_result        tool returned a result
  - file_write         agent wrote/edited a file
  - approval_needed    agent needs user permission
  - approval_resolved  user responded to approval
  - plan_step          plan step status change
  - terminal_output    shell stdout/stderr line
  - terminal_exit      shell process exited
  - test_result        structured test pass/fail
  - diagnostics        lint/type errors
  - status_change      idle/planning/generating/etc.
  - done               agent finished
  - error              agent failed

All platforms consume the same JSON event shape. The only difference
is the transport: WebSocket, SSE, or VS Code postMessage.
"""
from __future__ import annotations

import asyncio
import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional

logger = logging.getLogger(__name__)

#: Seconds of silence before a heartbeat.  15 rather than 25 (Batch V4-E4): the
#: usual proxy idle timeout is 30–60s, and 25 leaves no margin for one late beat.
HEARTBEAT_INTERVAL_SEC = 15.0


@dataclass
class StreamMetrics:
    """First-byte and total timing for one stream — Batch V4-E4.

    Adopted from ``streaming.py`` rather than reimplemented: that module had the
    right framing (named events, heartbeats, back-pressure, timing) and no
    caller, while this bus had every caller and none of the framing. Keeping both
    would have meant two answers to "how long until the user saw something?".
    """

    started_at: float = field(default_factory=time.monotonic)
    first_byte_at: Optional[float] = None
    finished_at: Optional[float] = None
    event_count: int = 0

    #: Events that do not count as "the user saw something".
    _SILENT = ("keepalive",)

    def observe(self, event: "AgentEvent") -> None:
        self.event_count += 1
        if event.data.get("status") in self._SILENT:
            return
        if self.first_byte_at is None:
            self.first_byte_at = time.monotonic()
        if event.type in (EventType.DONE, EventType.ERROR):
            self.finished_at = time.monotonic()

    @property
    def first_byte_ms(self) -> Optional[int]:
        if self.first_byte_at is None:
            return None
        return int((self.first_byte_at - self.started_at) * 1000)

    @property
    def total_ms(self) -> Optional[int]:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at) * 1000)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "first_byte_ms": self.first_byte_ms,
            "total_ms": self.total_ms,
            "event_count": self.event_count,
        }


class EventType(str, enum.Enum):
    # Batch V4-C2 additions.  The loop needs to say what it is doing between
    # tool calls; before these, a client could only see plan steps and a final
    # answer, which is why the UI had nothing to render for an iterative run.
    RUN_STARTED = "run_started"
    RUN_RESUMED = "run_resumed"
    ITERATION = "iteration"
    AGENT_MESSAGE = "agent_message"
    TODO_UPDATED = "todo_updated"
    CHECKPOINT_CREATED = "checkpoint_created"
    COMPACTION = "compaction"
    DIALECT_DOWNGRADED = "dialect_downgraded"
    TOOLS_PRUNED = "tools_pruned"
    AGENT_DELEGATED = "agent_delegated"
    AGENT_DELEGATE_DONE = "agent_delegate_done"

    TEXT_DELTA = "text_delta"
    TOOL_START = "tool_start"
    TOOL_RESULT = "tool_result"
    FILE_WRITE = "file_write"
    APPROVAL_NEEDED = "approval_needed"
    APPROVAL_RESOLVED = "approval_resolved"
    PLAN_STEP = "plan_step"
    TERMINAL_OUTPUT = "terminal_output"
    TERMINAL_EXIT = "terminal_exit"
    TEST_RESULT = "test_result"
    DIAGNOSTICS = "diagnostics"
    STATUS_CHANGE = "status_change"
    DONE = "done"
    ERROR = "error"


@dataclass
class AgentEvent:
    """Single event emitted by the agent during execution."""

    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "id": self.id,
            "ts": self.timestamp,
            **self.data,
        }

    def to_sse(self) -> str:
        """Format as a Server-Sent Event line."""
        import json

        return f"data: {json.dumps(self.to_dict())}\n\n"


# ---------------------------------------------------------------------------
# Factory functions for clean event creation
# ---------------------------------------------------------------------------


def text_delta(text: str) -> AgentEvent:
    return AgentEvent(type=EventType.TEXT_DELTA, data={"text": text})


def tool_start(tool_id: str, name: str, args: dict) -> AgentEvent:
    return AgentEvent(
        type=EventType.TOOL_START,
        data={"tool_id": tool_id, "name": name, "arguments": args},
    )


def tool_result(
    tool_id: str, name: str, result: str, is_error: bool = False
) -> AgentEvent:
    return AgentEvent(
        type=EventType.TOOL_RESULT,
        data={
            "tool_id": tool_id,
            "name": name,
            "result": result[:2000],
            "is_error": is_error,
        },
    )


def file_write(path: str, action: str = "modify") -> AgentEvent:
    return AgentEvent(
        type=EventType.FILE_WRITE, data={"path": path, "action": action}
    )


def approval_needed(
    request_id: str,
    tool: str,
    args: dict,
    summary: str,
    diff_preview: str | None = None,
    risk: str = "medium",
) -> AgentEvent:
    return AgentEvent(
        type=EventType.APPROVAL_NEEDED,
        data={
            "request_id": request_id,
            "tool": tool,
            "arguments": args,
            "summary": summary,
            "diff_preview": diff_preview,
            "risk_level": risk,
        },
    )


def approval_resolved(request_id: str, approved: bool) -> AgentEvent:
    return AgentEvent(
        type=EventType.APPROVAL_RESOLVED,
        data={"request_id": request_id, "approved": approved},
    )


def plan_step(index: int, title: str, status: str, action: str = "") -> AgentEvent:
    return AgentEvent(
        type=EventType.PLAN_STEP,
        data={
            "step_index": index,
            "title": title,
            "status": status,
            "action": action,
        },
    )


def terminal_output(stream: str, text: str) -> AgentEvent:
    return AgentEvent(
        type=EventType.TERMINAL_OUTPUT, data={"stream": stream, "text": text}
    )


def terminal_exit(command: str, exit_code: int, duration_ms: int = 0) -> AgentEvent:
    return AgentEvent(
        type=EventType.TERMINAL_EXIT,
        data={
            "command": command,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
        },
    )


def test_result(
    framework: str,
    passed: int,
    failed: int,
    skipped: int = 0,
    output: str = "",
    exit_code: int = 0,
) -> AgentEvent:
    return AgentEvent(
        type=EventType.TEST_RESULT,
        data={
            "framework": framework,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "output": output[:5000],
            "exit_code": exit_code,
        },
    )


def diagnostics(
    errors: int, warnings: int, entries: list
) -> AgentEvent:
    return AgentEvent(
        type=EventType.DIAGNOSTICS,
        data={
            "errors": errors,
            "warnings": warnings,
            "entries": entries[:30],
        },
    )


def status_change(status: str, message: str = "") -> AgentEvent:
    return AgentEvent(
        type=EventType.STATUS_CHANGE, data={"status": status, "message": message}
    )


def agent_done(usage: dict | None = None, summary: str = "") -> AgentEvent:
    return AgentEvent(
        type=EventType.DONE,
        data={"usage": usage or {}, "summary": summary},
    )


def agent_error(error: str, recoverable: bool = True) -> AgentEvent:
    return AgentEvent(
        type=EventType.ERROR,
        data={"error": error, "recoverable": recoverable},
    )


# ---------------------------------------------------------------------------
# AgentEventBus — fan-out event bus per session
# ---------------------------------------------------------------------------


class AgentEventBus:
    """
    Fan-out event bus. The executor emits events; multiple consumers
    (WebSocket, SSE, polling) subscribe via async queues.

    Thread-safe: events are pushed through asyncio.Queue per subscriber.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[str, asyncio.Queue[AgentEvent]] = {}

    def subscribe(self) -> tuple[str, asyncio.Queue[AgentEvent]]:
        """Create a new subscription. Returns (sub_id, queue)."""
        sub_id = uuid.uuid4().hex[:8]
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=2000)
        self._subscribers[sub_id] = queue
        return sub_id, queue

    def unsubscribe(self, sub_id: str) -> None:
        self._subscribers.pop(sub_id, None)

    async def emit(self, event: AgentEvent) -> None:
        """Push event to all subscribers (non-blocking drop on full)."""
        for queue in self._subscribers.values():
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("AgentEventBus: subscriber queue full, dropping event")

    async def stream(
        self,
        sub_id: str,
        *,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SEC,
        metrics: Optional["StreamMetrics"] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Yield events for a subscriber, with a heartbeat while it is quiet.

        The heartbeat interval came down from 25s to 15s in Batch V4-E4: proxies
        and load balancers commonly idle-timeout a connection at 30 or 60 seconds,
        and a 25-second beat leaves no room for one late beat. ``streaming.py``
        had already worked this out and used 15; it just had no way to tell this
        bus, because the two were parallel implementations of the same job.

        *metrics* records first-byte and total timing when a caller wants it,
        which is what makes "did streaming actually get faster?" answerable
        instead of asserted.
        """
        queue = self._subscribers.get(sub_id)
        if not queue:
            return
        try:
            while sub_id in self._subscribers:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=heartbeat_interval,
                    )
                    if metrics is not None:
                        metrics.observe(event)
                    yield event
                except asyncio.TimeoutError:
                    yield AgentEvent(
                        type=EventType.STATUS_CHANGE,
                        data={"status": "keepalive"},
                    )
        finally:
            self.unsubscribe(sub_id)


# ---------------------------------------------------------------------------
# Global bus registry (one bus per session)
# ---------------------------------------------------------------------------

_session_buses: Dict[str, AgentEventBus] = {}


def get_bus(session_id: str) -> AgentEventBus:
    """Get or create the event bus for a session."""
    if session_id not in _session_buses:
        _session_buses[session_id] = AgentEventBus()
    return _session_buses[session_id]


def remove_bus(session_id: str) -> None:
    """Clean up the event bus for a session."""
    _session_buses.pop(session_id, None)
