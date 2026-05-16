"""Tests for the /api/sandbox/* HTTP surface.

These tests pin down the persistence + transparency contract so a
future change can't silently regress the production behaviour:

- backend persistence: PUT /config writes through, GET /status reads back
- env override: GITPILOT_SANDBOX leaks into /status.env_override
- token redaction: GET /settings never returns matrixlab_token
- snippet execution: POST /run with the default subprocess backend
  produces stdout the agent can read
- language whitelist: unknown languages return 400 not 500
- MatrixLab routing: backend=matrixlab POSTs to /code/run (verified
  with httpx.MockTransport rather than a live runner)
- lifecycle gating: mutating endpoints 403 when the env flag is off

These run without Docker, without Ollama, and without MatrixLab — they
exercise our code path only.
"""
from __future__ import annotations

import json
import os
from typing import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from gitpilot.settings import reload_settings


@pytest.fixture()
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """Spin up the FastAPI app with an isolated config dir so the
    tests don't read/write the real ~/.gitpilot/settings.json."""
    # CONFIG_DIR / CONFIG_FILE are module constants captured at import
    # time from the env, so a plain setenv after import is too late.
    # Patch the module attributes directly so reload_settings() reads
    # from our tmp dir.
    from gitpilot import settings as settings_module

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings_module, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(settings_module, "CONFIG_FILE", cfg_dir / "settings.json")
    monkeypatch.setenv("GITPILOT_CONFIG_DIR", str(cfg_dir))
    # Clear any env var that would override the persisted backend
    # so each test starts from the on-disk default.
    for name in (
        "GITPILOT_SANDBOX",
        "GITPILOT_MATRIXLAB_URL",
        "GITPILOT_MATRIXLAB_TOKEN",
        "GITPILOT_MATRIXLAB_IMAGE",
        "GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE",
    ):
        monkeypatch.delenv(name, raising=False)
    reload_settings()
    from gitpilot.api import app

    with TestClient(app) as c:
        yield c
    reload_settings()


def test_status_defaults_to_subprocess(client: TestClient) -> None:
    r = client.get("/api/sandbox/status")
    assert r.status_code == 200
    data = r.json()
    assert data["backend"] == "subprocess"
    assert data["ok"] is True
    assert data["has_token"] is False
    assert data["env_override"] is None
    assert sorted(data["available_backends"]) == ["matrixlab", "off", "subprocess"]


def test_config_persists_across_calls(client: TestClient) -> None:
    r = client.put(
        "/api/sandbox/config",
        json={"backend": "matrixlab", "matrixlab_url": "http://matrix.example:9000"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["backend"] == "matrixlab"
    assert data["matrixlab_url"] == "http://matrix.example:9000"
    # And GET /status now reflects it.
    r = client.get("/api/sandbox/status")
    assert r.json()["backend"] == "matrixlab"
    assert r.json()["matrixlab_url"] == "http://matrix.example:9000"


def test_config_rejects_unknown_backend(client: TestClient) -> None:
    r = client.put("/api/sandbox/config", json={"backend": "docker"})
    assert r.status_code == 400
    assert "unknown sandbox backend" in r.json()["detail"]


def test_token_redacted_from_settings_response(client: TestClient) -> None:
    """The secret matrixlab_token must never round-trip back to the
    browser.  GET /settings returns has_token instead."""
    client.put(
        "/api/sandbox/config",
        json={"backend": "matrixlab", "matrixlab_token": "very-secret"},
    )
    r = client.get("/api/settings")
    sandbox_block = r.json()["sandbox"]
    assert sandbox_block["has_token"] is True
    assert "matrixlab_token" not in sandbox_block


def test_env_var_surfaces_as_override(client: TestClient, monkeypatch) -> None:
    """Operators sometimes pin the backend with an env var on the host.
    The UI must be able to render an "env override" badge so users
    don't think their UI choice was silently lost."""
    monkeypatch.setenv("GITPILOT_SANDBOX", "subprocess")
    reload_settings()  # pick up the env after fixture cleanup
    r = client.get("/api/sandbox/status")
    assert r.json()["env_override"] == "GITPILOT_SANDBOX"


def test_run_python_via_subprocess_backend(client: TestClient) -> None:
    """Default backend executes a snippet and surfaces stdout."""
    r = client.post(
        "/api/sandbox/run",
        json={"language": "python", "code": "print('it works'); print(7 + 5)"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["backend"] == "subprocess"
    assert data["exit_code"] == 0
    assert "it works" in data["stdout"]
    assert "12" in data["stdout"]


def test_run_surfaces_python_traceback(client: TestClient) -> None:
    """Error retrieval contract: stderr comes back verbatim, exit
    code non-zero.  This is what the agent's run_in_sandbox tool
    reads to plan a fix."""
    r = client.post(
        "/api/sandbox/run",
        json={"language": "python", "code": "raise ValueError('boom')"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["exit_code"] != 0
    assert "ValueError" in data["stderr"]
    assert "boom" in data["stderr"]


def test_run_rejects_unknown_language(client: TestClient) -> None:
    r = client.post(
        "/api/sandbox/run",
        json={"language": "ruby", "code": "puts 'hi'"},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "ruby" in detail


def test_run_rejects_empty_code(client: TestClient) -> None:
    r = client.post("/api/sandbox/run", json={"language": "python", "code": "   "})
    assert r.status_code == 400


def test_matrixlab_backend_calls_code_run(client: TestClient, monkeypatch) -> None:
    """When the user picks matrixlab, GitPilot must POST to /code/run
    (the snippet endpoint), not /repo/run (which expects a repo_url).
    Use httpx.MockTransport so we don't need a real runner."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "sandbox_id": "test-uuid",
                "exit_code": 0,
                "stdout": "matrixlab response",
                "stderr": "",
                "duration_ms": 123,
            },
        )

    mock_transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):
        return real_async_client(transport=mock_transport, **{k: v for k, v in kwargs.items() if k != "transport"})

    monkeypatch.setattr(httpx, "AsyncClient", _patched)

    client.put("/api/sandbox/config", json={"backend": "matrixlab"})
    r = client.post(
        "/api/sandbox/run",
        json={"language": "py", "code": "print('hi')"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["backend"] == "matrixlab"
    assert data["stdout"] == "matrixlab response"
    assert data["sandbox_id"] == "test-uuid"
    assert captured["url"].endswith("/code/run")
    # Alias normalisation: "py" → "python" before hitting the runner.
    assert captured["body"]["language"] == "python"
    assert captured["body"]["code"] == "print('hi')"


def test_lifecycle_mutating_endpoints_gated(client: TestClient) -> None:
    """Without GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE, install/start/stop
    must 403 — never silently execute docker on behalf of a browser
    POST."""
    for path in ("/api/sandbox/matrixlab/install",
                 "/api/sandbox/matrixlab/start",
                 "/api/sandbox/matrixlab/stop"):
        r = client.post(path)
        assert r.status_code == 403, f"{path} must require the env flag"
        assert "GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE" in r.json()["detail"]


def test_lifecycle_status_always_safe(client: TestClient) -> None:
    """GET /lifecycle is read-only; safe to call even when the env
    flag is off.  Reports lifecycle_enabled=False so the UI can render
    the right hint."""
    r = client.get("/api/sandbox/matrixlab/lifecycle")
    assert r.status_code == 200
    data = r.json()
    assert data["lifecycle_enabled"] is False
    assert "instructions" in data
