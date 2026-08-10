"""The reasoning wrapper has to be something CrewAI will accept.

The report these come from: every query from the web app failing with

    2 validation errors for Agent
    llm.str      Input should be a valid string
    llm.BaseLLM  Input should be a valid dictionary or instance of BaseLLM

``Agent.llm`` is a validated pydantic field typed ``str | BaseLLM``. The
normalizer wrapped reasoning models in a plain class — a deliberate choice,
on the reasoning that CrewAI's LLM class changes between versions and
composition is safer than subclassing. It was safer right up until the
field started validating, at which point a wrapper that was neither of the
accepted types failed at agent construction, for every reasoning model, on
every agent path.

This module had no tests at all, which is why that shipped. These cover
both halves: the wrapper is accepted by CrewAI, and it still does the job
it was written for.
"""

from __future__ import annotations

import pytest

from gitpilot import reasoning_normalizer as rn


class _FakeLLM:
    """Stands in for a CrewAI native completion client."""

    def __init__(self, response: str = "plain answer", model: str = "deepseek-r1:1.5b"):
        self.model = model
        self._response = response
        self.calls: list = []

    def call(self, messages=None, **kwargs):
        self.calls.append((messages, kwargs))
        return self._response

    def supports_function_calling(self) -> bool:
        return True

    def get_context_window_size(self) -> int:
        return 32768

    def something_only_the_client_has(self) -> str:
        return "delegated"


# ── The regression: CrewAI must accept it ────────────────────────────────


def test_a_wrapped_reasoning_model_is_a_crewai_base_llm() -> None:
    base_llm = pytest.importorskip("crewai.llms.base_llm")

    wrapped = rn.wrap_if_reasoning_model(_FakeLLM(), "ollama/deepseek-r1:14b")

    assert isinstance(wrapped, base_llm.BaseLLM), (
        "Agent.llm validates as `str | BaseLLM`; anything else fails at "
        "construction before a single token is generated"
    )


def test_an_agent_can_actually_be_built_with_it() -> None:
    """The end the user hits. Type-checking the wrapper is not the same as
    surviving pydantic's validator, so build the real thing."""
    crewai = pytest.importorskip("crewai")

    wrapped = rn.wrap_if_reasoning_model(_FakeLLM(), "ollama/deepseek-r1:14b")

    agent = crewai.Agent(
        role="Analyst", goal="Answer", backstory="You explain code.",
        llm=wrapped, verbose=False,
    )

    assert agent.llm is not None


def test_the_model_name_survives_the_wrapping() -> None:
    """CrewAI reads `model` for logging, token accounting and window sizing."""
    pytest.importorskip("crewai")

    wrapped = rn.wrap_if_reasoning_model(_FakeLLM(model="deepseek-r1:14b"), "deepseek-r1:14b")

    assert wrapped.model == "deepseek-r1:14b"


# ── Still does the job it was written for ────────────────────────────────


def test_think_blocks_are_stripped_from_the_reply() -> None:
    pytest.importorskip("crewai")
    inner = _FakeLLM(response="<think>weighing the options</think>The answer is 42.")

    wrapped = rn.wrap_if_reasoning_model(inner, "deepseek-r1:14b")

    assert wrapped.call([{"role": "user", "content": "hi"}]) == "The answer is 42."


def test_a_reply_that_is_only_reasoning_is_not_swallowed() -> None:
    """Handing CrewAI "" reads as a failed call. The raw text at least
    carries the model's work."""
    pytest.importorskip("crewai")
    inner = _FakeLLM(response="<think>still thinking</think>")

    wrapped = rn.wrap_if_reasoning_model(inner, "deepseek-r1:14b")

    assert "still thinking" in wrapped.call([])


def test_a_non_string_reply_passes_through_untouched() -> None:
    """Tool-call payloads are not text and must not be regex'd."""
    pytest.importorskip("crewai")
    payload = {"tool_calls": [{"name": "read_file"}]}
    inner = _FakeLLM()
    inner._response = payload  # type: ignore[assignment]

    wrapped = rn.wrap_if_reasoning_model(inner, "deepseek-r1:14b")

    assert wrapped.call([]) is payload


def test_arguments_reach_the_inner_client() -> None:
    pytest.importorskip("crewai")
    inner = _FakeLLM()

    wrapped = rn.wrap_if_reasoning_model(inner, "deepseek-r1:14b")
    wrapped.call([{"role": "user", "content": "hi"}], tools=["a_tool"])

    messages, kwargs = inner.calls[0]
    assert messages == [{"role": "user", "content": "hi"}]
    assert kwargs.get("tools") == ["a_tool"]


def test_capabilities_are_answered_by_the_real_client() -> None:
    """BaseLLM's defaults describe nothing in particular; the client knows."""
    pytest.importorskip("crewai")

    wrapped = rn.wrap_if_reasoning_model(_FakeLLM(), "deepseek-r1:14b")

    assert wrapped.supports_function_calling() is True
    assert wrapped.get_context_window_size() == 32768


def test_unknown_attributes_fall_through_to_the_client() -> None:
    pytest.importorskip("crewai")

    wrapped = rn.wrap_if_reasoning_model(_FakeLLM(), "deepseek-r1:14b")

    assert wrapped.something_only_the_client_has() == "delegated"


# ── Everything else is left alone ────────────────────────────────────────


@pytest.mark.parametrize("model", ["llama3", "qwen2.5:7b", "gpt-4o-mini", "claude-sonnet-4-5"])
def test_an_ordinary_model_is_returned_unchanged(model: str) -> None:
    """Zero overhead, and — more to the point — zero new ways to fail."""
    inner = _FakeLLM()

    assert rn.wrap_if_reasoning_model(inner, model) is inner


@pytest.mark.parametrize("model", ["deepseek-r1:14b", "ollama/deepseek-r1:1.5b", "qwq:32b"])
def test_reasoning_models_are_recognised(model: str) -> None:
    assert rn.is_reasoning_model(model)


def test_a_wrapper_that_cannot_be_built_does_not_block_the_chat(monkeypatch) -> None:
    """Unstripped <think> tags are cosmetic; having no LLM is not."""
    monkeypatch.setattr(rn, "_CREWAI_WRAPPER_CLASS", None)

    def _explode() -> None:
        raise RuntimeError("CrewAI changed shape again")

    monkeypatch.setattr(rn, "_crewai_wrapper_class", _explode)
    inner = _FakeLLM()

    with pytest.raises(RuntimeError):
        rn._crewai_wrapper_class()

    # And through the public entry point, the failure is absorbed.
    monkeypatch.setattr(rn, "_crewai_wrapper_class", lambda: None)
    result = rn.wrap_if_reasoning_model(inner, "deepseek-r1:14b")
    assert result is not None
