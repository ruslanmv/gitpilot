"""Tests for the context-window usage meter.

The meter answers a single user question: "is my current LLM about to
run out of context?".  These tests pin the contract the chat UI relies
on:

* arithmetic is consistent: ``used + reserved + free == context_window``
* topology string accurately reflects mode + tool count
* the ``is_estimate`` flag is true for providers without a real
  tokenizer (Ollama / OllaBridge) and false for OpenAI / Anthropic
  *when* ``tiktoken`` is installed
* the FastAPI endpoint returns the documented shape and can be
  disabled by the feature flag

The module is pure; tests run without network or LLM calls.
"""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from gitpilot import context_meter, flags
from gitpilot.context_meter import (
    FLAG_CONTEXT_METER,
    RESERVED_RESPONSE_TOKENS,
    ContextUsage,
    build_usage,
    count_messages_tokens,
    count_system_prompt_tokens,
    count_tool_schema_tokens,
    describe_topology,
    resolve_context_window,
    resolve_provider_model,
    system_prompt_text,
)
from gitpilot.settings import (
    AppSettings,
    ClaudeConfig,
    LLMProvider,
    OllaBridgeConfig,
    OllamaConfig,
    OpenAIConfig,
    WatsonxConfig,
)


# ----------------------------------------------------------------------
# Topology string
# ----------------------------------------------------------------------

def test_topology_single_agent_counts_tools() -> None:
    assert describe_topology(lite_mode=False, tool_count=12) == (
        "single-agent · CrewAI ReAct · 12 tools"
    )


def test_topology_includes_mcp_extras_when_supplied() -> None:
    assert describe_topology(lite_mode=False, tool_count=12, extra_tools=3) == (
        "single-agent · CrewAI ReAct · 15 tools"
    )


def test_topology_lite_mode_ignores_tool_count() -> None:
    """Lite mode never invokes tools — the string must reflect that."""
    assert describe_topology(lite_mode=True, tool_count=99, extra_tools=5) == (
        "lite · prompt-only · 0 tools · no repo I/O"
    )


# ----------------------------------------------------------------------
# Provider / model / window resolution
# ----------------------------------------------------------------------

def test_resolve_openai_known_model() -> None:
    s = AppSettings(provider=LLMProvider.openai, openai=OpenAIConfig(model="gpt-4o-mini"))
    assert resolve_provider_model(s) == ("OpenAI", "gpt-4o-mini")
    assert resolve_context_window(s) == 128_000


def test_resolve_anthropic_opus_47() -> None:
    s = AppSettings(provider=LLMProvider.claude, claude=ClaudeConfig(model="claude-opus-4-7"))
    assert resolve_provider_model(s) == ("Anthropic", "claude-opus-4-7")
    assert resolve_context_window(s) == 200_000


def test_resolve_ollama_known_family() -> None:
    s = AppSettings(provider=LLMProvider.ollama, ollama=OllamaConfig(model="llama3:8b"))
    assert resolve_provider_model(s) == ("Ollama", "llama3:8b")
    # llama3 = 8 k context — the exact knob the user hit in production.
    assert resolve_context_window(s) == 8_192


def test_resolve_ollama_large_context_family() -> None:
    s = AppSettings(provider=LLMProvider.ollama, ollama=OllamaConfig(model="llama3.1:70b"))
    assert resolve_context_window(s) == 131_072


def test_resolve_ollama_unknown_falls_back_to_default() -> None:
    s = AppSettings(provider=LLMProvider.ollama, ollama=OllamaConfig(model="weirdmodel:xyz"))
    # Unknown families round DOWN — better to over-warn than to claim more
    # context than the model actually has.
    assert resolve_context_window(s) == 8_192


def test_resolve_ollabridge_uses_ollama_table() -> None:
    s = AppSettings(
        provider=LLMProvider.ollabridge,
        ollabridge=OllaBridgeConfig(model="qwen2.5:1.5b"),
    )
    assert resolve_provider_model(s) == ("OllaBridge", "qwen2.5:1.5b")
    assert resolve_context_window(s) == 32_768


def test_resolve_watsonx_known_model() -> None:
    s = AppSettings(
        provider=LLMProvider.watsonx,
        watsonx=WatsonxConfig(model_id="meta-llama/llama-3-3-70b-instruct"),
    )
    assert resolve_provider_model(s) == ("watsonx", "meta-llama/llama-3-3-70b-instruct")
    assert resolve_context_window(s) == 131_072


# ----------------------------------------------------------------------
# Tokenizer-availability flag
# ----------------------------------------------------------------------

def test_is_estimate_true_for_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    s = AppSettings(provider=LLMProvider.ollama, ollama=OllamaConfig(model="llama3"))
    usage = build_usage(s, breakdown={}, tool_count=0, lite_mode=False)
    # Ollama has no published tokenizer → we always heuristic-estimate.
    assert usage.is_estimate is True


def test_is_estimate_reflects_tiktoken_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``tiktoken`` is loaded, OpenAI/Anthropic counts are honest;
    when it isn't, we admit the estimate."""
    s = AppSettings(provider=LLMProvider.openai)

    monkeypatch.setattr(context_meter, "_TIKTOKEN", object())
    assert build_usage(s, breakdown={}, tool_count=0, lite_mode=False).is_estimate is False

    monkeypatch.setattr(context_meter, "_TIKTOKEN", None)
    assert build_usage(s, breakdown={}, tool_count=0, lite_mode=False).is_estimate is True


# ----------------------------------------------------------------------
# ContextUsage arithmetic
# ----------------------------------------------------------------------

def test_used_plus_reserved_plus_free_equals_window() -> None:
    """The invariant the UI bar relies on — no rounding holes."""
    s = AppSettings(provider=LLMProvider.openai, openai=OpenAIConfig(model="gpt-4o-mini"))
    usage = build_usage(
        s,
        breakdown={"messages": 1_000, "system_prompt": 500, "tool_schemas": 200},
        tool_count=10,
        lite_mode=False,
    )
    assert usage.used == 1_700
    assert usage.reserved_response == RESERVED_RESPONSE_TOKENS
    assert usage.used + usage.reserved_response + usage.free == usage.context_window


def test_percent_used_clamped_to_one_decimal() -> None:
    s = AppSettings(provider=LLMProvider.openai, openai=OpenAIConfig(model="gpt-4o-mini"))
    usage = build_usage(
        s,
        breakdown={"messages": 12_345},
        tool_count=0,
        lite_mode=False,
    )
    # 12345 / 128000 = 9.6445…% → rounded to 9.6
    assert usage.percent_used == pytest.approx(9.6, abs=0.05)


def test_oversubscribed_breakdown_yields_zero_free() -> None:
    """If a caller passes more tokens than the window holds, free should
    bottom-out at zero rather than going negative."""
    s = AppSettings(provider=LLMProvider.ollama, ollama=OllamaConfig(model="llama3"))
    usage = build_usage(
        s,
        breakdown={"messages": 999_999},
        tool_count=0,
        lite_mode=False,
    )
    assert usage.free == 0


def test_to_dict_shape_is_stable() -> None:
    """The frontend reads these keys by name — pin every one."""
    s = AppSettings(provider=LLMProvider.openai, openai=OpenAIConfig(model="gpt-4o-mini"))
    d = build_usage(
        s,
        breakdown={"messages": 100, "system_prompt": 50},
        tool_count=7,
        lite_mode=False,
    ).to_dict()
    assert set(d.keys()) == {
        "provider",
        "model",
        "context_window",
        "used",
        "reserved_response",
        "free",
        "percent_used",
        "topology",
        "tool_count",
        "breakdown",
        "is_estimate",
    }


# ----------------------------------------------------------------------
# API endpoint
# ----------------------------------------------------------------------

@pytest.fixture()
def client() -> Iterator[TestClient]:
    from gitpilot import api as api_module

    yield TestClient(api_module.app)


def test_endpoint_returns_documented_shape(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gitpilot import api as api_module

    monkeypatch.setattr(api_module, "_is_lite_mode_active", lambda: False)
    flags.set_override(FLAG_CONTEXT_METER, True)
    try:
        r = client.get("/api/context/usage")
    finally:
        flags.clear_override(FLAG_CONTEXT_METER)

    assert r.status_code == 200, r.text
    body = r.json()
    for key in (
        "provider",
        "model",
        "context_window",
        "used",
        "reserved_response",
        "free",
        "percent_used",
        "topology",
        "tool_count",
        "breakdown",
        "is_estimate",
    ):
        assert key in body, f"missing key: {key}"
    assert isinstance(body["breakdown"], dict)
    assert (
        body["used"] + body["reserved_response"] + body["free"]
        == body["context_window"]
    )


# ----------------------------------------------------------------------
# Real-source counters
# ----------------------------------------------------------------------

def test_system_prompt_lite_is_shorter_than_full() -> None:
    """Lite mode injects a single short persona; full mode stacks
    explorer + planner.  The counter must reflect the difference."""
    full = count_system_prompt_tokens(lite_mode=False)
    lite = count_system_prompt_tokens(lite_mode=True)
    assert full > 100
    assert 0 < lite < full


def test_system_prompt_text_changes_with_mode() -> None:
    assert "Repository Refactor Planner" in system_prompt_text(lite_mode=False)
    assert "GitPilot Lite" in system_prompt_text(lite_mode=True)
    assert "Repository Refactor Planner" not in system_prompt_text(lite_mode=True)


def test_count_messages_tokens_handles_dataclass_and_dict() -> None:
    """The session manager returns dataclass Message objects; legacy
    snapshots may carry dicts.  Both must work."""
    from gitpilot.session import Message

    msgs = [
        Message(role="user", content="hello world"),
        {"role": "assistant", "content": "hi there, here is a longer reply"},
        None,  # tolerated, ignored
        Message(role="system", content=""),  # empty ignored
    ]
    total = count_messages_tokens(msgs)
    # Both real messages contribute >0 tokens; None / empty are skipped.
    assert total > 0


def test_count_messages_tokens_empty_iterable_is_zero() -> None:
    assert count_messages_tokens([]) == 0


def test_count_tool_schema_tokens_sums_real_tool_lists() -> None:
    """Exercise the real REPOSITORY_TOOLS list — confirms we walk
    CrewAI tool objects and pull name + description + schema."""
    from gitpilot.agent_tools import REPOSITORY_TOOLS, WRITE_TOOLS

    total = count_tool_schema_tokens([REPOSITORY_TOOLS, WRITE_TOOLS])
    # The four READ + three WRITE tools easily clear 200 tokens of
    # descriptive text; this pins "non-zero on the real tool list".
    assert total > 200


def test_count_tool_schema_tokens_empty_groups_yield_zero() -> None:
    assert count_tool_schema_tokens([]) == 0
    assert count_tool_schema_tokens([[]]) == 0


# ----------------------------------------------------------------------
# Endpoint with real numbers
# ----------------------------------------------------------------------

def test_endpoint_populates_real_breakdown_in_full_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside lite mode the breakdown's system_prompt and tool_schemas
    rows must be non-zero — that's the proof the popover stopped
    showing the placeholder zeros."""
    from gitpilot import api as api_module

    monkeypatch.setattr(api_module, "_is_lite_mode_active", lambda: False)
    flags.set_override(FLAG_CONTEXT_METER, True)
    try:
        r = client.get("/api/context/usage")
    finally:
        flags.clear_override(FLAG_CONTEXT_METER)

    body = r.json()
    assert body["breakdown"]["system_prompt"] > 0
    assert body["breakdown"]["tool_schemas"] > 0
    assert body["tool_count"] > 0
    assert body["used"] > 0


def test_endpoint_session_id_loads_messages(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: object,
) -> None:
    """When session_id is supplied, the messages row reflects that
    session's content.  No session_id → row stays 0."""
    from gitpilot import api as api_module

    monkeypatch.setattr(api_module, "_is_lite_mode_active", lambda: False)

    session = api_module._session_mgr.create(
        repo_full_name="owner/repo", branch="main", name="test-meter"
    )
    session.add_message("user", "the quick brown fox jumps over the lazy dog")
    session.add_message("assistant", "an answer with similar length text content")
    api_module._session_mgr.save(session)

    flags.set_override(FLAG_CONTEXT_METER, True)
    try:
        with_session = client.get(
            f"/api/context/usage?session_id={session.id}"
        ).json()
        without_session = client.get("/api/context/usage").json()
    finally:
        flags.clear_override(FLAG_CONTEXT_METER)

    assert with_session["breakdown"]["messages"] > 0
    assert without_session["breakdown"]["messages"] == 0
    # The with-session call must claim strictly more total `used` than
    # the no-session call — that's what the popover surfaces to users.
    assert with_session["used"] > without_session["used"]


def test_endpoint_unknown_session_id_does_not_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad session ids degrade gracefully — the popover stays useful
    rather than 500ing the whole UI."""
    from gitpilot import api as api_module

    monkeypatch.setattr(api_module, "_is_lite_mode_active", lambda: False)
    flags.set_override(FLAG_CONTEXT_METER, True)
    try:
        r = client.get("/api/context/usage?session_id=does-not-exist")
    finally:
        flags.clear_override(FLAG_CONTEXT_METER)

    assert r.status_code == 200
    assert r.json()["breakdown"]["messages"] == 0


def test_endpoint_returns_404_when_flag_off(client: TestClient) -> None:
    """Disabling the flag must kill the endpoint without touching code —
    this is the kill-switch the rollout plan relies on."""
    flags.set_override(FLAG_CONTEXT_METER, False)
    try:
        r = client.get("/api/context/usage")
    finally:
        flags.clear_override(FLAG_CONTEXT_METER)
    assert r.status_code == 404
