"""Provider clients — Batch V4-B1.

Everything runs through ``httpx.MockTransport`` against captured fixtures: the
real body assembly, headers, error mapping and parsing execute, with no network.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
import pytest

from gitpilot.agent.providers import (
    AnthropicProvider,
    MalformedResponse,
    ModelNotFound,
    OpenAICompatibleProvider,
    ProviderAuthError,
    ProviderNotReachable,
    ProviderQuotaError,
    ToolCallAccumulator,
    ToolDef,
    WatsonxProvider,
    build_provider,
)

from .replay import ReplayTransport, error_response, json_response, sse_lines_from_fixture, sse_response

READ_TOOL = ToolDef(
    name="fs.read",
    description="Read a file.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)


def _openai(transport, model="gpt-4o-mini", **kwargs):
    return OpenAICompatibleProvider(
        base_url="https://api.example/v1", model=model, api_key="sk-test",
        name="openai", transport=transport.as_transport(), **kwargs,
    )


def _collect(provider, **kwargs):
    async def run():
        return [chunk async for chunk in provider.stream([{"role": "user", "content": "hi"}], **kwargs)]

    return asyncio.run(run())


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


class TestOpenAIRequestShape:
    def test_messages_tools_and_system(self):
        replay = ReplayTransport([json_response("openai_text")])
        provider = _openai(replay)

        asyncio.run(provider.complete(
            [{"role": "user", "content": "where are the tests?"}],
            tools=[READ_TOOL], system="You are GitPilot.",
        ))

        body = replay.last.body
        assert body["model"] == "gpt-4o-mini"
        assert body["messages"][0] == {"role": "system", "content": "You are GitPilot."}
        assert body["messages"][1]["content"] == "where are the tests?"
        assert body["tools"] == [{
            "type": "function",
            "function": {
                "name": "fs.read",
                "description": "Read a file.",
                "parameters": READ_TOOL.parameters,
            },
        }]
        assert body["tool_choice"] == "auto"
        assert body["stream"] is False

    def test_authorization_header_and_url(self):
        replay = ReplayTransport([json_response("openai_text")])
        asyncio.run(_openai(replay).complete([{"role": "user", "content": "hi"}]))

        assert replay.last.url == "https://api.example/v1/chat/completions"
        assert replay.last.headers["authorization"] == "Bearer sk-test"

    def test_only_whitelisted_sampling_options_are_forwarded(self):
        """Gateways reject unknown fields, and an odd key is usually a typo."""
        replay = ReplayTransport([json_response("openai_text")])
        asyncio.run(_openai(replay).complete(
            [{"role": "user", "content": "hi"}],
            temperature=0.2, top_p=0.9, nonsense="drop me", frequency_penalty=1,
        ))

        body = replay.last.body
        assert body["temperature"] == 0.2 and body["top_p"] == 0.9
        assert "nonsense" not in body and "frequency_penalty" not in body

    def test_tools_are_omitted_when_the_provider_cannot_use_them(self):
        replay = ReplayTransport([json_response("openai_text")])
        provider = _openai(replay, supports_native_tools=False)

        asyncio.run(provider.complete([{"role": "user", "content": "hi"}], tools=[READ_TOOL]))

        assert "tools" not in replay.last.body


class TestAnthropicRequestShape:
    def test_system_is_a_parameter_and_tools_use_input_schema(self):
        replay = ReplayTransport([json_response("anthropic_text")])
        provider = AnthropicProvider(
            model="claude-sonnet-4-5", api_key="sk-ant", transport=replay.as_transport(),
        )

        asyncio.run(provider.complete(
            [{"role": "user", "content": "hi"}], tools=[READ_TOOL], system="SYS",
        ))

        body = replay.last.body
        assert body["system"] == [{"type": "text", "text": "SYS"}]
        assert body["tools"] == [{
            "name": "fs.read", "description": "Read a file.", "input_schema": READ_TOOL.parameters,
        }]
        assert body["max_tokens"] == 32_000
        assert replay.last.headers["x-api-key"] == "sk-ant"
        assert replay.last.headers["anthropic-version"] == "2023-06-01"

    def test_cache_control_markers_survive_a_prompt_cache_payload(self):
        """Batch P2-A built these blocks; this is the first call that sends them."""
        class _Payload:
            def to_anthropic_system(self):
                return [
                    {"type": "text", "text": "stable prefix",
                     "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": "session tail"},
                ]

        replay = ReplayTransport([json_response("anthropic_text")])
        provider = AnthropicProvider(model="c", api_key="k", transport=replay.as_transport())

        asyncio.run(provider.complete([{"role": "user", "content": "hi"}], system=_Payload()))

        assert replay.last.body["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in replay.last.body["system"][1]

    def test_tool_results_become_user_tool_result_blocks(self):
        """Anthropic has no `role: tool`; the loop keeps one message format."""
        replay = ReplayTransport([json_response("anthropic_text")])
        provider = AnthropicProvider(model="c", api_key="k", transport=replay.as_transport())

        asyncio.run(provider.complete([
            {"role": "user", "content": "read it"},
            {"role": "assistant", "content": "ok", "tool_calls": [
                {"id": "toolu_01", "function": {"name": "fs.read", "arguments": '{"path": "a"}'}},
            ]},
            {"role": "tool", "tool_call_id": "toolu_01", "content": "file body"},
        ]))

        messages = replay.last.body["messages"]
        assert messages[1]["content"][1] == {
            "type": "tool_use", "id": "toolu_01", "name": "fs.read", "input": {"path": "a"},
        }
        assert messages[2] == {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_01", "content": "file body"}],
        }


class TestWatsonxRequestShape:
    def test_exchanges_the_api_key_for_a_bearer_token_once(self):
        replay = ReplayTransport([
            json_response("watsonx_iam_token"),
            json_response("watsonx_text"),
            json_response("watsonx_text"),
        ])
        provider = WatsonxProvider(
            model="ibm/granite-13b-chat-v2", api_key="wx-key", project_id="proj-1",
            transport=replay.as_transport(),
        )

        asyncio.run(provider.complete([{"role": "user", "content": "hi"}]))
        asyncio.run(provider.complete([{"role": "user", "content": "again"}]))

        token_calls = [c for c in replay.calls if "iam.cloud.ibm.com" in c.url]
        assert len(token_calls) == 1, "the token is valid for an hour; do not re-fetch per call"
        chat_call = replay.calls[1]
        assert "version=" in chat_call.url
        assert chat_call.body["project_id"] == "proj-1"
        assert chat_call.headers["authorization"] == "Bearer iam-bearer-token"

    def test_native_tools_are_not_claimed(self):
        """Unverifiable against a live project, so REACT_TEXT is the honest route."""
        provider = WatsonxProvider(model="m", api_key="k")
        assert provider.supports_native_tools is False


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestResponseParsing:
    def test_plain_text_and_usage(self):
        replay = ReplayTransport([json_response("openai_text")])
        result = asyncio.run(_openai(replay).complete([{"role": "user", "content": "hi"}]))

        assert result.text == "The tests live in tests/."
        assert result.usage.prompt_tokens == 120 and result.usage.completion_tokens == 9
        assert result.finish_reason == "stop"

    def test_tool_call_arguments_are_parsed_from_json(self):
        replay = ReplayTransport([json_response("openai_tool_call")])
        result = asyncio.run(_openai(replay).complete([{"role": "user", "content": "hi"}]))

        assert len(result.tool_calls) == 1
        call = result.tool_calls[0]
        assert (call.id, call.name, call.arguments) == ("call_abc", "fs.read", {"path": "README.md"})

    def test_parallel_tool_calls_all_survive(self):
        replay = ReplayTransport([json_response("openai_parallel_tool_calls")])
        result = asyncio.run(_openai(replay).complete([{"role": "user", "content": "hi"}]))

        assert [c.arguments["path"] for c in result.tool_calls] == ["a.py", "b.py"]

    def test_list_content_is_joined(self):
        """Some gateways return parts even for a plain text answer."""
        replay = ReplayTransport([json_response("openai_list_content")])
        result = asyncio.run(_openai(replay).complete([{"role": "user", "content": "hi"}]))

        assert result.text == "part one part two"

    def test_reasoning_sibling_field_is_read_when_content_is_empty(self):
        """Reading only `content` reports 'empty response' for a working provider."""
        replay = ReplayTransport([json_response("ollama_reasoning_only")])
        result = asyncio.run(_openai(replay, model="deepseek-r1:7b").complete(
            [{"role": "user", "content": "hi"}],
        ))

        assert "look at the Makefile" in result.text

    def test_think_tags_are_stripped_from_a_visible_answer(self):
        replay = ReplayTransport([json_response("ollama_think_tags")])
        result = asyncio.run(_openai(replay, model="deepseek-r1:7b").complete(
            [{"role": "user", "content": "hi"}],
        ))

        assert result.text == "Run make test."
        assert "<think>" not in result.text

    def test_legacy_completion_shape(self):
        replay = ReplayTransport([json_response("legacy_completion_shape")])
        result = asyncio.run(_openai(replay).complete([{"role": "user", "content": "hi"}]))

        assert result.text == "legacy answer"

    def test_unparseable_tool_arguments_become_empty_not_an_exception(self):
        """The registry then names the missing argument — a usable correction."""
        replay = ReplayTransport([httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant",
            "tool_calls": [{"id": "c1", "function": {"name": "fs.read", "arguments": "{not json"}}],
        }}]})])
        result = asyncio.run(_openai(replay).complete([{"role": "user", "content": "hi"}]))

        assert result.tool_calls[0].arguments == {}

    def test_anthropic_mixes_text_and_tool_use_in_one_turn(self):
        replay = ReplayTransport([json_response("anthropic_tool_use")])
        provider = AnthropicProvider(model="c", api_key="k", transport=replay.as_transport())

        result = asyncio.run(provider.complete([{"role": "user", "content": "hi"}]))

        assert result.text == "Let me read that file."
        assert result.tool_calls[0].arguments == {"path": "Makefile"}
        assert result.extra["cache_read_input_tokens"] is None

    def test_anthropic_usage_maps_input_output_tokens(self):
        replay = ReplayTransport([json_response("anthropic_text")])
        provider = AnthropicProvider(model="c", api_key="k", transport=replay.as_transport())

        result = asyncio.run(provider.complete([{"role": "user", "content": "hi"}]))

        assert result.usage.prompt_tokens == 210
        assert result.extra["cache_read_input_tokens"] == 180


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


class TestErrorTaxonomy:
    @pytest.mark.parametrize("status,expected", [
        (404, ModelNotFound),
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (429, ProviderQuotaError),
        (500, ProviderNotReachable),
    ])
    def test_status_maps_to_a_meaningful_error(self, status, expected):
        replay = ReplayTransport([error_response(status)])
        with pytest.raises(expected):
            asyncio.run(_openai(replay).complete([{"role": "user", "content": "hi"}]))

    def test_quota_is_detected_from_the_body_not_only_the_status(self):
        replay = ReplayTransport([
            error_response(400, {"error": {"message": "You exceeded your current quota"}}),
        ])
        with pytest.raises(ProviderQuotaError):
            asyncio.run(_openai(replay).complete([{"role": "user", "content": "hi"}]))

    def test_ollama_reports_a_missing_model_as_400(self):
        """Ollama answers 400 with 'try pulling it first' rather than 404."""
        replay = ReplayTransport([
            error_response(400, {"error": "model 'llama9' not found, try pulling it first"}),
        ])
        with pytest.raises(ModelNotFound):
            asyncio.run(_openai(replay, model="llama9").complete([{"role": "user", "content": "hi"}]))

    def test_malformed_json_is_its_own_error(self):
        replay = ReplayTransport([httpx.Response(200, text="<html>gateway</html>")])
        with pytest.raises(MalformedResponse):
            asyncio.run(_openai(replay).complete([{"role": "user", "content": "hi"}]))

    def test_no_choices_is_malformed(self):
        replay = ReplayTransport([json_response("malformed_no_choices")])
        with pytest.raises(MalformedResponse, match="no choices"):
            asyncio.run(_openai(replay).complete([{"role": "user", "content": "hi"}]))

    def test_timeout_message_says_what_to_do(self):
        def handler(request):
            raise httpx.TimeoutException("timed out", request=request)

        provider = OpenAICompatibleProvider(
            base_url="https://api.example/v1", model="m",
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ProviderNotReachable, match="cold cache"):
            asyncio.run(provider.complete([{"role": "user", "content": "hi"}]))

    def test_connection_failure_names_the_endpoint(self):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        provider = OpenAICompatibleProvider(
            base_url="http://localhost:11434/v1", model="m", name="ollama",
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ProviderNotReachable, match="localhost:11434"):
            asyncio.run(provider.complete([{"role": "user", "content": "hi"}]))


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


class TestStreaming:
    def test_text_deltas_arrive_in_order_and_terminate(self):
        replay = ReplayTransport([sse_response(sse_lines_from_fixture("openai_stream_text"))])
        chunks = _collect(_openai(replay))

        assert "".join(c.text for c in chunks) == "Looking at the tests."
        assert chunks[-1].done is True

    def test_tool_call_arguments_assemble_across_chunk_boundaries(self):
        """Providers split argument JSON at arbitrary bytes, including mid-string."""
        replay = ReplayTransport([sse_response(sse_lines_from_fixture("openai_stream_tool_call"))])
        accumulator = ToolCallAccumulator()

        for chunk in _collect(_openai(replay)):
            accumulator.add(chunk)

        calls = accumulator.finish()
        assert len(calls) == 1
        assert calls[0].id == "call_xyz"
        assert calls[0].name == "fs.grep"
        assert calls[0].arguments == {"pattern": "def greet"}

    def test_anthropic_stream_pairs_block_start_with_json_deltas(self):
        replay = ReplayTransport([sse_response(sse_lines_from_fixture("anthropic_stream_tool_use"))])
        provider = AnthropicProvider(model="c", api_key="k", transport=replay.as_transport())

        async def run():
            return [c async for c in provider.stream([{"role": "user", "content": "hi"}])]

        chunks = asyncio.run(run())
        accumulator = ToolCallAccumulator()
        for chunk in chunks:
            accumulator.add(chunk)

        assert "".join(c.text for c in chunks) == "Reading it."
        assert accumulator.finish()[0].arguments == {"path": "Makefile"}
        assert chunks[-1].done is True

    def test_stream_error_status_maps_to_the_taxonomy(self):
        replay = ReplayTransport([error_response(429)])
        with pytest.raises(ProviderQuotaError):
            _collect(_openai(replay))

    def test_unparseable_stream_line_is_skipped_not_fatal(self):
        replay = ReplayTransport([sse_response(["data: {not json", "data: [DONE]"])])
        chunks = _collect(_openai(replay))

        assert chunks[-1].done is True

    def test_watsonx_streams_one_chunk_rather_than_guessing_event_names(self):
        replay = ReplayTransport([json_response("watsonx_iam_token"), json_response("watsonx_text")])
        provider = WatsonxProvider(model="m", api_key="k", transport=replay.as_transport())

        async def run():
            return [c async for c in provider.stream([{"role": "user", "content": "hi"}])]

        chunks = asyncio.run(run())
        assert chunks[0].text == "watsonx answer"
        assert chunks[-1].done is True


# ---------------------------------------------------------------------------
# Hygiene
# ---------------------------------------------------------------------------


class TestNoContamination:
    def test_no_environment_mutation(self, monkeypatch):
        """llm_provider writes OPENAI_API_KEY process-wide; direct_chat then reads
        it and concludes things about a provider the user never selected."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_BASE", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        before = dict(os.environ)

        replay = ReplayTransport([json_response("openai_text")])
        asyncio.run(_openai(replay).complete([{"role": "user", "content": "hi"}]))

        anthropic_replay = ReplayTransport([json_response("anthropic_text")])
        asyncio.run(AnthropicProvider(
            model="c", api_key="sk-ant", transport=anthropic_replay.as_transport(),
        ).complete([{"role": "user", "content": "hi"}]))

        assert dict(os.environ) == before

    def test_neither_crewai_nor_litellm_is_imported(self):
        """The loop calls a provider every iteration; a 10-60s import tax is not payable.

        Checked in a subprocess because this is a property of *this* import graph,
        and other tests in the suite legitimately import CrewAI — asserting on the
        shared ``sys.modules`` would pass or fail depending on test order.
        """
        import subprocess
        import textwrap

        script = textwrap.dedent(
            """
            import sys

            from gitpilot.agent.adapter import build_adapter
            from gitpilot.agent.dialects.lite import classify_intent
            from gitpilot.agent.dialects.react_text import parse_react_output
            from gitpilot.agent.model_profile import resolve_profile
            from gitpilot.agent.providers import build_provider  # noqa: F401
            from gitpilot.agent.providers import OpenAICompatibleProvider

            # Exercise the paths that reach into legacy modules for their tables:
            # model_profile -> agentic, lite -> agentic + plan_guards.
            resolve_profile("ollama", "qwen2.5:1.5b", allow_probe=False)
            classify_intent("delete the demo files")
            parse_react_output("Action: fs.read", known_tools=["fs.read"])
            OpenAICompatibleProvider(base_url="http://x/v1", model="m").build_body(
                [{"role": "user", "content": "hi"}],
            )

            leaked = sorted(
                name for name in sys.modules
                if name == "crewai" or name.startswith("crewai.")
                or name == "litellm" or name.startswith("litellm.")
            )
            print(",".join(leaked))
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, check=False,
        )

        assert result.returncode == 0, result.stderr
        leaked = result.stdout.strip()
        assert leaked == "", f"the provider/dialect path imported: {leaked}"


class TestFactory:
    def test_builds_an_openai_compatible_client_for_ollama(self, monkeypatch):
        from gitpilot.settings import LLMProvider, get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "provider", LLMProvider.ollama, raising=False)
        monkeypatch.setattr(settings.ollama, "model", "llama3:8b", raising=False)

        provider = build_provider(settings)

        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.model == "llama3:8b"
        assert provider.name == "ollama"

    def test_model_override_reaches_the_client(self, monkeypatch):
        """This is how the model router stops being advisory (Batch V4-B2)."""
        from gitpilot.settings import LLMProvider, get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "provider", LLMProvider.ollama, raising=False)

        assert build_provider(settings, model="qwen2.5:1.5b").model == "qwen2.5:1.5b"

    def test_builds_anthropic_for_claude(self, monkeypatch):
        from gitpilot.settings import LLMProvider, get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "provider", LLMProvider.claude, raising=False)
        monkeypatch.setattr(settings.claude, "model", "claude-sonnet-4-5", raising=False)

        provider = build_provider(settings)

        assert isinstance(provider, AnthropicProvider)
        assert provider.supports_native_tools is True

    def test_builds_watsonx(self, monkeypatch):
        from gitpilot.settings import LLMProvider, get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "provider", LLMProvider.watsonx, raising=False)

        assert isinstance(build_provider(settings), WatsonxProvider)
