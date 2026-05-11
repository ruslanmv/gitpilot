"""Tests for SSE streaming — Batch P2-D."""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Iterator, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitpilot import flags
from gitpilot.streaming import (
    FLAG_STREAM_V2_SERVER,
    AgentStreamRunner,
    StreamEvent,
    fallback_adapter,
    format_sse_event,
    register_stream_routes,
)


@pytest.fixture(autouse=True)
def _isolate_flags() -> Iterator[None]:
    flags.clear_all_overrides()
    yield
    flags.clear_all_overrides()


# ----------------------------------------------------------------------
# Wire format
# ----------------------------------------------------------------------

def test_format_sse_event_basic() -> None:
    wire = format_sse_event(StreamEvent(event="assistant_chunk", data={"text": "hi"}))
    assert "event: assistant_chunk" in wire
    assert 'data: {"text":"hi"}' in wire
    assert wire.endswith("\n\n")


def test_format_sse_event_with_id_and_retry() -> None:
    wire = format_sse_event(StreamEvent(event="x", data={"v": 1}, id="abc", retry_ms=1500))
    assert "id: abc" in wire
    assert "retry: 1500" in wire


def test_format_sse_event_splits_data_on_newlines() -> None:
    wire = format_sse_event(StreamEvent(event="line", data={"text": "one\ntwo"}))
    # The serialised JSON keeps the \n as a literal, so the split-on-line
    # logic should not produce multiple ``data:`` lines unless the
    # serialised payload itself contains a real newline.  We assert the
    # JSON contains an escaped newline, not a raw one.
    assert "\\n" in wire
    assert wire.count("data: ") == 1


# ----------------------------------------------------------------------
# Runner — event lifecycle
# ----------------------------------------------------------------------

async def _collect(runner: AgentStreamRunner, payload: dict) -> List[str]:
    out: List[str] = []
    async for chunk in runner.stream(payload):
        out.append(chunk)
    return out


def test_runner_emits_start_then_chunks_then_done() -> None:
    async def adapter(payload):
        yield StreamEvent(event="assistant_chunk", data={"text": "hello"})
        yield StreamEvent(event="assistant_chunk", data={"text": "world"})

    runner = AgentStreamRunner(adapter, heartbeat_interval=10.0)
    chunks = asyncio.run(_collect(runner, {"user_message": "hi"}))
    events = [c.split("event: ", 1)[1].split("\n", 1)[0] for c in chunks]
    assert events[0] == "stream_start"
    assert events[-1] == "done"
    assert events.count("assistant_chunk") == 2


def test_runner_translates_adapter_exception_into_error_event() -> None:
    async def boom(_payload):
        yield StreamEvent(event="assistant_chunk", data={"text": "before"})
        raise RuntimeError("kaboom")

    runner = AgentStreamRunner(boom, heartbeat_interval=10.0)
    chunks = asyncio.run(_collect(runner, {}))
    body = "\n".join(chunks)
    assert "event: error" in body
    assert "kaboom" in body
    assert "event: done" in body


def test_runner_emits_heartbeats_when_idle() -> None:
    async def slow(_payload):
        await asyncio.sleep(0.15)
        yield StreamEvent(event="assistant_chunk", data={"text": "late"})

    runner = AgentStreamRunner(slow, heartbeat_interval=0.05)
    chunks = asyncio.run(_collect(runner, {}))
    body = "\n".join(chunks)
    assert "event: heartbeat" in body


def test_runner_cancels_adapter_on_client_disconnect() -> None:
    cancelled = asyncio.Event()

    async def stalls(_payload):
        try:
            await asyncio.sleep(5)
            yield StreamEvent(event="assistant_chunk", data={"text": "never"})
        except asyncio.CancelledError:
            cancelled.set()
            raise

    runner = AgentStreamRunner(stalls, heartbeat_interval=0.05)

    async def driver() -> List[str]:
        out: List[str] = []
        alive_calls = 0

        async def alive() -> bool:
            nonlocal alive_calls
            alive_calls += 1
            return alive_calls < 2  # disconnect after the first probe

        async for chunk in runner.stream({}, client_alive=alive):
            out.append(chunk)
            if "stream_start" in chunk:
                break
        # Allow producer task to receive cancellation.
        await asyncio.sleep(0.1)
        return out

    asyncio.run(driver())
    # The producer was cancelled on the disconnect probe.
    assert cancelled.is_set()


# ----------------------------------------------------------------------
# Fallback adapter — adopted when an executor doesn't expose run_streaming
# ----------------------------------------------------------------------

def test_fallback_adapter_yields_assistant_chunk() -> None:
    async def collect() -> List[StreamEvent]:
        return [e async for e in fallback_adapter({"user_message": "ping"})]
    events = asyncio.run(collect())
    assert events and events[0].event == "assistant_chunk"
    assert events[0].data["text"] == "ping"


# ----------------------------------------------------------------------
# FastAPI integration
# ----------------------------------------------------------------------

def _build_app(events: List[StreamEvent]) -> FastAPI:
    async def adapter(_payload) -> AsyncIterator[StreamEvent]:
        for ev in events:
            yield ev

    app = FastAPI()
    flags.set_override(FLAG_STREAM_V2_SERVER, True)
    registered = register_stream_routes(app, adapter=adapter)
    assert registered is True
    return app


def test_route_emits_event_stream_media_type() -> None:
    app = _build_app([StreamEvent(event="assistant_chunk", data={"text": "ok"})])
    client = TestClient(app)
    resp = client.post("/chat/stream", json={"user_message": "hi"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "event: stream_start" in resp.text
    assert "event: assistant_chunk" in resp.text
    assert "event: done" in resp.text


def test_route_not_registered_when_flag_off() -> None:
    app = FastAPI()
    flags.set_override(FLAG_STREAM_V2_SERVER, False)
    registered = register_stream_routes(app)
    assert registered is False
    client = TestClient(app)
    assert client.post("/chat/stream", json={}).status_code == 404


def test_done_event_carries_metrics() -> None:
    app = _build_app([StreamEvent(event="assistant_chunk", data={"text": "ok"})])
    client = TestClient(app)
    resp = client.post("/chat/stream", json={})
    done_lines = [line for line in resp.text.splitlines() if line.startswith("data: ")]
    # Last data line is the ``done`` payload.
    payload = json.loads(done_lines[-1][len("data: "):])
    assert "duration_ms" in payload
    assert "event_count" in payload
    assert payload["event_count"] >= 1
