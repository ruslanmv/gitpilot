"""What happens when the agent runtime is present but incomplete.

The report these come from: a correctly configured, reachable Ollama, a
running server, and every message answered with

    Error processing message: Fallback to LiteLLM is not available

That sentence is CrewAI's, and it names neither Ollama nor the package that is
missing. Two separate faults produce it, and both are covered here.

**The routing fault.** ``LLM(model="ollama/llama3:8b")`` looks unambiguous and
is not. CrewAI maps the prefix to a canonical provider, then validates the
model part against its own constants — and its validator has no Ollama branch,
so it falls through to a pattern match that a locally created model, or one
released after the pattern was written, does not satisfy. CrewAI then routes
the call to its optional LiteLLM fallback instead of to Ollama.

Naming Ollama as the provider does not rescue it either, and that is the part
worth pinning down here. Reproduced against the reporter's exact stack —
CrewAI 1.6.0, no LiteLLM, a real Ollama serving qwen2.5:0.5b —
``provider="ollama"`` fails identically, because 1.6.0's
``SUPPORTED_NATIVE_PROVIDERS`` is ``[openai, anthropic, claude, azure,
azure_openai, google, gemini, bedrock, aws]``. Ollama is not in it, so the
call falls straight through to the same missing fallback. Ollama joined that
list around 1.10; every release before it can only be reached one way.

That way is ``provider="openai"``. Ollama, OllaBridge, Open WebUI and custom
gateways all serve the OpenAI chat-completions API, "openai" has been native
for as long as the native list has existed, and an explicit provider skips
both the inference and the model-name validation that reject these models.
The same fault, and the same fix, apply to all four providers — they were all
spelling it ``openai/<non-openai-model>``, which fails validation and lands in
the LiteLLM fallback exactly like the Ollama prefix did.

**The reporting fault.** CrewAI raises a plain ``ImportError`` for the absent
fallback, and the handlers caught ``ModuleNotFoundError`` — which does not
catch it, because the subclassing goes the other way. So the actionable
message was written and never reached anyone.
"""

from __future__ import annotations

import os
import sys
import types
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from gitpilot import _api_app as api_app
from gitpilot import llm_provider
from gitpilot.settings import LLMProvider


# ── A CrewAI that records how it was called ──────────────────────────────


class _FakeLLM:
    """Stands in for ``crewai.LLM``, capturing its constructor arguments."""

    calls: list[dict] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        type(self).calls.append(kwargs)


#: What CrewAI 1.6.0 — the reporter's version — will route natively.
#: Note what is absent: ollama. That absence is the whole bug.
CREWAI_1_6_NATIVE = (
    "openai",
    "anthropic",
    "claude",
    "azure",
    "azure_openai",
    "google",
    "gemini",
    "bedrock",
    "aws",
)

#: A later CrewAI, where Ollama did become native. The fix has to keep
#: working here too, which is why it is asserted against both.
CREWAI_1_15_NATIVE = CREWAI_1_6_NATIVE + ("ollama", "ollama_chat", "hosted_vllm")


@pytest.fixture()
def crewai_stub(monkeypatch: pytest.MonkeyPatch):
    """Install a fake ``crewai`` module for build_llm()'s lazy import.

    build_llm imports CrewAI at call time precisely so the real package's ~180
    dependencies never load at startup; that same laziness is what lets a stub
    be swapped in here.

    ``native`` is the stand-in for ``crewai.llm.SUPPORTED_NATIVE_PROVIDERS``,
    the list build_llm reads to decide how to spell the call. Passing ``None``
    models a CrewAI so old the attribute does not exist at all.
    """

    def _install(cls=_FakeLLM, native=CREWAI_1_6_NATIVE):
        cls.calls = []
        module = types.ModuleType("crewai")
        module.LLM = cls
        llm_module = types.ModuleType("crewai.llm")
        llm_module.LLM = cls
        if native is not None:
            llm_module.SUPPORTED_NATIVE_PROVIDERS = list(native)
        module.llm = llm_module
        monkeypatch.setitem(sys.modules, "crewai", module)
        monkeypatch.setitem(sys.modules, "crewai.llm", llm_module)
        return cls

    return _install


@pytest.fixture()
def ollama_settings(monkeypatch: pytest.MonkeyPatch):
    """Point build_llm() at Ollama with a model and base URL we choose."""

    def _configure(model: str, base_url: str = "http://127.0.0.1:11434"):
        settings = types.SimpleNamespace(
            provider=LLMProvider.ollama,
            ollama=types.SimpleNamespace(model=model, base_url=base_url),
        )
        monkeypatch.setattr(llm_provider, "get_settings", lambda: settings)
        return settings

    return _configure


# ── The routing fault ────────────────────────────────────────────────────


def test_ollama_is_routed_through_the_native_openai_adapter(
    crewai_stub, ollama_settings
) -> None:
    LLM = crewai_stub()
    ollama_settings("llama3:8b", "http://127.0.0.1:11434")

    llm_provider.build_llm()

    assert len(LLM.calls) == 1
    kwargs = LLM.calls[0]
    assert kwargs["provider"] == "openai", (
        "Ollama serves the OpenAI chat API, and 'openai' is the only provider "
        "CrewAI 1.6 will route natively. provider='ollama' is more honest and "
        "lands in the LiteLLM fallback, which is the bug being fixed"
    )
    assert kwargs["model"] == "llama3:8b", "the prefix belongs to provider=, not to the model name"
    assert kwargs["base_url"] == "http://127.0.0.1:11434/v1", (
        "the OpenAI-compatible surface is /v1; the bare root is the Ollama API"
    )
    assert kwargs["api_key"], "the OpenAI-shaped client insists on a key even though Ollama ignores it"


def test_ollama_is_not_left_to_the_litellm_fallback_on_crewai_1_6(
    crewai_stub, ollama_settings
) -> None:
    """The regression guard for the fix that was not enough.

    Naming Ollama looks like the fix and is not: on the reporter's CrewAI,
    "ollama" is not in the native list, so the call falls through to a
    LiteLLM that is not installed and dies as "Fallback to LiteLLM is not
    available". Reproduced against the real 1.6.0 before this was written.
    """
    LLM = crewai_stub(native=CREWAI_1_6_NATIVE)
    ollama_settings("llama3:8b")

    llm_provider.build_llm()

    assert LLM.calls[0]["provider"] in CREWAI_1_6_NATIVE, (
        "a provider CrewAI 1.6 cannot route natively is a provider that ends "
        "up in the LiteLLM fallback"
    )


def test_the_routing_survives_a_crewai_that_did_learn_ollama(
    crewai_stub, ollama_settings
) -> None:
    """Later CrewAI routes ollama natively — to the same OpenAI-compatible
    client, at the same /v1. So the fix needs no version branch, and taking
    one out is what keeps this working on both."""
    LLM = crewai_stub(native=CREWAI_1_15_NATIVE)
    ollama_settings("llama3:8b")

    llm_provider.build_llm()

    assert LLM.calls[0]["provider"] == "openai"
    assert LLM.calls[0]["base_url"] == "http://127.0.0.1:11434/v1"


@pytest.mark.parametrize(
    "configured",
    [
        "llama3:8b",
        "ollama/llama3:8b",  # already prefixed — the settings UI allows both
    ],
)
def test_an_already_prefixed_model_is_not_prefixed_twice(
    crewai_stub, ollama_settings, configured: str
) -> None:
    LLM = crewai_stub()
    ollama_settings(configured)

    llm_provider.build_llm()

    assert LLM.calls[0]["model"] == "llama3:8b", (
        f"{configured!r} must reach CrewAI as the bare model name; "
        "'ollama/ollama/llama3:8b' resolves to nothing"
    )


def test_a_locally_created_model_reaches_ollama_unchanged(
    crewai_stub, ollama_settings
) -> None:
    # The names that broke it: `ollama create` lets you call a model anything,
    # and nothing about that name will satisfy a validator written for the
    # public registry.
    LLM = crewai_stub()
    ollama_settings("my-finetune-v2")

    llm_provider.build_llm()

    assert LLM.calls[0]["model"] == "my-finetune-v2"
    assert LLM.calls[0]["provider"] == "openai"


def test_a_namespaced_ollama_model_keeps_its_slashes(
    crewai_stub, ollama_settings
) -> None:
    """`ollama pull hf.co/user/model` is a real thing, and the path is the
    name. Splitting on the first slash to strip a prefix would eat part of
    it — so only a known prefix is ever removed."""
    LLM = crewai_stub()
    ollama_settings("hf.co/bartowski/some-model-GGUF")

    llm_provider.build_llm()

    assert LLM.calls[0]["model"] == "hf.co/bartowski/some-model-GGUF"


def test_selecting_ollama_does_not_leave_a_fake_openai_key_behind(
    crewai_stub, ollama_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The placeholder key must not outlive the call that needed it.

    Ollama ignores the key but the OpenAI-shaped client demands one, so a
    placeholder is passed. Exporting that placeholder to os.environ would be
    a trap with a delay on it: OPENAI_API_KEY is process-wide, and
    direct_chat.resolve_endpoint reads it to decide whether OpenAI is
    configured. Someone who ran Ollama and then switched to OpenAI without
    entering a key would be sent to api.openai.com holding the string
    "ollama" — a 401 about a key they never set, instead of the
    "OpenAI API key is required" that names the actual problem.
    """
    crewai_stub()
    ollama_settings("llama3:8b")
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delitem(os.environ, "OPENAI_API_KEY", raising=False)

    llm_provider.build_llm()

    assert "OPENAI_API_KEY" not in os.environ, (
        "the placeholder belongs to this one call, not to the process"
    )


def test_a_crewai_with_no_native_adapters_falls_back_to_the_prefixed_name(
    crewai_stub, ollama_settings
) -> None:
    """CrewAI 0.x has no native adapters at all — but it depends on LiteLLM
    outright, so the prefixed spelling both works and is all there is."""
    LLM = crewai_stub(native=None)
    ollama_settings("llama3:8b")

    llm_provider.build_llm()

    assert len(LLM.calls) == 1
    kwargs = LLM.calls[0]
    assert "provider" not in kwargs
    assert kwargs["model"] == "openai/llama3:8b"
    assert kwargs["base_url"] == "http://127.0.0.1:11434/v1"


# ── The same fault, in the three sibling providers ───────────────────────


@pytest.mark.parametrize(
    "provider, config, expected_model, expected_base",
    [
        (
            LLMProvider.ollabridge,
            {"model": "qwen2.5:1.5b", "base_url": "http://localhost:11435", "api_key": ""},
            "qwen2.5:1.5b",
            "http://localhost:11435/v1",
        ),
        (
            LLMProvider.openwebui,
            {"model": "llama3.2:3b", "base_url": "http://localhost:3000", "api_key": "sk-x"},
            "llama3.2:3b",
            "http://localhost:3000/api",
        ),
        (
            LLMProvider.custom,
            {
                "model": "my-gateway-model",
                "base_url": "https://gw.example.com/v1",
                "api_key": "k",
                "headers": {},
            },
            "my-gateway-model",
            "https://gw.example.com/v1",
        ),
    ],
)
def test_every_openai_shaped_provider_names_openai_explicitly(
    crewai_stub,
    monkeypatch: pytest.MonkeyPatch,
    provider: LLMProvider,
    config: dict,
    expected_model: str,
    expected_base: str,
) -> None:
    """These three were failing the same way Ollama was, for the same reason.

    They spelled it ``model="openai/qwen2.5:1.5b"``, which reads as settled
    and is not: CrewAI validates the part after the prefix against its OpenAI
    model constants, no model these endpoints serve is in that list, and the
    call goes to the LiteLLM fallback. Verified against the real CrewAI 1.6.0
    — all three raised the reporter's error before this change.
    """
    LLM = crewai_stub(native=CREWAI_1_6_NATIVE)
    settings = types.SimpleNamespace(
        provider=provider, **{provider.value: types.SimpleNamespace(**config)}
    )
    monkeypatch.setattr(llm_provider, "get_settings", lambda: settings)
    # These three branches export OPENAI_API_KEY / OPENAI_API_BASE so that
    # LiteLLM, when it is in play, does not fall back to api.openai.com.
    # os.environ outlives the test, and direct_chat.resolve_endpoint reads
    # OPENAI_API_KEY to decide whether OpenAI is configured — so a real
    # write here makes a later test, or a later request, believe a key
    # exists. Give the writes somewhere harmless to land.
    monkeypatch.setattr(os, "environ", dict(os.environ))

    llm_provider.build_llm()

    kwargs = LLM.calls[0]
    assert kwargs["provider"] == "openai"
    assert kwargs["model"] == expected_model, (
        "the model id is the endpoint's own; prefixing it is what broke this"
    )
    assert kwargs["base_url"] == expected_base


# ── The reporting fault ──────────────────────────────────────────────────


@pytest.fixture()
def active_provider(monkeypatch: pytest.MonkeyPatch):
    """Set the provider the hint text is written against."""

    def _set(provider: LLMProvider):
        import gitpilot.settings as settings_mod

        monkeypatch.setattr(
            settings_mod,
            "get_settings",
            lambda: types.SimpleNamespace(provider=provider),
        )
        return provider

    return _set


def test_a_missing_litellm_is_named_along_with_the_way_out(active_provider) -> None:
    active_provider(LLMProvider.claude)

    hint = api_app._agent_runtime_hint(ImportError("Fallback to LiteLLM is not available"))

    assert "litellm" in hint.lower(), "the missing package has to appear in the message"
    assert "pip install litellm" in hint, "and the command that installs it"
    assert "ollama" in hint.lower(), "and the providers that do not need it at all"


@pytest.mark.parametrize(
    "provider",
    [
        LLMProvider.ollama,
        LLMProvider.ollabridge,
        LLMProvider.openwebui,
        LLMProvider.custom,
    ],
)
def test_an_openai_shaped_provider_is_not_told_to_switch_to_itself(
    active_provider, provider: LLMProvider
) -> None:
    """The advice has to be read against the provider in force.

    "Switch to Ollama, OllaBridge, Open WebUI, OpenAI or a custom endpoint"
    is a dead end when the user is already on one of them — which, for this
    bug, they always are. These providers need no LiteLLM at all, so reaching
    its fallback means the CrewAI underneath is too old to route them, and an
    upgrade is the fix.
    """
    active_provider(provider)

    hint = api_app._agent_runtime_hint(ImportError("Fallback to LiteLLM is not available"))

    assert provider.value in hint.lower(), "name the provider that actually failed"
    assert "crewai" in hint.lower(), "and the package whose age is the cause"
    assert "pip install -U" in hint, "and the upgrade that fixes it"


def test_a_missing_crewai_is_a_different_message() -> None:
    hint = api_app._agent_runtime_hint(ModuleNotFoundError("No module named 'crewai'"))

    assert "pip install litellm" not in hint, (
        "installing LiteLLM does nothing when CrewAI itself is absent"
    )
    assert "agents" in hint, "the extra that installs the agent runtime"


@pytest.mark.parametrize(
    "err",
    [
        ImportError("Fallback to LiteLLM is not available"),
        ModuleNotFoundError("No module named 'crewai'"),
    ],
)
def test_every_runtime_hint_says_what_to_do(err: Exception) -> None:
    hint = api_app._agent_runtime_hint(err)
    assert "pip install" in hint
    assert "Settings" in hint, "a message with no second option is a dead end"


@pytest.mark.anyio
async def test_a_plain_importerror_from_crewai_is_caught() -> None:
    """``except ModuleNotFoundError`` was the bug: it does not catch this.

    ModuleNotFoundError is a *subclass* of ImportError, so catching the
    subclass leaves the parent — which is exactly what CrewAI raises for its
    absent fallback — to escape as a 500.
    """
    import gitpilot.llm_provider as lp

    def _boom():
        raise ImportError("Fallback to LiteLLM is not available")

    original = lp.build_llm
    lp.build_llm = _boom
    try:
        with pytest.raises(api_app._ProviderNotReachable) as caught:
            await api_app._chat_via_agent_runtime([{"role": "user", "content": "hi"}])
    finally:
        lp.build_llm = original

    assert "litellm" in str(caught.value).lower()


# ── /api/chat/plan — the path direct_chat does not cover ─────────────────


@pytest.fixture()
def plan_client(monkeypatch: pytest.MonkeyPatch):
    """The real app, with the planner and its auth context stubbed out."""

    @contextmanager
    def _noop_ctx(*_a, **_kw):
        yield

    monkeypatch.setattr(api_app, "execution_context", _noop_ctx)
    monkeypatch.setattr(api_app, "_is_lite_mode_active", lambda: False)
    return TestClient(api_app.app)


def _post_plan(client: TestClient):
    return client.post(
        "/api/chat/plan",
        json={"goal": "add a test", "repo_owner": "x", "repo_name": "y", "branch_name": "main"},
    )


@pytest.mark.parametrize(
    "raised",
    [
        ImportError("Fallback to LiteLLM is not available"),
        ModuleNotFoundError("No module named 'crewai'"),
        RuntimeError("Fallback to LiteLLM is not available"),  # re-raised, type lost
    ],
)
def test_plan_reports_an_unavailable_runtime_as_503(
    plan_client: TestClient, monkeypatch: pytest.MonkeyPatch, raised: Exception
) -> None:
    """Planning still goes through CrewAI, so it fails where chat no longer does.

    ``/api/chat/send`` was fixed by bypassing CrewAI entirely for the
    HTTP-speaking providers. Planning cannot be: the LLM object is handed to
    CrewAI Agents. So the deliverable here is an honest failure, not a
    traceback.
    """

    async def _raise(*_a, **_kw):
        raise raised

    monkeypatch.setattr(api_app, "generate_plan", _raise)

    resp = _post_plan(plan_client)

    assert resp.status_code == 503, resp.text
    detail = resp.json()["detail"]
    assert "pip install" in detail, "503 with no remedy is just a nicer 500"
    assert "Settings" in detail


def test_plan_still_reports_unrelated_failures_as_500(
    plan_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _raise(*_a, **_kw):
        raise RuntimeError("completely unrelated boom")

    monkeypatch.setattr(api_app, "generate_plan", _raise)

    resp = _post_plan(plan_client)

    assert resp.status_code == 500, "the runtime branch must not swallow everything else"
    assert "completely unrelated boom" in resp.json()["detail"]
