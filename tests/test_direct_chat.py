"""Chatting with a provider that speaks HTTP, without the agent runtime.

The bug these cover: a correctly configured, reachable Ollama could not answer
a single question, because every chat path went through build_llm() and that
lazy-imports CrewAI. With CrewAI absent the message came back as
"Error processing message: No module named 'crewai'" — while the startup
banner said "LLM Provider ✅ OLLAMA".

So the assertions here are about two things: the direct path is actually taken
for the providers that support it, and every failure says something a person
can act on.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from gitpilot import _api_app as api_app
from gitpilot import direct_chat, settings as settings_mod
from gitpilot.direct_chat import ChatEndpoint, ProviderNotReachable
from gitpilot.session import SessionManager


# ── A stand-in for any OpenAI-compatible server ──────────────────────────


class _Recorder:
    """What the fake endpoint saw, so tests can assert on the request."""

    def __init__(self) -> None:
        self.path = ""
        self.model = ""
        self.authorization = ""
        self.headers: dict[str, str] = {}
        self.body: dict = {}


def _serve(handler_factory) -> tuple[str, _Recorder, HTTPServer]:
    recorder = _Recorder()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server's interface
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            recorder.path = self.path
            recorder.headers = dict(self.headers)
            recorder.authorization = self.headers.get("Authorization", "")
            recorder.body = json.loads(raw) if raw else {}
            recorder.model = recorder.body.get("model", "")

            status, payload = handler_factory(recorder)
            body = json.dumps(payload).encode() if payload is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, *args):  # keep pytest output clean
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", recorder, server


def _ok(content: str = "an answer"):
    return lambda _r: (
        200,
        {"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


@pytest.fixture()
def settings():
    s = settings_mod.get_settings()
    original = s.provider
    yield s
    s.provider = original


@pytest.fixture()
def folder_session(tmp_path, monkeypatch):
    """A folder-mode session — the case that could not answer at all."""
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "main.py").write_text("print('hi')\n")

    mgr = SessionManager(root=tmp_path / "state" / "sessions")
    monkeypatch.setattr(api_app, "_session_mgr", mgr)

    session = mgr.create(name="folder mode")
    session.mode = "folder"
    session.folder_path = str(workspace)
    mgr.save(session)
    return session


@pytest.fixture()
def client():
    return TestClient(api_app.app)


def _ask(client, session, message: str = "Explain this project's architecture") -> str:
    res = client.post(
        "/api/chat/send", json={"session_id": session.id, "message": message}
    )
    assert res.status_code == 200
    body = res.json()
    return str(body.get("message") or body.get("answer") or body)


# ── The reported failure ─────────────────────────────────────────────────


def test_ollama_answers_without_the_agent_runtime(settings, folder_session, client):
    """The exact case from the report: Ollama configured, CrewAI absent."""
    url, recorder, server = _serve(_ok("A FastAPI backend with a VS Code extension."))
    try:
        settings.provider = settings_mod.LLMProvider.ollama
        settings.ollama.base_url = url
        settings.ollama.model = "llama3:8b"

        answer = _ask(client, folder_session)

        assert answer == "A FastAPI backend with a VS Code extension."
        assert recorder.path == "/v1/chat/completions", (
            "Ollama's OpenAI-compatible surface is what every other provider "
            "here speaks"
        )
        assert recorder.model == "llama3:8b"
    finally:
        server.shutdown()


def test_the_conversation_is_saved(settings, folder_session, client):
    url, _recorder, server = _serve(_ok("saved answer"))
    try:
        settings.provider = settings_mod.LLMProvider.ollama
        settings.ollama.base_url = url
        settings.ollama.model = "llama3"

        _ask(client, folder_session, "remember this")

        reloaded = api_app._session_mgr.load(folder_session.id)
        roles = [(m.role, m.content) for m in reloaded.messages]
        assert ("user", "remember this") in roles
        assert ("assistant", "saved answer") in roles
    finally:
        server.shutdown()


# ── Every failure says something you can act on ──────────────────────────


def test_an_unreachable_provider_names_the_url(settings, folder_session, client):
    settings.provider = settings_mod.LLMProvider.ollama
    settings.ollama.base_url = "http://127.0.0.1:9"  # discard port
    settings.ollama.model = "llama3"

    answer = _ask(client, folder_session)

    assert "Could not reach ollama" in answer
    assert "127.0.0.1:9" in answer, "the URL is the thing the user has to fix"
    assert "crewai" not in answer.lower()


def test_a_missing_model_says_which_model(settings, folder_session, client):
    url, _recorder, server = _serve(lambda _r: (404, {"error": "model not found"}))
    try:
        settings.provider = settings_mod.LLMProvider.ollama
        settings.ollama.base_url = url
        settings.ollama.model = "nope:70b"

        answer = _ask(client, folder_session)

        assert "nope:70b" in answer
        assert "Pull it first" in answer
    finally:
        server.shutdown()


def test_an_empty_response_is_explained(settings, folder_session, client):
    url, _recorder, server = _serve(_ok(""))
    try:
        settings.provider = settings_mod.LLMProvider.ollama
        settings.ollama.base_url = url
        settings.ollama.model = "tiny"

        answer = _ask(client, folder_session)

        assert "empty response" in answer
        assert "small models" in answer
    finally:
        server.shutdown()


def test_a_provider_that_needs_the_runtime_says_how_to_install_it(
    settings, folder_session, client, monkeypatch
):
    """Claude and watsonx do not speak the OpenAI shape, so they still need it."""
    import builtins

    real_import = builtins.__import__

    def no_crewai(name, *args, **kwargs):
        if name == "crewai" or name.startswith("crewai."):
            raise ModuleNotFoundError("No module named 'crewai'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_crewai)

    settings.provider = settings_mod.LLMProvider.claude
    settings.claude.api_key = "sk-test"

    answer = _ask(client, folder_session)

    assert "agent runtime" in answer
    assert "pip install" in answer, "a dead end is not an error message"
    assert "No module named" not in answer


# ── Endpoint resolution ──────────────────────────────────────────────────


def test_openai_compatible_providers_resolve_to_an_endpoint(settings):
    settings.provider = settings_mod.LLMProvider.ollama
    settings.ollama.base_url = "http://localhost:11434"
    settings.ollama.model = "llama3"

    endpoint = direct_chat.resolve_endpoint(settings)
    assert endpoint is not None
    assert endpoint.url == "http://localhost:11434/v1/chat/completions"


def test_a_pasted_completions_url_is_normalised(settings):
    """People paste whatever their gateway's docs showed them."""
    settings.provider = settings_mod.LLMProvider.ollama
    settings.ollama.base_url = "http://localhost:11434/v1/chat/completions"
    settings.ollama.model = "llama3"

    endpoint = direct_chat.resolve_endpoint(settings)
    assert endpoint.url == "http://localhost:11434/v1/chat/completions", (
        "the URL must not end up doubled"
    )


def test_claude_and_watsonx_have_no_openai_endpoint(settings):
    for provider in (
        settings_mod.LLMProvider.claude,
        settings_mod.LLMProvider.watsonx,
    ):
        settings.provider = provider
        assert direct_chat.resolve_endpoint(settings) is None, (
            f"{provider} has its own wire format; claiming otherwise would "
            "send it somewhere that cannot answer"
        )


def test_openai_without_a_key_defers_to_the_runtime(settings):
    """Better the provider's own credential error than a 401 from us."""
    settings.provider = settings_mod.LLMProvider.openai
    settings.openai.api_key = ""
    settings.openai.base_url = ""
    assert direct_chat.resolve_endpoint(settings) is None


def test_custom_headers_travel_with_the_request(settings):
    url, recorder, server = _serve(_ok("routed"))
    try:
        settings.provider = settings_mod.LLMProvider.custom
        settings.custom.base_url = url
        settings.custom.model = "gateway-model"
        settings.custom.api_key = "key-1"
        settings.custom.headers = {"X-Team": "platform"}

        import asyncio

        answer = asyncio.run(direct_chat.chat([{"role": "user", "content": "hi"}]))

        assert answer == "routed"
        assert recorder.headers.get("X-Team") == "platform", (
            "gateways routinely require a header for attribution or routing"
        )
        assert recorder.authorization == "Bearer key-1"
    finally:
        server.shutdown()


def test_a_multipart_content_response_is_read(settings):
    """Some gateways answer in the multi-modal shape even for plain text."""
    payload = {
        "choices": [
            {"message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}}
        ]
    }
    url, _recorder, server = _serve(lambda _r: (200, payload))
    try:
        settings.provider = settings_mod.LLMProvider.ollama
        settings.ollama.base_url = url
        settings.ollama.model = "llama3"

        import asyncio

        assert asyncio.run(direct_chat.chat([{"role": "user", "content": "hi"}])) == "hello"
    finally:
        server.shutdown()


def test_no_model_selected_is_caught_before_the_request(settings):
    """Ollama has a default model; a bare gateway does not."""
    settings.provider = settings_mod.LLMProvider.custom
    settings.custom.base_url = "http://gateway.example.com"
    settings.custom.model = ""

    import asyncio

    with pytest.raises(ProviderNotReachable, match="No model is selected"):
        asyncio.run(direct_chat.chat([{"role": "user", "content": "hi"}]))


def test_ollama_keeps_its_default_model(settings):
    """Matching what build_llm() always did, so behaviour does not shift."""
    settings.provider = settings_mod.LLMProvider.ollama
    settings.ollama.base_url = "http://localhost:11434"
    settings.ollama.model = ""

    assert direct_chat.resolve_endpoint(settings).model == "llama3"


# ── The banner tells the truth ───────────────────────────────────────────


def test_the_runtime_check_reports_the_direct_path(settings):
    settings.provider = settings_mod.LLMProvider.ollama
    settings.ollama.base_url = "http://localhost:11434"
    settings.ollama.model = "llama3"

    runtime = direct_chat.describe_runtime()
    assert runtime["path"] == "direct"
    assert runtime["ready"] is True


def test_a_provider_needing_a_missing_runtime_is_not_ready(settings, monkeypatch):
    """Configuration is not the same as being able to answer."""
    import importlib.util

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "crewai" else real_find_spec(name, *a, **k),
    )

    settings.provider = settings_mod.LLMProvider.claude
    runtime = direct_chat.describe_runtime()

    assert runtime["path"] == "crewai"
    assert runtime["ready"] is False
    assert "pip install" in runtime["detail"]


def test_the_startup_check_refuses_to_claim_readiness_it_cannot_back(
    settings, monkeypatch
):
    """The banner said "LLM Provider ✅ OLLAMA" and then the first message failed."""
    import importlib.util

    from gitpilot.cli import _check_configuration

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "crewai" else real_find_spec(name, *a, **k),
    )

    settings.provider = settings_mod.LLMProvider.claude
    settings.claude.api_key = "sk-test"

    _has_env, _has_github, provider_ok, issues, warnings = _check_configuration()

    assert provider_ok is False
    assert any("cannot answer yet" in i for i in issues)
    assert any("pip install" in w for w in warnings)


# ── The provider probe must never block a request ────────────────────────


def test_a_stale_probe_refreshes_behind_the_request(monkeypatch):
    """Every early request took exactly 10.02s, which is a timeout, not slowness.

    autoconfigure_local_provider() probes Ollama and OllaBridge with a socket
    timeout each, and it ran inline inside `async def` handlers. When one of
    them hangs rather than refusing — WSL2's usual behaviour for an unbound
    port — that blocked the event loop, so /api/health, /api/status and
    /api/settings all finished together, ten seconds later, every TTL.
    """
    import threading
    import time

    probe_started = threading.Event()
    probe_may_finish = threading.Event()

    def slow_probe(provider, settings):
        probe_started.set()
        probe_may_finish.wait(timeout=5)
        return [], "unreachable"

    monkeypatch.setattr(
        "gitpilot.model_catalog.list_models_for_provider", slow_probe
    )
    monkeypatch.setattr(settings_mod, "_AUTOCONFIG_LAST_RUN_TS", 0.0)

    try:
        started = time.time()
        settings_mod.autoconfigure_local_provider()
        elapsed = time.time() - started

        assert elapsed < 1.0, (
            f"the request waited {elapsed:.1f}s on a provider probe; it must "
            "serve the cached value and refresh behind the request"
        )
        assert probe_started.wait(timeout=5), "the refresh should still happen"
    finally:
        probe_may_finish.set()


def test_only_one_refresh_runs_at_a_time(monkeypatch):
    """A burst of requests must not each start their own probe."""
    import threading
    import time

    calls = []
    release = threading.Event()

    def counting_probe(provider, settings):
        calls.append(provider)
        release.wait(timeout=5)
        return [], "unreachable"

    monkeypatch.setattr(
        "gitpilot.model_catalog.list_models_for_provider", counting_probe
    )
    monkeypatch.setattr(settings_mod, "_AUTOCONFIG_LAST_RUN_TS", 0.0)

    try:
        for _ in range(5):
            settings_mod.autoconfigure_local_provider()
        time.sleep(0.15)

        assert len(calls) <= 2, (
            f"five callers started {len(calls)} probes; the in-flight refresh "
            "should be shared"
        )
    finally:
        release.set()
        time.sleep(0.05)


def test_force_still_probes_inline(monkeypatch):
    """Explicit bootstrap paths genuinely need a fresh answer."""
    calls = []

    def probe(provider, settings):
        calls.append(provider)
        return [], "unreachable"

    monkeypatch.setattr("gitpilot.model_catalog.list_models_for_provider", probe)
    monkeypatch.setattr(settings_mod, "_AUTOCONFIG_LAST_RUN_TS", 0.0)

    settings_mod.autoconfigure_local_provider(force=True)

    assert calls, "force=True must not defer to a background thread"


# ── Reasoning models ─────────────────────────────────────────────────────
#
# deepseek-r1, QwQ and friends think before they answer. Where that
# thinking goes depends on the server: Ollama (checked on 0.12.9 and
# 0.32.6) puts it in a sibling ``reasoning`` field and leaves ``content``
# clean, while llama.cpp, vLLM and LM Studio inline it as ``<think>`` tags.
# The CrewAI path has always handled the tags — build_llm() wraps these
# models — but this path does not go through CrewAI, so it handled neither.


def test_inline_think_tags_do_not_reach_the_user(settings, folder_session, client):
    """A gateway that inlines its reasoning must not show it as the answer."""
    url, _recorder, server = _serve(
        _ok("<think>Let me work through this carefully.</think>The answer is 42.")
    )
    try:
        settings.provider = settings_mod.LLMProvider.ollama
        settings.ollama.base_url = url
        settings.ollama.model = "deepseek-r1:1.5b"

        import asyncio

        answer = asyncio.run(direct_chat.chat([{"role": "user", "content": "hi"}]))

        assert answer == "The answer is 42."
        assert "<think>" not in answer, "the scratchpad is not the reply"
    finally:
        server.shutdown()


def test_a_model_that_only_thinks_still_shows_something(settings, folder_session, client):
    """Stripping everything is worse than showing the reasoning.

    A small reasoning model can spend its whole budget thinking and close
    the tag with nothing after it. Returning "" there surfaces as "returned
    an empty response", which points at the provider — and the provider
    answered fine.
    """
    url, _recorder, server = _serve(_ok("<think>still thinking about it</think>"))
    try:
        settings.provider = settings_mod.LLMProvider.ollama
        settings.ollama.base_url = url
        settings.ollama.model = "deepseek-r1:1.5b"

        import asyncio

        answer = asyncio.run(direct_chat.chat([{"role": "user", "content": "hi"}]))

        assert "still thinking about it" in answer, (
            "partial output beats a wrong diagnosis about an empty response"
        )
    finally:
        server.shutdown()


def test_reasoning_in_its_own_field_is_used_when_content_is_empty():
    """Ollama's shape: reasoning beside content, not inside it."""
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning": "I have been thinking the whole time.",
                }
            }
        ]
    }

    assert direct_chat._extract(payload) == "I have been thinking the whole time."


def test_content_still_wins_when_both_are_present():
    """The reply is the reply; reasoning is only the fallback."""
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "The answer is 42.",
                    "reasoning": "Let me think...",
                }
            }
        ]
    }

    assert direct_chat._extract(payload) == "The answer is 42."


def test_a_plain_model_is_untouched():
    """No tags, no reasoning field, no behaviour change."""
    assert direct_chat._without_reasoning("just an answer", "llama3") == "just an answer"
