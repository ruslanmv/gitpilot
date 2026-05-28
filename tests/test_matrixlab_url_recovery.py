"""Backend tests for the stale-URL auto-detect + diagnostic logs.

These cover the two production bugs the install modal exposed:

* "Runner URL: http://localhost:8000 — Needs attention" after the
  :8000 → :8765 port migration.  /repair and /url/auto-detect now
  probe candidate ports and adopt the first live one.

* "Could not read MatrixLab logs."  The /logs endpoint now returns
  an actionable error + a list of matrixlab-shaped containers when
  the expected one is missing.
"""

from __future__ import annotations

from typing import Optional

import pytest

from gitpilot import matrixlab_admin_api as adm


# ---------------------------------------------------------------------------
# _discover_live_url
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_picks_first_responder(monkeypatch) -> None:
    async def fake_probe(url):
        return (url == "http://localhost:8765", None, None)

    monkeypatch.setattr(adm, "_probe_url", fake_probe)
    discovered = await adm._discover_live_url("http://localhost:8000")
    assert discovered == "http://localhost:8765"


@pytest.mark.asyncio
async def test_discover_skips_current_url(monkeypatch) -> None:
    """The current URL is already known-bad — don't waste a probe on it."""
    probed = []

    async def fake_probe(url):
        probed.append(url)
        return (False, "boom", None)

    monkeypatch.setattr(adm, "_probe_url", fake_probe)
    out = await adm._discover_live_url("http://localhost:8765")
    assert out is None
    # The current URL must not appear in the probe list.
    assert "http://localhost:8765" not in probed
    # And we did probe the other candidates.
    assert "http://localhost:8000" in probed


@pytest.mark.asyncio
async def test_discover_none_when_all_dead(monkeypatch) -> None:
    async def fake_probe(_url):
        return (False, "down", None)

    monkeypatch.setattr(adm, "_probe_url", fake_probe)
    assert await adm._discover_live_url("http://localhost:8000") is None


# ---------------------------------------------------------------------------
# discoveredUrl bubbles through _current_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_populates_discovered_url(monkeypatch) -> None:
    """When the configured URL is down and another responds, the
    /status payload exposes discoveredUrl so the modal can offer
    the one-click switch."""
    async def fake_probe_health():
        return (False, "connection refused", None)

    async def fake_discover(_current):
        return "http://localhost:8765"

    async def fake_gather():
        # Minimal lifecycle shape derive_status can consume.
        from gitpilot.sandbox_api import MatrixLabLifecycleResponse
        return MatrixLabLifecycleResponse(
            docker_available=True,
            installed=True,
            running=True,
            lifecycle_enabled=True,
            runner_image="ruslanmv/matrixlab-runner:latest",
            sandbox_images=[],
            container_name="gitpilot-matrixlab",
            matrixlab_url="http://localhost:8765",
            steps=[],
        )

    monkeypatch.setattr(adm, "_probe_health", fake_probe_health)
    monkeypatch.setattr(adm, "_discover_live_url", fake_discover)
    monkeypatch.setattr(adm, "_gather_lifecycle_status", fake_gather)

    status = await adm._current_status()
    assert status.discoveredUrl == "http://localhost:8765"


@pytest.mark.asyncio
async def test_status_no_discovered_when_already_healthy(monkeypatch) -> None:
    async def fake_probe_health():
        return (True, None, "1.2.3")

    calls = []

    async def fake_discover(_current):
        calls.append(1)
        return "http://localhost:9999"

    async def fake_gather():
        from gitpilot.sandbox_api import MatrixLabLifecycleResponse
        return MatrixLabLifecycleResponse(
            docker_available=True,
            installed=True,
            running=True,
            lifecycle_enabled=True,
            runner_image="ruslanmv/matrixlab-runner:latest",
            sandbox_images=[],
            container_name="gitpilot-matrixlab",
            matrixlab_url="http://localhost:8765",
            steps=[],
        )

    monkeypatch.setattr(adm, "_probe_health", fake_probe_health)
    monkeypatch.setattr(adm, "_discover_live_url", fake_discover)
    monkeypatch.setattr(adm, "_gather_lifecycle_status", fake_gather)

    status = await adm._current_status()
    assert status.discoveredUrl is None
    # Don't waste a probe when we're already healthy.
    assert calls == []


# ---------------------------------------------------------------------------
# /logs — diagnostic mode for "container not found"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logs_reports_missing_container_with_hint(monkeypatch) -> None:
    from gitpilot.sandbox_api import _StepResult

    monkeypatch.setattr(adm, "_docker_available", lambda: True)

    async def fake_run_shell(cmd, *, timeout=15):
        if "logs" in cmd:
            return _StepResult(
                cmd=" ".join(cmd), exit_code=1, duration_ms=10,
                stdout="", stderr='Error: No such container: gitpilot-matrixlab',
            )
        if cmd[:3] == ["docker", "ps", "-a"]:
            # Two other matrixlab-shaped containers from older installs.
            return _StepResult(
                cmd=" ".join(cmd), exit_code=0, duration_ms=5,
                stdout=(
                    "matrixlab-runner\tUp 3 minutes\n"
                    "old-matrixlab\tExited (137) 2 days ago\n"
                    "unrelated-container\tUp 5 minutes\n"
                ),
                stderr="",
            )
        return _StepResult(cmd=" ".join(cmd), exit_code=0, duration_ms=0, stdout="", stderr="")

    monkeypatch.setattr(adm, "_run_shell", fake_run_shell)

    payload = await adm.matrixlab_logs(tail=50)
    assert payload["ok"] is False
    assert payload["errorCode"] == "CONTAINER_NOT_FOUND"
    assert "make install-matrixlab" in payload["hint"]
    # We surfaced exactly the matrixlab-shaped candidates, not the
    # unrelated container.
    names = " ".join(payload["candidates"])
    assert "matrixlab-runner" in names
    assert "old-matrixlab" in names
    assert "unrelated-container" not in names


@pytest.mark.asyncio
async def test_logs_happy_path(monkeypatch) -> None:
    from gitpilot.sandbox_api import _StepResult

    monkeypatch.setattr(adm, "_docker_available", lambda: True)

    async def fake_run_shell(cmd, *, timeout=15):
        return _StepResult(
            cmd=" ".join(cmd), exit_code=0, duration_ms=12,
            stdout="line one\nline two\n", stderr="",
        )

    monkeypatch.setattr(adm, "_run_shell", fake_run_shell)

    payload = await adm.matrixlab_logs(tail=10)
    assert payload["ok"] is True
    assert payload["lines"] == ["line one", "line two"]
    assert payload["container"]  # surfaced for the UI badge


@pytest.mark.asyncio
async def test_logs_no_docker(monkeypatch) -> None:
    monkeypatch.setattr(adm, "_docker_available", lambda: False)
    payload = await adm.matrixlab_logs(tail=50)
    assert payload["ok"] is False
    assert payload["errorCode"] == "DOCKER_NOT_INSTALLED"
    assert "make install-matrixlab" in payload["hint"]
