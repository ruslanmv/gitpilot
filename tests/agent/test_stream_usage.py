# tests/agent/test_stream_usage.py
"""Token usage on streamed OpenAI-compatible responses.

Found by the first real-model benchmark run: the REACT_TEXT tier streamed every
turn and its journal recorded zero tokens, because OpenAI, vLLM, llama.cpp and
Ollama all leave ``usage`` out of a stream unless ``stream_options.include_usage``
asks for it — and then send it in a trailer chunk with no ``choices``, which the
parser was dropping as framing noise.
"""
from __future__ import annotations

import json

from gitpilot.agent.providers import OpenAICompatibleProvider
from gitpilot.agent.providers.openai_compat import _parse_stream_line

from .replay import ReplayTransport, sse_response
from .test_providers import _collect, _openai


def _body(stream: bool) -> dict:
    return OpenAICompatibleProvider(base_url="http://x/v1", model="m").build_body(
        [{"role": "user", "content": "hi"}], stream=stream,
    )


class TestStreamUsageIsRequested:
    def test_a_streamed_request_asks_for_usage(self):
        assert _body(stream=True)["stream_options"] == {"include_usage": True}

    def test_a_plain_request_does_not_carry_stream_options(self):
        """Servers answer a non-streamed request with usage already; the option
        only means something with ``stream: true`` and some reject it otherwise."""
        assert "stream_options" not in _body(stream=False)


class TestTheUsageTrailerIsKept:
    def test_a_choiceless_usage_chunk_becomes_a_usage_chunk(self):
        line = "data: " + json.dumps({
            "id": "x", "choices": [],
            "usage": {"prompt_tokens": 120, "completion_tokens": 7, "total_tokens": 127},
        })
        chunk = _parse_stream_line(line)

        assert chunk is not None
        assert chunk.text == ""
        assert chunk.usage is not None
        assert (chunk.usage.prompt_tokens, chunk.usage.completion_tokens) == (120, 7)

    def test_a_choiceless_chunk_without_usage_is_still_noise(self):
        assert _parse_stream_line('data: {"id": "x", "choices": []}') is None

    def test_the_stream_surfaces_the_counts_to_the_dialects(self):
        """NATIVE and REACT_TEXT both read ``chunk.usage`` off the stream, so the
        trailer has to come through as a chunk rather than be summed here."""
        replay = ReplayTransport([sse_response([
            'data: {"choices": [{"delta": {"content": "Hel"}}]}',
            'data: {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]}',
            'data: {"choices": [], "usage": {"prompt_tokens": 42, "completion_tokens": 2}}',
            "data: [DONE]",
        ])])
        chunks = _collect(_openai(replay))

        usage = [chunk.usage for chunk in chunks if chunk.usage is not None]
        assert len(usage) == 1
        assert (usage[0].prompt_tokens, usage[0].completion_tokens) == (42, 2)
        assert "".join(chunk.text for chunk in chunks) == "Hello"
        assert chunks[-1].done is True
