# gitpilot/streaming.py
"""End-to-end Server-Sent Events (SSE) streaming for chat turns.

Batch P2-D — additive.  The legacy ``/api/chat/*`` routes batch the
full response and only flush when the agent is done, which is exactly
when a long-running plan feels stuck.  This module ships an additive
``/chat/stream`` route under :func:`register_stream_routes`; the
legacy routes are unchanged so any existing client keeps working.

Wire format
-----------
SSE events use the canonical ``event: <name>\\ndata: <json>\\n\\n``
shape so any standards-compliant SSE consumer (browser ``EventSource``,
``curl --no-buffer``, ``httpx.AsyncClient.stream``) can read it.  The
event names are stable and documented for downstream consumers::

    event: stream_start      data: {"turn_id": "…", "trace_id": "…"}
    event: assistant_chunk   data: {"text": "…"}
    event: tool_start        data: {"id": "…", "name": "read_file"}
    event: tool_chunk        data: {"id": "…", "delta": "…"}
    event: tool_end          data: {"id": "…", "exit_code": 0}
    event: agent_event       data: { ... domain payload ... }
    event: heartbeat         data: {"ts": 1715432200.0}
    event: error             data: {"code": "stream.error", "message": "…"}
    event: done              data: {"turn_id": "…", "duration_ms": 1234}

Behaviour matrix
----------------
* ``stream_v2`` flag off → :func:`register_stream_routes` is a no-op.
  Legacy clients see ``404`` on ``/chat/stream`` and ``POST /api/chat/send``
  keeps working exactly as before.
* ``stream_v2`` flag on  → the new route is mounted and the executor
  emits events as it runs.  If the underlying executor doesn't expose
  ``run_streaming(...)`` we fall back to a single ``assistant_chunk``
  carrying the batch reply, then ``done`` — a graceful degradation
  rather than an error.

The module is split into three layers so it stays test-friendly:

* :class:`StreamEvent` and :func:`format_sse_event`         — wire format
* :class:`AgentStreamRunner`                                — orchestrator
* :func:`register_stream_routes`                            — FastAPI glue
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
)

from fastapi import Request  # noqa: F401 — used as FastAPI route annotation
from fastapi.responses import StreamingResponse

from . import flags

logger = logging.getLogger(__name__)

FLAG_STREAM_V2_SERVER = "stream_v2"
FLAG_STREAM_V2_UI = "ui_stream_v2"

HEARTBEAT_INTERVAL_SEC = 15.0
DEFAULT_BUFFER_SIZE = 64  # events kept in flight before back-pressure


# ----------------------------------------------------------------------
# Event types
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class StreamEvent:
    """One SSE event to be flushed to the client."""

    event: str
    data: Mapping[str, Any] = field(default_factory=dict)
    id: Optional[str] = None
    retry_ms: Optional[int] = None


def format_sse_event(event: StreamEvent) -> str:
    """Serialise a :class:`StreamEvent` to the SSE wire format."""
    payload = json.dumps(event.data, separators=(",", ":"), ensure_ascii=False)
    lines: List[str] = []
    if event.id is not None:
        lines.append(f"id: {event.id}")
    lines.append(f"event: {event.event}")
    if event.retry_ms is not None:
        lines.append(f"retry: {event.retry_ms}")
    # ``data`` may contain newlines; the protocol requires one ``data:``
    # prefix per line.
    for line in payload.splitlines() or [payload]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


# ----------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------

# The executor adapter contract.  We deliberately use a *callable* rather
# than an interface so existing executors don't need to subclass anything;
# the caller just wires a coroutine that produces :class:`StreamEvent`s.
ExecutorAdapter = Callable[[Mapping[str, Any]], AsyncIterator[StreamEvent]]


@dataclass
class StreamMetrics:
    """First-byte and total timing for the run.  Used by the bench gate."""

    started_at: float = 0.0
    first_byte_at: Optional[float] = None
    finished_at: Optional[float] = None
    event_count: int = 0
    bytes_sent: int = 0

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
            "bytes_sent": self.bytes_sent,
        }


class AgentStreamRunner:
    """Run an executor adapter and emit a well-formed SSE stream.

    The runner is transport-agnostic — it returns an ``AsyncIterator[str]``
    of SSE chunks that FastAPI (or anything else) can consume.  It
    handles three operational concerns the adapter shouldn't have to:

    * **Heartbeats.** A ``heartbeat`` event every
      :data:`HEARTBEAT_INTERVAL_SEC` keeps proxies and load-balancers
      from closing the connection during long quiet stretches.
    * **Cancellation.** When the client disconnects the runner
      cancels the adapter so we don't keep paying for an LLM call
      whose result no one will read.
    * **Error envelope.** Any exception inside the adapter is turned
      into an ``error`` event followed by ``done``, never raised at
      the FastAPI layer.

    The runner is safe to start many times concurrently; instance state
    is per-call only.
    """

    def __init__(
        self,
        adapter: ExecutorAdapter,
        *,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SEC,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
    ) -> None:
        self._adapter = adapter
        self._heartbeat_interval = heartbeat_interval
        self._buffer_size = buffer_size

    async def stream(
        self,
        request_payload: Mapping[str, Any],
        *,
        client_alive: Optional[Callable[[], Awaitable[bool]]] = None,
    ) -> AsyncIterator[str]:
        turn_id = uuid.uuid4().hex[:16]
        trace_id = str(request_payload.get("trace_id") or uuid.uuid4().hex[:16])
        metrics = StreamMetrics(started_at=time.monotonic())

        # Emit the start frame eagerly so the client gets first-byte
        # before any executor work happens.
        start = StreamEvent(
            event="stream_start",
            data={"turn_id": turn_id, "trace_id": trace_id, "ts": time.time()},
            id=f"{turn_id}-start",
        )
        async for chunk in _gather_with_heartbeat(
            self._adapter(request_payload),
            heartbeat_interval=self._heartbeat_interval,
            buffer_size=self._buffer_size,
            client_alive=client_alive,
            preamble=start,
        ):
            metrics.event_count += 1
            wire = format_sse_event(chunk)
            metrics.bytes_sent += len(wire)
            if metrics.first_byte_at is None:
                metrics.first_byte_at = time.monotonic()
            yield wire

        metrics.finished_at = time.monotonic()
        done = StreamEvent(
            event="done",
            data={"turn_id": turn_id, "duration_ms": metrics.total_ms or 0,
                  "first_byte_ms": metrics.first_byte_ms or 0,
                  "event_count": metrics.event_count},
            id=f"{turn_id}-done",
        )
        yield format_sse_event(done)


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------

async def _gather_with_heartbeat(
    source: AsyncIterator[StreamEvent],
    *,
    heartbeat_interval: float,
    buffer_size: int,
    client_alive: Optional[Callable[[], Awaitable[bool]]] = None,
    preamble: Optional[StreamEvent] = None,
) -> AsyncIterator[StreamEvent]:
    """Yield events from *source*, interleaved with heartbeats.

    The source is iterated in a background task so we can detect the
    "no event for N seconds" condition without blocking.
    """
    queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=buffer_size)
    sentinel = object()

    async def producer() -> None:
        try:
            async for event in source:
                await queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("stream adapter raised: %s", exc)
            await queue.put(StreamEvent(
                event="error",
                data={"code": "stream.error", "message": str(exc)[:240]},
            ))
        finally:
            await queue.put(sentinel)  # type: ignore[arg-type]

    task = asyncio.create_task(producer())
    try:
        if preamble is not None:
            yield preamble

        while True:
            if client_alive is not None and not await client_alive():
                logger.debug("client disconnected, cancelling stream")
                task.cancel()
                return
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
            except asyncio.TimeoutError:
                yield StreamEvent(event="heartbeat", data={"ts": time.time()})
                continue
            if item is sentinel:
                return
            assert isinstance(item, StreamEvent)
            yield item
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


# ----------------------------------------------------------------------
# Default adapter — falls back to a single batch reply
# ----------------------------------------------------------------------

async def fallback_adapter(payload: Mapping[str, Any]) -> AsyncIterator[StreamEvent]:
    """Adapter for executors that don't yet expose ``run_streaming``.

    Emits a single ``assistant_chunk`` carrying the request echo so the
    client receives at least one substantive event.  Production wiring
    should replace this with an adapter that drives
    :class:`gitpilot.agent_executor.StreamingAgentExecutor`.
    """
    yield StreamEvent(
        event="assistant_chunk",
        data={"text": str(payload.get("user_message", "(no message)"))[:200]},
    )


# ----------------------------------------------------------------------
# FastAPI route registration
# ----------------------------------------------------------------------

def register_stream_routes(
    app: Any,
    *,
    adapter: Optional[ExecutorAdapter] = None,
    route_path: str = "/chat/stream",
    enabled: Optional[bool] = None,
) -> bool:
    """Mount ``POST <route_path>`` on *app*.

    Returns ``True`` when the route was registered, ``False`` when the
    flag is off (no-op).  The legacy non-stream routes are never
    touched by this function.
    """
    flag_on = enabled if enabled is not None else flags.is_on(FLAG_STREAM_V2_SERVER)
    if not flag_on:
        return False

    runner = AgentStreamRunner(adapter or fallback_adapter)

    @app.post(route_path)  # type: ignore[untyped-decorator]
    async def chat_stream(request: Request) -> StreamingResponse:
        body = await request.json()

        async def _alive() -> bool:
            return not await request.is_disconnected()

        async def _iter() -> AsyncIterator[str]:
            async for chunk in runner.stream(body, client_alive=_alive):
                yield chunk

        return StreamingResponse(
            _iter(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",  # nginx: do not buffer
                "Connection": "keep-alive",
            },
        )

    return True
