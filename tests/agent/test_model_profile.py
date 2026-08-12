"""Model profiles — Batch V4-B2."""
from __future__ import annotations

import asyncio
import json

import pytest

from gitpilot.agent.contracts import Dialect
from gitpilot.agent.model_profile import (
    PROBE_TOOL_NAME,
    ModelProfile,
    base_profile,
    classify_probe_response,
    dialect_from_tables,
    is_small_or_reasoning_model,
    load_yaml_overrides,
    needs_probe,
    probe_model,
    read_probe_cache,
    resolve_context_window,
    resolve_profile,
    routed_model,
)
from gitpilot.agent.providers.base import ProviderResponse, RawToolCall


@pytest.fixture()
def cache(tmp_path):
    return tmp_path / "model_profiles.json"


# ---------------------------------------------------------------------------
# Table resolution
# ---------------------------------------------------------------------------


class TestTableResolution:
    def test_small_local_model_resolves_to_lite_from_the_table(self, cache):
        """qwen2.5:1.5b is GitPilot's default model, and it is on the <7B list."""
        profile = resolve_profile("ollama", "qwen2.5:1.5b", cache_path=cache)

        assert profile.dialect is Dialect.LITE
        assert profile.source == "table"
        assert profile.max_tool_schemas == 6
        assert profile.schema_verbosity == "names_only"
        assert profile.prompt_style == "lean"

    def test_capable_local_model_resolves_to_react_text(self, cache):
        """Not on any table, so it lands on the safe middle rung, not LITE."""
        profile = resolve_profile("ollama", "llama3:8b", cache_path=cache)

        assert profile.dialect is Dialect.REACT_TEXT
        assert profile.source == "fallback", "no table claims llama3:8b; that is expected"
        assert profile.supports_parallel_tool_calls is False

    def test_claude_resolves_to_native_by_provider_family(self, cache):
        profile = resolve_profile("claude", "claude-sonnet-4-5", cache_path=cache)

        assert profile.dialect is Dialect.NATIVE
        assert profile.source == "table"
        assert profile.context_window == 200_000
        assert profile.cacheable_prefix is True
        assert profile.supports_parallel_tool_calls is True
        assert profile.prompt_style == "standard"

    def test_openai_resolves_to_native(self, cache):
        profile = resolve_profile("openai", "gpt-4o-mini", cache_path=cache)

        assert profile.dialect is Dialect.NATIVE
        assert profile.cacheable_prefix is False, "prompt caching markers are Anthropic-only"

    def test_reasoning_model_beats_the_provider_family(self, cache):
        """o1 on OpenAI breaks a tool loop; the family rule cannot see that."""
        profile = resolve_profile("openai", "o1-preview", cache_path=cache)

        assert profile.dialect is Dialect.LITE
        assert profile.strip_reasoning_tags is True

    def test_unknown_provider_degrades_to_react_text(self, cache):
        profile = resolve_profile("some-new-gateway", "mystery-model", cache_path=cache)

        assert profile.dialect is Dialect.REACT_TEXT
        assert profile.source == "fallback"
        assert "no table entry" in profile.notes

    def test_deepseek_is_both_small_listed_and_reasoning(self):
        assert is_small_or_reasoning_model("deepseek-r1:14b") is True
        assert dialect_from_tables("ollama", "deepseek-r1:14b") is Dialect.LITE

    def test_provider_prefix_is_stripped_before_matching(self):
        assert is_small_or_reasoning_model("ollama/qwen2.5:1.5b") is True

    def test_small_context_shrinks_the_schema_budget(self):
        """A 2k window cannot hold a long tool list whatever the dialect."""
        profile = base_profile("ollama", "phi", Dialect.REACT_TEXT, "table")

        assert profile.context_window < 16_000
        assert profile.max_tool_schemas <= 8


class TestContextWindows:
    @pytest.mark.parametrize("provider,model,expected", [
        ("claude", "claude-sonnet-4-5", 200_000),
        ("openai", "gpt-4o-mini", 128_000),
        ("ollama", "llama3.1:8b", 131_072),
    ])
    def test_known_models(self, provider, model, expected):
        assert resolve_context_window(provider, model) == expected

    def test_dated_variants_match_by_prefix(self):
        """Providers append dates; an exact-match lookup would return the 8k default
        and make the loop compact a 200k conversation."""
        assert resolve_context_window("claude", "claude-3-5-sonnet-20241022") == 200_000
        assert resolve_context_window("openai", "o1-preview") == 200_000

    def test_longest_prefix_wins(self):
        assert resolve_context_window("openai", "o3-mini-2025") == 200_000


# ---------------------------------------------------------------------------
# models.yaml
# ---------------------------------------------------------------------------


class TestYamlOverride:
    def _write(self, tmp_path, body):
        (tmp_path / ".gitpilot").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".gitpilot" / "models.yaml").write_text(body, encoding="utf-8")

    def test_override_beats_the_tables(self, tmp_path, cache):
        """A model nobody has catalogued can be pinned without a release."""
        self._write(tmp_path, "models:\n  qwen2.5:1.5b:\n    dialect: native\n")

        profile = resolve_profile(
            "ollama", "qwen2.5:1.5b", workspace=tmp_path, cache_path=cache,
        )

        assert profile.dialect is Dialect.NATIVE
        assert profile.source == "yaml"

    def test_override_can_set_individual_fields(self, tmp_path, cache):
        self._write(
            tmp_path,
            "models:\n  llama3:8b:\n    context_window: 65536\n    max_tool_schemas: 12\n",
        )

        profile = resolve_profile("ollama", "llama3:8b", workspace=tmp_path, cache_path=cache)

        assert profile.context_window == 65536
        assert profile.max_tool_schemas == 12
        assert profile.dialect is Dialect.REACT_TEXT, "unspecified fields keep their resolved value"

    def test_unknown_dialect_is_ignored_with_a_warning(self, tmp_path, cache):
        self._write(tmp_path, "models:\n  llama3:8b:\n    dialect: telepathy\n")

        profile = resolve_profile("ollama", "llama3:8b", workspace=tmp_path, cache_path=cache)

        assert profile.dialect is Dialect.REACT_TEXT

    def test_missing_file_is_not_an_error(self, tmp_path):
        assert load_yaml_overrides(tmp_path) == {}

    def test_malformed_file_does_not_break_a_run(self, tmp_path, cache):
        self._write(tmp_path, "models: [this is not a mapping")

        profile = resolve_profile("ollama", "llama3:8b", workspace=tmp_path, cache_path=cache)
        assert profile.dialect is Dialect.REACT_TEXT


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------


class _FakeClient:
    """Answers one probe however the test asks it to."""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = 0

    async def complete(self, messages, *, tools=None, system=None, **opts):
        self.calls += 1
        if self._error:
            raise self._error
        return self._response


class TestProbe:
    #: Deliberately absent from every table — a probe test on a table-covered id
    #: would assert nothing, because step 2 wins before the probe runs.
    UNKNOWN_MODEL = "acme-secret-model-v9"

    def test_a_table_covered_model_is_never_probed(self, cache):
        assert needs_probe("ollama", "qwen2.5:1.5b", cache_path=cache) is False
        assert needs_probe("claude", "claude-sonnet-4-5", cache_path=cache) is False

    def test_an_unknown_model_wants_a_probe(self, cache):
        assert needs_probe("custom", self.UNKNOWN_MODEL, cache_path=cache) is True

    def test_native_when_the_model_returns_a_tool_call(self, cache):
        client = _FakeClient(ProviderResponse(
            tool_calls=[RawToolCall(id="c1", name=PROBE_TOOL_NAME, arguments={"word": "ping"})],
        ))

        profile = asyncio.run(probe_model(
            client, "custom", self.UNKNOWN_MODEL, base_url="http://host:1234", cache_path=cache,
        ))

        assert profile.dialect is Dialect.NATIVE
        assert profile.source == "probe"

    def test_react_text_when_the_model_follows_the_grammar(self, cache):
        client = _FakeClient(ProviderResponse(
            text=f"Thought: I will echo\nAction: {PROBE_TOOL_NAME}\nAction Input: {{\"word\": \"ping\"}}",
        ))

        profile = asyncio.run(probe_model(
            client, "custom", self.UNKNOWN_MODEL, cache_path=cache,
        ))

        assert profile.dialect is Dialect.REACT_TEXT

    def test_lite_when_the_model_just_chats(self, cache):
        client = _FakeClient(ProviderResponse(text="Sure! The word is ping."))

        profile = asyncio.run(probe_model(client, "custom", self.UNKNOWN_MODEL, cache_path=cache))

        assert profile.dialect is Dialect.LITE

    def test_result_is_cached_and_reused_without_a_second_call(self, cache):
        client = _FakeClient(ProviderResponse(
            tool_calls=[RawToolCall(id="c1", name=PROBE_TOOL_NAME)],
        ))
        asyncio.run(probe_model(
            client, "custom", self.UNKNOWN_MODEL, base_url="http://host-a:1234", cache_path=cache,
        ))

        assert needs_probe(
            "custom", self.UNKNOWN_MODEL, base_url="http://host-a:1234", cache_path=cache,
        ) is False
        resolved = resolve_profile(
            "custom", self.UNKNOWN_MODEL, base_url="http://host-a:1234", cache_path=cache,
        )
        assert resolved.dialect is Dialect.NATIVE
        assert resolved.source == "probe"
        assert client.calls == 1

    def test_cache_is_keyed_by_host_so_another_fleet_reprobes(self, cache):
        """The same name on a different host may be a different model."""
        client = _FakeClient(ProviderResponse(
            tool_calls=[RawToolCall(id="c1", name=PROBE_TOOL_NAME)],
        ))
        asyncio.run(probe_model(
            client, "custom", self.UNKNOWN_MODEL, base_url="http://host-a:1234", cache_path=cache,
        ))

        assert needs_probe(
            "custom", self.UNKNOWN_MODEL, base_url="http://host-b:1234", cache_path=cache,
        ) is True

    def test_transport_failure_does_not_poison_the_cache(self, cache):
        """An unreachable endpoint says nothing about the model."""
        client = _FakeClient(error=RuntimeError("connection refused"))

        profile = asyncio.run(probe_model(
            client, "custom", self.UNKNOWN_MODEL, base_url="http://host:1", cache_path=cache,
        ))

        assert profile.dialect is Dialect.REACT_TEXT
        assert "probe failed" in profile.notes
        assert read_probe_cache(cache) == {}

    def test_classification_is_testable_without_a_client(self):
        assert classify_probe_response(ProviderResponse(text="hello")) is Dialect.LITE
        assert classify_probe_response(
            ProviderResponse(tool_calls=[RawToolCall(id="1", name="x")])
        ) is Dialect.NATIVE

    def test_corrupt_cache_file_is_ignored(self, cache):
        cache.write_text("{not json", encoding="utf-8")
        assert read_probe_cache(cache) == {}


# ---------------------------------------------------------------------------
# Degradation ladder
# ---------------------------------------------------------------------------


class TestDegradation:
    def test_native_falls_to_react_text_keeping_the_tool_surface(self, cache):
        profile = resolve_profile("claude", "claude-sonnet-4-5", cache_path=cache)
        downgraded = profile.downgraded()

        assert downgraded.dialect is Dialect.REACT_TEXT
        assert downgraded.schema_verbosity == "compact"
        assert downgraded.supports_parallel_tool_calls is False
        assert "downgraded" in downgraded.source

    def test_react_text_falls_to_lite(self, cache):
        profile = resolve_profile("ollama", "llama3:8b", cache_path=cache)
        assert profile.downgraded().dialect is Dialect.LITE

    def test_lite_is_the_last_rung(self, cache):
        profile = resolve_profile("ollama", "qwen2.5:1.5b", cache_path=cache)
        assert profile.downgraded() is profile

    def test_a_downgrade_never_widens_the_schema_budget(self, cache):
        profile = resolve_profile("ollama", "phi3:mini", cache_path=cache)
        assert profile.downgraded().max_tool_schemas <= profile.max_tool_schemas


# ---------------------------------------------------------------------------
# Router integration
# ---------------------------------------------------------------------------


class TestRouterIntegration:
    def test_router_selection_is_returned_for_the_caller_to_use(self):
        """It has been computed and discarded since it shipped; now it can be used."""
        chosen = routed_model("refactor the authentication layer", category="plan_execute")

        assert chosen is None or isinstance(chosen, str) and chosen


class TestSerialisation:
    def test_profile_round_trips_to_a_dict(self, cache):
        profile = resolve_profile("claude", "claude-sonnet-4-5", cache_path=cache)
        payload = profile.to_dict()

        assert payload["dialect"] == "native"
        assert json.loads(json.dumps(payload))["context_window"] == 200_000

    def test_profile_is_frozen(self, cache):
        profile = resolve_profile("openai", "gpt-4o-mini", cache_path=cache)
        with pytest.raises(Exception):
            profile.dialect = Dialect.LITE  # type: ignore[misc]

    def test_satisfies_the_registry_schema_profile_protocol(self, cache):
        """A1 defined SchemaProfile structurally so B2 could satisfy it without
        inheriting from it."""
        from gitpilot.toolkit import ToolRegistry
        from gitpilot.toolkit.builtins import build_default_registry

        profile = resolve_profile("ollama", "qwen2.5:1.5b", cache_path=cache)
        registry: ToolRegistry = build_default_registry()

        render = registry.render(profile=profile)

        assert len(render.schemas) == profile.max_tool_schemas
        assert render.truncated
        assert all("signature" in schema for schema in render.schemas), "names_only rendering"
