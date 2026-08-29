"""Streaming that is actually streaming — Batch V4-E4.

The thing being replaced was a costume. ``StreamingAgentExecutor`` waited for a
batch result, then sliced the finished text into 80-character pieces with a 15 ms
sleep between them and emitted those as ``text_delta``. Every visible property of
streaming was there except the one that matters: the first token still arrived
after the whole run. It also made the *absence* of real streaming invisible, which
is why it survived so long.
"""
from __future__ import annotations

import asyncio
import inspect
import time

import pytest

from gitpilot.agent.dialects.lite import LiteAdapter
from gitpilot.agent.dialects.react_text import ReactTextAdapter
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.providers.base import ProviderResponse, StreamChunk
from gitpilot.agent_events import (
    HEARTBEAT_INTERVAL_SEC,
    AgentEventBus,
    StreamMetrics,
    agent_done,
    text_delta,
)


class _StreamingProvider:
    name = "ollama"
    model = "llama3:8b"
    supports_native_tools = False
    supports_parallel_tool_calls = False
    supports_streaming = True

    def __init__(self, pieces):
        self.pieces = list(pieces)
        self.completes = 0

    async def complete(self, messages, *, tools=None, system=None, **opts):
        self.completes += 1
        return ProviderResponse(text="".join(self.pieces))

    async def stream(self, messages, *, tools=None, system=None, **opts):
        for piece in self.pieces:
            yield StreamChunk(text=piece)


# ---------------------------------------------------------------------------
# The chunker is gone
# ---------------------------------------------------------------------------


class TestNoFakeChunking:
    def test_the_timing_signature_is_gone(self):
        """80 characters, 15 ms apart, forever — the giveaway that this was a
        replay of a finished string rather than a live one."""
        source = inspect.getsource(
            __import__("gitpilot.agent_executor", fromlist=["x"]),
        )
        assert "chunk_size = 80" not in source
        assert "asyncio.sleep(0.015)" not in source

    def test_the_batch_path_sends_its_answer_once(self):
        source = inspect.getsource(
            __import__("gitpilot.agent_executor", fromlist=["x"]),
        )
        assert "in one event" in source, (
            "the honest one-shot emit should be documented as such"
        )


# ---------------------------------------------------------------------------
# REACT_TEXT — per line
# ---------------------------------------------------------------------------


def _react(tmp_path, pieces):
    provider = _StreamingProvider(pieces)
    profile = resolve_profile("ollama", "llama3:8b", cache_path=tmp_path / "c.json")
    return ReactTextAdapter(provider, profile, None), provider


class TestReactPerLine:
    def _collect(self, adapter):
        lines = []

        async def sink(text):
            lines.append(text)

        turn = asyncio.run(adapter.generate(
            system="s", messages=[{"role": "user", "content": "go"}], on_text=sink,
        ))
        return lines, turn

    def test_prose_is_streamed_a_line_at_a_time(self, tmp_path):
        adapter, _provider = _react(tmp_path, [
            "Thought: I need to ", "look at the file.\n",
            "Thought: then I will read it.\n",
            "Action: fs.read\n", 'Action Input: {"path": "a.py"}\n',
        ])

        lines, turn = self._collect(adapter)

        assert lines == [
            "I need to look at the file.\n",
            "then I will read it.\n",
        ]
        assert [call.tool for call in turn.tool_calls] == ["fs.read"]

    def test_the_grammar_is_not_streamed(self, tmp_path):
        """A client renders a tool card from the parsed turn; watching the model
        type `Action Input:` is watching the protocol, not the work."""
        adapter, _provider = _react(tmp_path, [
            "Action: fs.read\n", 'Action Input: {"path": "a.py"}\n',
        ])

        lines, _turn = self._collect(adapter)

        assert lines == []

    def test_a_partial_final_line_is_still_sent(self, tmp_path):
        adapter, _provider = _react(tmp_path, ["Thought: no trailing newline"])
        lines, _turn = self._collect(adapter)
        assert lines == ["no trailing newline\n"]

    def test_no_sink_means_no_streaming_call(self, tmp_path):
        """A caller that does not want deltas should not pay for a stream."""
        adapter, provider = _react(tmp_path, ["Thought: hello\n"])

        asyncio.run(adapter.generate(
            system="s", messages=[{"role": "user", "content": "go"}],
        ))

        assert provider.completes == 1

    def test_a_provider_that_cannot_stream_falls_back(self, tmp_path):
        class _Liar(_StreamingProvider):
            async def stream(self, *args, **kwargs):
                raise NotImplementedError
                yield   # pragma: no cover - makes this an async generator

        provider = _Liar(["Thought: fine\n"])
        profile = resolve_profile("ollama", "llama3:8b", cache_path=tmp_path / "c.json")
        adapter = ReactTextAdapter(provider, profile, None)

        lines, turn = self._collect(adapter)

        assert provider.completes == 1
        assert lines == []
        assert turn.text


# ---------------------------------------------------------------------------
# LITE — per phase
# ---------------------------------------------------------------------------


class TestLitePerPhase:
    def test_the_phase_is_announced(self, tmp_path):
        """A small model's output is a line protocol; streaming it verbatim shows
        the user `READ path` instead of progress."""
        provider = _StreamingProvider(["READ a.py\n"])
        profile = resolve_profile("ollama", "qwen2.5:1.5b", cache_path=tmp_path / "c.json")
        adapter = LiteAdapter(provider, profile, None)
        adapter.prepare("read a.py", ["a.py"])

        lines = []

        async def sink(text):
            lines.append(text)

        asyncio.run(adapter.generate(
            system="s", messages=[{"role": "user", "content": "go"}], on_text=sink,
        ))

        assert lines == ["Reading the relevant files…\n"]

    def test_a_repeated_phase_does_not_repeat_itself(self, tmp_path):
        provider = _StreamingProvider(["READ a.py\n"])
        profile = resolve_profile("ollama", "qwen2.5:1.5b", cache_path=tmp_path / "c.json")
        adapter = LiteAdapter(provider, profile, None)
        adapter.prepare("read a.py", ["a.py", "b.py"])

        lines = []

        async def sink(text):
            lines.append(text)

        for _ in range(3):
            asyncio.run(adapter.generate(
                system="s", messages=[{"role": "user", "content": "go"}], on_text=sink,
            ))

        assert lines.count("Reading the relevant files…\n") == 1


# ---------------------------------------------------------------------------
# The framing, merged from streaming.py
# ---------------------------------------------------------------------------


class TestFraming:
    def test_the_heartbeat_is_inside_the_usual_proxy_timeout(self):
        """25 seconds left no margin for one late beat against a 30-second idle
        timeout. `streaming.py` had already worked this out and had no way to tell
        this bus, because the two were parallel implementations."""
        assert HEARTBEAT_INTERVAL_SEC == 15.0

    def test_first_byte_and_total_are_recorded(self):
        async def drive():
            bus = AgentEventBus()
            sub_id, _queue = bus.subscribe()
            metrics = StreamMetrics()

            async def feed():
                await bus.emit(text_delta("hello"))
                await bus.emit(agent_done(summary="done"))

            asyncio.create_task(feed())
            async for event in bus.stream(sub_id, metrics=metrics):
                if event.type.value in ("done", "error"):
                    break
            return metrics

        metrics = asyncio.run(drive())

        assert metrics.first_byte_ms is not None
        assert metrics.total_ms is not None
        assert metrics.event_count == 2

    def test_a_heartbeat_does_not_count_as_the_first_byte(self):
        """Otherwise a slow model looks instant."""
        metrics = StreamMetrics()
        from gitpilot.agent_events import AgentEvent, EventType

        metrics.observe(AgentEvent(
            type=EventType.STATUS_CHANGE, data={"status": "keepalive"},
        ))

        assert metrics.first_byte_ms is None
        assert metrics.event_count == 1

    def test_a_quiet_stream_beats(self):
        async def drive():
            bus = AgentEventBus()
            sub_id, _queue = bus.subscribe()
            beats = []
            async for event in bus.stream(sub_id, heartbeat_interval=0.01):
                beats.append(event.data.get("status"))
                if len(beats) >= 2:
                    break
            return beats

        assert asyncio.run(asyncio.wait_for(drive(), timeout=5)) == [
            "keepalive", "keepalive",
        ]

    def test_a_disconnect_ends_the_stream(self):
        """Unsubscribing is what a disconnected client amounts to; the generator
        must finish rather than beat forever."""
        async def drive():
            bus = AgentEventBus()
            sub_id, _queue = bus.subscribe()
            seen = []

            async def disconnect():
                await asyncio.sleep(0.02)
                bus.unsubscribe(sub_id)

            asyncio.create_task(disconnect())
            async for event in bus.stream(sub_id, heartbeat_interval=0.01):
                seen.append(event)
                if len(seen) > 20:  # pragma: no cover - the failure mode
                    break
            return seen

        seen = asyncio.run(asyncio.wait_for(drive(), timeout=5))
        assert len(seen) <= 20, "the stream did not end when the client left"
