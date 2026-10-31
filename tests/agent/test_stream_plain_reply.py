"""Streaming against a server that ignores ``stream: true`` (issue GP-003).

OllaBridge answers a streaming request with one plain JSON completion. Read as
Server-Sent Events that body yields no lines starting with ``data:``, so the
provider produced zero chunks and the dialects built an empty turn, with no
error anywhere. The provider now replays a plain completion as chunks.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from gitpilot.agent.dialects.native import NativeAdapter
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.providers import OpenAICompatibleProvider
from gitpilot.agent.providers.base import MalformedResponse, ToolCallAccumulator
from gitpilot.toolkit.builtins import build_default_registry

from .replay import ReplayTransport, sse_response


def _provider(replay, model="gateway-model"):
    return OpenAICompatibleProvider(base_url="http://gateway.example/v1", model=model, api_key="k",
                                    name="gateway", transport=replay.as_transport())


def _plain(payload):
    """What OllaBridge sends back: a normal completion, not an event stream."""
    return httpx.Response(200, json=payload)


def _collect(provider):
    async def run():
        return [chunk async for chunk in provider.stream([{"role": "user", "content": "hi"}])]
    return asyncio.run(run())


TEXT_REPLY = {"id": "x", "object": "chat.completion",
              "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"}}]}


def test_a_plain_text_reply_streams_as_text_then_done():
    chunks = _collect(_provider(ReplayTransport([_plain(TEXT_REPLY)])))

    assert "".join(c.text for c in chunks) == "pong"
    assert chunks[-1].done is True
    assert chunks[-1].finish_reason == "stop"


def test_usage_and_finish_reason_are_carried_when_the_server_sends_them():
    reply = {"choices": [{"message": {"content": "pong"}, "finish_reason": "length"}],
             "usage": {"prompt_tokens": 12, "completion_tokens": 1}}
    chunks = _collect(_provider(ReplayTransport([_plain(reply)])))

    done = chunks[-1]
    assert done.finish_reason == "length"
    assert (done.usage.prompt_tokens, done.usage.completion_tokens) == (12, 1)


def test_plain_tool_calls_stream_as_fragments_the_accumulator_assembles():
    reply = {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "fs.read", "arguments": "{\"path\": \"README.md\"}"}},
        {"id": "call_2", "type": "function",
         "function": {"name": "fs.grep", "arguments": {"pattern": "TODO"}}},
    ]}, "finish_reason": "tool_calls"}]}
    chunks = _collect(_provider(ReplayTransport([_plain(reply)])))

    accumulator = ToolCallAccumulator()
    for chunk in chunks:
        if chunk.tool_call_index is not None:
            accumulator.add(chunk)
    calls = accumulator.finish()
    assert [(c.id, c.name, c.arguments) for c in calls] == [
        ("call_1", "fs.read", {"path": "README.md"}),
        ("call_2", "fs.grep", {"pattern": "TODO"}),
    ]
    assert chunks[-1].finish_reason == "tool_calls"


def test_reasoning_is_stripped_as_on_the_non_streaming_path():
    reply = {"choices": [{"message": {"content": "<think>let me see</think>\nAnswer: 5"}}]}
    chunks = _collect(_provider(ReplayTransport([_plain(reply)]), model="deepseek-r1:8b"))

    assert "".join(c.text for c in chunks) == "Answer: 5"


def test_a_body_without_choices_is_an_error_not_an_empty_turn():
    with pytest.raises(MalformedResponse):
        _collect(_provider(ReplayTransport([_plain({"error": "something upstream"})])))


def test_an_event_stream_without_its_content_type_still_streams():
    body = b'data: {"choices": [{"delta": {"content": "po"}}]}\n\n' \
           b'data: {"choices": [{"delta": {"content": "ng"}}]}\n\ndata: [DONE]\n\n'
    chunks = _collect(_provider(ReplayTransport([httpx.Response(200, content=body)])))

    assert "".join(c.text for c in chunks) == "pong"
    assert chunks[-1].done is True


def test_a_real_event_stream_is_read_exactly_as_before():
    replay = ReplayTransport([sse_response([
        'data: {"choices": [{"delta": {"content": "po"}}]}',
        'data: {"choices": [{"delta": {"content": "ng"}, "finish_reason": "stop"}]}',
        "data: [DONE]",
    ])])
    chunks = _collect(_provider(replay))

    assert [c.text for c in chunks if c.text] == ["po", "ng"]
    assert chunks[-1].done is True


def test_the_native_dialect_gets_a_real_turn_from_a_plain_reply(tmp_path):
    """The symptom itself: a streamed NATIVE turn through such a server was empty."""
    profile = resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "c.json")
    adapter = NativeAdapter(_provider(ReplayTransport([_plain(TEXT_REPLY)])), profile,
                            build_default_registry())
    streamed = []

    async def on_text(text):
        streamed.append(text)

    turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "hi"}], on_text=on_text))

    assert turn.text == "pong"
    assert streamed == ["pong"], "the UI sink sees the text too"
