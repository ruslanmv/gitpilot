"""The NATIVE dialect — Batch V4-B3."""
from __future__ import annotations

import asyncio
import json

import pytest

from gitpilot.agent.adapter import build_adapter
from gitpilot.agent.contracts import Dialect, Finality
from gitpilot.agent.dialects.native import NativeAdapter
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.providers import AnthropicProvider, OpenAICompatibleProvider
from gitpilot.toolkit import ToolCall, ToolResult
from gitpilot.toolkit.builtins import build_default_registry
from gitpilot.toolkit.registry import capabilities_from

from .replay import ReplayTransport, json_response, sse_lines_from_fixture, sse_response


@pytest.fixture()
def registry():
    return build_default_registry()


@pytest.fixture()
def profile(tmp_path):
    return resolve_profile("claude", "claude-sonnet-4-5", cache_path=tmp_path / "c.json")


def _openai_adapter(replay, registry, profile):
    provider = OpenAICompatibleProvider(
        base_url="https://api.example/v1", model="gpt-4o-mini",
        api_key="k", name="openai", transport=replay.as_transport(),
    )
    return NativeAdapter(provider, profile, registry)


def _anthropic_adapter(replay, registry, profile):
    provider = AnthropicProvider(
        model="claude-sonnet-4-5", api_key="k", transport=replay.as_transport(),
    )
    return NativeAdapter(provider, profile, registry)


class TestSchemaTranslation:
    def test_registry_schemas_reach_the_openai_wire_shape(self, registry, profile):
        replay = ReplayTransport([json_response("openai_text")])
        adapter = _openai_adapter(replay, registry, profile)

        asyncio.run(adapter.generate(messages=[{"role": "user", "content": "hi"}], stream=False))

        names = [t["function"]["name"] for t in replay.last.body["tools"]]
        assert "fs.read" in names and "terminal.run" in names
        read = next(t for t in replay.last.body["tools"] if t["function"]["name"] == "fs.read")
        assert read["function"]["parameters"]["required"] == ["path"]

    def test_registry_schemas_reach_the_anthropic_wire_shape(self, registry, profile):
        replay = ReplayTransport([json_response("anthropic_text")])
        adapter = _anthropic_adapter(replay, registry, profile)

        asyncio.run(adapter.generate(messages=[{"role": "user", "content": "hi"}], stream=False))

        tools = replay.last.body["tools"]
        assert {"name", "description", "input_schema"} <= set(tools[0])
        assert all("function" not in tool for tool in tools)

    def test_capability_mask_shrinks_what_the_model_is_offered(self, registry, profile):
        replay = ReplayTransport([json_response("openai_text")])
        adapter = _openai_adapter(replay, registry, profile)

        asyncio.run(adapter.generate(
            messages=[{"role": "user", "content": "hi"}],
            capabilities=capabilities_from({"fs.read", "fs.grep"}),
            stream=False,
        ))

        names = {t["function"]["name"] for t in replay.last.body["tools"]}
        assert names == {"fs.read", "fs.grep"}

    def test_schema_budget_is_honoured_and_reported(self, registry, tmp_path):
        """A small-window model must not be handed the whole toolbox."""
        small = resolve_profile("ollama", "phi3:mini", cache_path=tmp_path / "c.json")
        native = small.__class__(**{**small.to_dict(), "dialect": Dialect.NATIVE})  # type: ignore[arg-type]
        replay = ReplayTransport([json_response("openai_text")])
        adapter = _openai_adapter(replay, registry, small)

        defs = adapter.tool_defs()
        dropped = adapter.dropped_tools()

        assert len(defs) == small.max_tool_schemas
        assert dropped, "withheld tools must be reported, not silently dropped"


class TestTurnAssembly:
    def test_no_tool_calls_means_final(self, registry, profile):
        replay = ReplayTransport([json_response("openai_text")])
        adapter = _openai_adapter(replay, registry, profile)

        turn = asyncio.run(adapter.generate(
            messages=[{"role": "user", "content": "hi"}], stream=False,
        ))

        assert turn.finality is Finality.FINAL
        assert turn.is_final
        assert turn.text == "The tests live in tests/."
        assert turn.usage.prompt_tokens == 120

    def test_a_tool_call_means_continue(self, registry, profile):
        replay = ReplayTransport([json_response("openai_tool_call")])
        adapter = _openai_adapter(replay, registry, profile)

        turn = asyncio.run(adapter.generate(
            messages=[{"role": "user", "content": "hi"}], stream=False,
        ))

        assert turn.finality is Finality.CONTINUE
        assert not turn.is_final
        assert turn.tool_calls[0].tool == "fs.read"
        assert turn.tool_calls[0].arguments == {"path": "README.md"}

    def test_parallel_calls_survive_when_the_model_supports_them(self, registry, profile):
        replay = ReplayTransport([json_response("openai_parallel_tool_calls")])
        adapter = _openai_adapter(replay, registry, profile)

        turn = asyncio.run(adapter.generate(
            messages=[{"role": "user", "content": "hi"}], stream=False,
        ))

        assert [c.arguments["path"] for c in turn.tool_calls] == ["a.py", "b.py"]

    def test_serial_only_model_keeps_one_call(self, registry, tmp_path):
        """Running the rest against state the first call changed is how a model
        ends up acting on a file it has already rewritten."""
        from dataclasses import replace

        profile = replace(
            resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "c.json"),
            supports_parallel_tool_calls=False,
        )
        replay = ReplayTransport([json_response("openai_parallel_tool_calls")])
        adapter = _openai_adapter(replay, registry, profile)

        turn = asyncio.run(adapter.generate(
            messages=[{"role": "user", "content": "hi"}], stream=False,
        ))

        assert len(turn.tool_calls) == 1


class TestStreaming:
    def test_text_deltas_are_forwarded_as_they_arrive(self, registry, profile):
        """This replaces chunking an already-complete answer 80 chars at a time."""
        replay = ReplayTransport([sse_response(sse_lines_from_fixture("openai_stream_text"))])
        adapter = _openai_adapter(replay, registry, profile)
        seen = []

        async def sink(text):
            seen.append(text)

        turn = asyncio.run(adapter.generate(
            messages=[{"role": "user", "content": "hi"}], on_text=sink, stream=True,
        ))

        assert seen == ["Look", "ing at ", "the tests."]
        assert turn.text == "Looking at the tests."
        assert turn.finality is Finality.FINAL

    def test_tool_call_deltas_assemble_across_chunk_boundaries(self, registry, profile):
        replay = ReplayTransport([sse_response(sse_lines_from_fixture("openai_stream_tool_call"))])
        adapter = _openai_adapter(replay, registry, profile)

        async def sink(text):
            pass

        turn = asyncio.run(adapter.generate(
            messages=[{"role": "user", "content": "hi"}], on_text=sink, stream=True,
        ))

        assert turn.tool_calls[0].tool == "fs.grep"
        assert turn.tool_calls[0].arguments == {"pattern": "def greet"}
        assert turn.finality is Finality.CONTINUE

    def test_anthropic_stream_produces_the_same_turn_shape(self, registry, profile):
        replay = ReplayTransport([sse_response(sse_lines_from_fixture("anthropic_stream_tool_use"))])
        adapter = _anthropic_adapter(replay, registry, profile)

        async def sink(text):
            pass

        turn = asyncio.run(adapter.generate(
            messages=[{"role": "user", "content": "hi"}], on_text=sink, stream=True,
        ))

        assert turn.text == "Reading it."
        assert turn.tool_calls[0].arguments == {"path": "Makefile"}


class TestMessageShapes:
    def test_assistant_turn_is_recorded_provider_neutrally(self, registry, profile):
        """History stays OpenAI-ish so a resumed run can change provider."""
        replay = ReplayTransport([json_response("openai_tool_call")])
        adapter = _openai_adapter(replay, registry, profile)
        turn = asyncio.run(adapter.generate(
            messages=[{"role": "user", "content": "hi"}], stream=False,
        ))

        message = adapter.assistant_message(turn)

        assert message["role"] == "assistant"
        assert message["tool_calls"][0]["function"]["name"] == "fs.read"
        assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"path": "README.md"}

    def test_tool_results_use_the_tool_role(self, registry, profile):
        replay = ReplayTransport([])
        adapter = _openai_adapter(replay, registry, profile)
        call = ToolCall(id="c1", tool="fs.read", arguments={"path": "a"})

        messages = adapter.result_messages(call, ToolResult.success(call, "file body"))

        assert messages == [{
            "role": "tool", "tool_call_id": "c1", "name": "fs.read", "content": "file body",
        }]

    def test_a_silent_turn_records_nothing(self, registry, profile):
        from gitpilot.agent.contracts import AgentTurn

        adapter = _openai_adapter(ReplayTransport([]), registry, profile)
        assert adapter.assistant_message(AgentTurn()) is None

    def test_conclude_is_false_for_this_dialect(self, registry, profile):
        """Only LITE produces FINAL_AFTER_TOOLS turns."""
        from gitpilot.agent.contracts import AgentTurn

        adapter = _openai_adapter(ReplayTransport([]), registry, profile)
        assert adapter.conclude(AgentTurn(), []) is False


class TestPromptCacheIntegration:
    def test_cache_markers_reach_anthropic_only(self, registry, profile):
        """Batch P2-A built these blocks and nothing ever sent them."""
        class _Payload:
            def to_anthropic_system(self):
                return [{"type": "text", "text": "prefix", "cache_control": {"type": "ephemeral"}}]

            def to_text(self):
                return "prefix"

        anthropic_replay = ReplayTransport([json_response("anthropic_text")])
        asyncio.run(_anthropic_adapter(anthropic_replay, registry, profile).generate(
            messages=[{"role": "user", "content": "hi"}], system=_Payload(), stream=False,
        ))
        assert anthropic_replay.last.body["system"][0]["cache_control"] == {"type": "ephemeral"}

        openai_replay = ReplayTransport([json_response("openai_text")])
        asyncio.run(_openai_adapter(openai_replay, registry, profile).generate(
            messages=[{"role": "user", "content": "hi"}], system=_Payload(), stream=False,
        ))
        system_message = openai_replay.last.body["messages"][0]
        assert system_message["role"] == "system"
        assert "cache_control" not in json.dumps(system_message)


class TestDispatch:
    def test_native_profile_on_a_provider_without_tools_is_corrected(self, registry, tmp_path):
        """watsonx does not claim native tools; a mis-set models.yaml must not
        produce a run where every call comes back malformed."""
        from gitpilot.agent.dialects.react_text import ReactTextAdapter
        from gitpilot.agent.providers import WatsonxProvider

        profile = resolve_profile("claude", "claude-sonnet-4-5", cache_path=tmp_path / "c.json")
        adapter = build_adapter(WatsonxProvider(model="granite", api_key="k"), profile, registry)

        assert isinstance(adapter, ReactTextAdapter)
