"""The endpoint that answers "it says connected, so why did that not work?".

Every field here has been the cause of a real report, and each one was
previously only discoverable from a terminal: a backend older than the client,
a provider configured but unable to answer, a package imported from
site-packages rather than the checkout someone had just pulled.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gitpilot import _api_app as api_app
from gitpilot import settings as settings_mod
from gitpilot.version import __version__


@pytest.fixture()
def client():
    return TestClient(api_app.app)


@pytest.fixture()
def settings():
    s = settings_mod.get_settings()
    original = s.provider
    yield s
    s.provider = original


def test_health_carries_the_version(client):
    """The one endpoint every client calls on every connect.

    Without this a client cannot tell it is talking to a backend from a
    different build — which looks completely healthy right up until one
    feature misbehaves for reasons nothing on screen explains.
    """
    body = client.get("/api/health").json()

    assert body["status"] == "healthy"
    assert body["version"] == __version__


def test_health_stays_cheap(client):
    """It is used to detect slowness, so it must not become slow itself."""
    import time

    started = time.time()
    for _ in range(20):
        client.get("/api/health")
    elapsed = time.time() - started

    assert elapsed < 2.0, f"20 health checks took {elapsed:.1f}s"


def test_diagnostics_reports_what_a_failure_needs(client, settings):
    settings.provider = settings_mod.LLMProvider.ollama
    settings.ollama.base_url = "http://localhost:11434"
    settings.ollama.model = "llama3"

    body = client.get("/api/diagnostics").json()

    assert body["version"] == __version__
    assert body["chat"]["path"] == "direct"
    assert body["chat"]["ready"] is True
    assert body["provider"]["name"] == "ollama"
    assert body["provider"]["endpoint"] == "http://localhost:11434/v1"
    assert "crewai" in body["runtimes"]


def test_diagnostics_names_where_the_package_was_imported_from(client):
    """The answer to "I pulled but nothing changed".

    `gitpilot` is a console script, so it imports from site-packages rather
    than the directory you are standing in. Printing the path makes a stale
    install visible instead of something you have to deduce.
    """
    body = client.get("/api/diagnostics").json()

    assert body["package_path"].endswith("gitpilot")
    assert body["python"]["executable"]


def test_diagnostics_says_when_chat_cannot_run(client, settings, monkeypatch):
    import importlib.util

    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "crewai" else real_find_spec(name, *a, **k),
    )

    settings.provider = settings_mod.LLMProvider.claude
    settings.claude.api_key = "sk-test"

    body = client.get("/api/diagnostics").json()

    assert body["chat"]["path"] == "crewai"
    assert body["chat"]["ready"] is False
    assert "pip install" in body["chat"]["detail"]


def test_diagnostics_never_raises(client, monkeypatch):
    """A diagnostics endpoint that fails when things are broken is useless."""
    def explode(*_a, **_k):
        raise RuntimeError("settings are unreadable")

    monkeypatch.setattr("gitpilot.direct_chat.describe_runtime", explode)

    res = client.get("/api/diagnostics")

    assert res.status_code == 200
    body = res.json()
    assert body["version"] == __version__
    assert body["chat"]["ready"] is False
    assert "unreadable" in body["chat"]["detail"]
