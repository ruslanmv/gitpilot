"""Tests for the enterprise MatrixLab install/admin surface.

Two layers:

1. Pure helpers (`classify_error`, `derive_status`) — verified
   without spinning up FastAPI or httpx. These are the smarts that
   keep raw Docker / HTTP errors out of the install modal.
2. HTTP endpoints — driven through ``TestClient`` with the docker /
   httpx layer monkeypatched so the tests stay hermetic. No real
   Docker, no real MatrixLab runner.

The contract under test is: every endpoint returns the same
:class:`MatrixLabStatus` shape; ``status`` is one of the documented
literals; raw errors land in ``technicalDetails.rawError`` only.
"""
from __future__ import annotations

from typing import Iterator, List

import httpx
import pytest
from fastapi.testclient import TestClient

from gitpilot import matrixlab_admin_api as mla
from gitpilot.matrixlab_admin_api import (
    MatrixLabStatus,
    classify_error,
    derive_status,
)
from gitpilot.sandbox_api import MatrixLabLifecycleResponse, _StepResult
from gitpilot.settings import reload_settings


# ---------------------------------------------------------------------
# Pure helper tests — no FastAPI, no httpx, no Docker
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected_code",
    [
        ("Expecting value: line 1 column 1 (char 0)", "INVALID_HEALTH_RESPONSE"),
        ("Invalid JSON: foo", "INVALID_HEALTH_RESPONSE"),
        ("[Errno 111] Connection refused", "CONNECTION_REFUSED"),
        ("the connection was refused by peer", "CONNECTION_REFUSED"),
        ("timed out waiting for response", "TIMEOUT"),
        ("Read timeout reached", "TIMEOUT"),
        ("Name or service not known", "DNS_ERROR"),
        ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock", "DOCKER_NOT_RUNNING"),
        ("Error response from docker daemon: ...", "DOCKER_NOT_RUNNING"),
        ("driver failed: port is already allocated", "PORT_IN_USE"),
        ("listen tcp 0.0.0.0:8000: bind: address already in use", "PORT_IN_USE"),
        ("permission denied while connecting to socket", "PERMISSION_DENIED"),
        ("No such image: matrixlab-runner:latest", "IMAGE_MISSING"),
        ("docker not on PATH", "DOCKER_NOT_INSTALLED"),
    ],
)
def test_classify_error_maps_known_traces(raw: str, expected_code: str) -> None:
    code, msg = classify_error(raw)
    assert code == expected_code
    assert msg  # always non-empty for non-empty inputs


def test_classify_error_empty_returns_empty() -> None:
    """Truthy guard so callers can branch on ``if code``."""
    assert classify_error("") == ("", "")
    assert classify_error(None) == ("", "")


def test_classify_error_unknown_is_documented_unknown_code() -> None:
    """Anything the table doesn't cover gets a stable ``UNKNOWN`` code
    so the UI can still drive a copy lookup table off it."""
    code, msg = classify_error("something completely novel and unparseable")
    assert code == "UNKNOWN"
    assert "unexpected" in msg.lower()


# ---------------------------------------------------------------------
# derive_status — the state machine that drives the modal
# ---------------------------------------------------------------------

def _lifecycle(**overrides) -> MatrixLabLifecycleResponse:
    base = dict(
        docker_available=True,
        installed=False,
        running=False,
        lifecycle_enabled=True,
        runner_image="ruslanmv/matrixlab-runner:latest",
        sandbox_images=["matrix-lab-sandbox-python:latest"],
        container_name="gitpilot-matrixlab",
        matrixlab_url="http://localhost:8000",
        steps=[],
    )
    base.update(overrides)
    return MatrixLabLifecycleResponse(**base)


def test_derive_status_not_installed_when_no_image_and_no_container() -> None:
    s = derive_status(
        lifecycle=_lifecycle(installed=False, running=False),
        reachable=False,
        health_error=None,
        active_sandbox="subprocess",
    )
    assert s.status == "not_installed"
    assert s.installed is False
    assert s.running is False
    assert s.errorCode is None
    assert "not installed" in s.message.lower()


def test_derive_status_ready_when_running_and_reachable() -> None:
    s = derive_status(
        lifecycle=_lifecycle(installed=True, running=True),
        reachable=True,
        health_error=None,
        active_sandbox="matrixlab",
    )
    assert s.status == "ready"
    assert s.installed is True
    assert s.running is True
    assert s.reachable is True
    assert s.errorCode is None


def test_derive_status_needs_attention_when_running_but_unreachable() -> None:
    """The bug from the original screenshot — runner is up but /health
    returned an empty body. The modal must NOT show contradictory
    badges."""
    s = derive_status(
        lifecycle=_lifecycle(installed=True, running=True),
        reachable=False,
        health_error="Expecting value: line 1 column 1 (char 0)",
        active_sandbox="local",
    )
    assert s.status == "needs_attention"
    # Single coherent state — running stays true but reachable=false
    # and we flag an error code rather than contradict ourselves.
    assert s.running is True
    assert s.reachable is False
    assert s.errorCode == "INVALID_HEALTH_RESPONSE"
    # User-facing message must NOT contain the raw stack-trace-like phrase
    assert "Expecting value" not in s.message
    assert "cannot connect" in s.message.lower()
    # Raw error preserved for the disclosure
    assert s.technicalDetails is not None
    assert s.technicalDetails.rawError == "Expecting value: line 1 column 1 (char 0)"


def test_derive_status_needs_attention_when_installed_but_stopped() -> None:
    s = derive_status(
        lifecycle=_lifecycle(installed=True, running=False),
        reachable=False,
        health_error=None,
        active_sandbox="subprocess",
    )
    assert s.status == "needs_attention"
    assert s.errorCode == "STOPPED"


def test_derive_status_needs_attention_when_docker_missing() -> None:
    s = derive_status(
        lifecycle=_lifecycle(docker_available=False),
        reachable=False,
        health_error=None,
        active_sandbox="subprocess",
    )
    assert s.status == "needs_attention"
    assert s.dockerAvailable is False
    # Docker-missing maps to a clear code, not just UNKNOWN
    assert s.errorCode in ("DOCKER_NOT_INSTALLED", "DOCKER_NOT_RUNNING")


@pytest.mark.parametrize("in_flight", ["installing", "starting", "stopping", "checking"])
def test_derive_status_in_flight_overrides_terminal_state(in_flight: str) -> None:
    """While a long-running install/start is mid-flight the modal must
    show the progress state, not the stale terminal state."""
    s = derive_status(
        lifecycle=_lifecycle(installed=False, running=False),
        reachable=False,
        health_error=None,
        active_sandbox="subprocess",
        in_flight=in_flight,  # type: ignore[arg-type]
    )
    assert s.status == in_flight
    # Progress messages are friendly, not jargon
    assert s.message and "…" in s.message


def test_status_response_excludes_secrets_and_tracebacks() -> None:
    """Critical UX invariant: the public ``message`` never leaks
    a raw exception trace. Operators see it only under
    ``technicalDetails.rawError``."""
    s = derive_status(
        lifecycle=_lifecycle(installed=True, running=True),
        reachable=False,
        health_error="Traceback (most recent call last): json.decoder.JSONDecodeError",
        active_sandbox="local",
    )
    assert "Traceback" not in s.message
    assert "JSONDecodeError" not in s.message
    assert s.technicalDetails is not None
    assert "Traceback" in (s.technicalDetails.rawError or "")


# ---------------------------------------------------------------------
# Endpoint tests — mocked docker + mocked httpx
# ---------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """Hermetic TestClient with docker + httpx stubbed so the tests
    never touch the real host."""
    from gitpilot import settings as settings_module

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings_module, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(settings_module, "CONFIG_FILE", cfg_dir / "settings.json")
    monkeypatch.setenv("GITPILOT_CONFIG_DIR", str(cfg_dir))
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


def _stub_docker(monkeypatch, *, available: bool, installed: bool, running: bool) -> None:
    """Pretend docker is/isn't on PATH and that the runner image / URL
    are present without invoking any subprocesses or sockets."""

    async def fake_image_present(name: str) -> bool:
        return installed

    async def fake_running() -> bool:
        return running

    monkeypatch.setattr(
        "gitpilot.sandbox_api._docker_available", lambda: available,
    )
    # matrixlab_admin_api imported the same symbol by name; patch its
    # bound reference too so the install path sees our stub.
    monkeypatch.setattr(
        "gitpilot.matrixlab_admin_api._docker_available", lambda: available,
    )
    monkeypatch.setattr(
        "gitpilot.sandbox_api._docker_image_present", fake_image_present,
    )
    monkeypatch.setattr(
        "gitpilot.sandbox_api._matrixlab_running", fake_running,
    )


def _stub_health(monkeypatch, *, reachable: bool, raw_error: str | None = None,
                 version: str | None = None) -> None:
    async def fake_probe() -> tuple[bool, str | None, str | None]:
        return (reachable, raw_error, version)

    monkeypatch.setattr(
        "gitpilot.matrixlab_admin_api._probe_health", fake_probe,
    )


def test_status_endpoint_returns_not_installed_on_fresh_host(client, monkeypatch) -> None:
    _stub_docker(monkeypatch, available=True, installed=False, running=False)
    _stub_health(monkeypatch, reachable=False)

    r = client.get("/api/matrixlab/status")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "not_installed"
    assert data["installed"] is False
    assert data["running"] is False
    assert data["reachable"] is False
    # Stable shape — every key in MatrixLabStatus is present
    for key in ("status", "installed", "running", "reachable", "lifecycleEnabled",
                "dockerAvailable", "activeSandbox", "runnerUrl", "message"):
        assert key in data


def test_status_endpoint_ready_when_runner_healthy(client, monkeypatch) -> None:
    _stub_docker(monkeypatch, available=True, installed=True, running=True)
    _stub_health(monkeypatch, reachable=True, version="1.2.3")

    r = client.get("/api/matrixlab/status")
    data = r.json()
    assert data["status"] == "ready"
    assert data["version"] == "1.2.3"
    assert data["errorCode"] is None


def test_status_endpoint_translates_invalid_health_response(client, monkeypatch) -> None:
    """The exact failure mode from the user's screenshot: running but
    the health endpoint returned empty/non-JSON. UI must get a clean
    classified error, never the raw 'Expecting value' string."""
    _stub_docker(monkeypatch, available=True, installed=True, running=True)
    _stub_health(
        monkeypatch,
        reachable=False,
        raw_error="Expecting value: line 1 column 1 (char 0)",
    )

    r = client.get("/api/matrixlab/status")
    data = r.json()
    assert data["status"] == "needs_attention"
    assert data["errorCode"] == "INVALID_HEALTH_RESPONSE"
    # User-facing message is friendly
    assert "Expecting value" not in data["message"]
    # Raw error preserved for the disclosure
    assert data["technicalDetails"]["rawError"] == "Expecting value: line 1 column 1 (char 0)"


def test_install_returns_failed_status_when_lifecycle_disabled(client, monkeypatch) -> None:
    """Lifecycle off (typical default) — install must NOT raise HTTP
    403. It must return a normalised failed-status JSON so the modal
    can render the right copy + recovery action."""
    _stub_docker(monkeypatch, available=True, installed=False, running=False)
    _stub_health(monkeypatch, reachable=False)
    monkeypatch.delenv("GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE", raising=False)

    r = client.post("/api/matrixlab/install")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "failed"
    assert data["errorCode"] == "LIFECYCLE_DISABLED"
    # Friendly copy
    assert "automatic" in data["message"].lower() or "disabled" in data["message"].lower()
    # Admin hint in technical details
    assert data["technicalDetails"] is not None


def test_install_returns_failed_status_when_docker_missing(client, monkeypatch) -> None:
    monkeypatch.setenv("GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE", "1")
    _stub_docker(monkeypatch, available=False, installed=False, running=False)
    _stub_health(monkeypatch, reachable=False)

    r = client.post("/api/matrixlab/install")
    data = r.json()
    assert data["status"] == "failed"
    assert data["errorCode"] == "DOCKER_NOT_INSTALLED"


def test_test_endpoint_reprobes_and_returns_status(client, monkeypatch) -> None:
    """The modal's 'Retry connection' button calls /test — must return
    a fresh status without performing any docker operations."""
    _stub_docker(monkeypatch, available=True, installed=True, running=True)
    _stub_health(monkeypatch, reachable=True, version="0.1.7")

    r = client.post("/api/matrixlab/test")
    data = r.json()
    assert data["status"] == "ready"
    assert data["version"] == "0.1.7"


def test_activate_refuses_when_not_ready(client, monkeypatch) -> None:
    """Don't let the user point the Run button at a dead backend by
    clicking Activate before MatrixLab is healthy."""
    _stub_docker(monkeypatch, available=True, installed=False, running=False)
    _stub_health(monkeypatch, reachable=False)

    # Snapshot the current backend before activation attempt
    before = client.get("/api/sandbox/status").json()["backend"]

    r = client.post("/api/matrixlab/activate")
    data = r.json()
    assert data["status"] != "ready"
    assert "not ready" in data["message"].lower()

    # And the backend wasn't flipped
    after = client.get("/api/sandbox/status").json()["backend"]
    assert after == before


def test_activate_sets_backend_when_ready(client, monkeypatch) -> None:
    """Successful flow: install + start + health-ok → activate flips
    the sandbox backend so the Run button uses MatrixLab from now on."""
    _stub_docker(monkeypatch, available=True, installed=True, running=True)
    _stub_health(monkeypatch, reachable=True, version="1.0.0")

    # Pre-condition — default is subprocess
    assert client.get("/api/sandbox/status").json()["backend"] == "subprocess"

    r = client.post("/api/matrixlab/activate")
    data = r.json()
    assert data["status"] == "ready"
    assert data["activeSandbox"] == "matrixlab"

    # /api/sandbox/status now reflects the flip
    assert client.get("/api/sandbox/status").json()["backend"] == "matrixlab"


def test_repair_returns_failed_status_when_lifecycle_disabled(client, monkeypatch) -> None:
    """The Repair button must NOT raise HTTP 403 when lifecycle is off —
    it has to return a normalised failed status so the modal can render
    a recovery action and copy."""
    monkeypatch.delenv("GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE", raising=False)

    r = client.post("/api/matrixlab/repair")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "failed"
    assert data["errorCode"] == "LIFECYCLE_DISABLED"


def test_repair_message_does_not_use_running_but_unreachable_phrasing(monkeypatch) -> None:
    """UX invariant: the headline status message must use the cleaner
    'installed, but GitPilot cannot connect to the runner' phrasing —
    NOT the older 'running, but GitPilot cannot connect to it at
    http://… contradictory copy."""
    from gitpilot.matrixlab_admin_api import derive_status
    from gitpilot.sandbox_api import MatrixLabLifecycleResponse

    lifecycle = MatrixLabLifecycleResponse(
        docker_available=True, installed=True, running=True,
        lifecycle_enabled=True,
        runner_image="ruslanmv/matrixlab-runner:latest",
        sandbox_images=[], container_name="x",
        matrixlab_url="http://localhost:8765",
        steps=[],
    )
    s = derive_status(
        lifecycle=lifecycle, reachable=False,
        health_error="connection refused",
        active_sandbox="local",
    )
    assert "installed" in s.message.lower()
    assert "cannot connect" in s.message.lower()
    # Older buggy copy must not survive a regression.
    assert "running, but" not in s.message.lower()


def test_reinstall_returns_failed_status_when_lifecycle_disabled(client, monkeypatch) -> None:
    monkeypatch.delenv("GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE", raising=False)

    r = client.post("/api/matrixlab/reinstall", json={"remove_data": False})
    data = r.json()
    assert data["status"] == "failed"
    assert data["errorCode"] == "LIFECYCLE_DISABLED"


def test_reinstall_accepts_remove_data_flag(client, monkeypatch) -> None:
    """Wiping cached data is opt-in; reinstall must still parse the
    request body even when set to True."""
    monkeypatch.delenv("GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE", raising=False)

    # Even with remove_data=True the request shape must be accepted
    # (lifecycle gate fires first; the test is about request schema).
    r = client.post("/api/matrixlab/reinstall", json={"remove_data": True})
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert "errorCode" in data


def test_reinstall_empty_body_uses_default(client, monkeypatch) -> None:
    """Posting with no body should not 422 — remove_data defaults to False."""
    monkeypatch.delenv("GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE", raising=False)

    r = client.post("/api/matrixlab/reinstall")
    assert r.status_code == 200, r.text


def test_logs_endpoint_reports_docker_missing(client, monkeypatch) -> None:
    monkeypatch.setattr("gitpilot.matrixlab_admin_api._docker_available", lambda: False)

    r = client.get("/api/matrixlab/logs")
    data = r.json()
    assert data["ok"] is False
    assert data["errorCode"] == "DOCKER_NOT_INSTALLED"


# ---------------------------------------------------------------------
# Cross-state invariants — properties every status must satisfy
# ---------------------------------------------------------------------

def test_no_response_can_be_both_running_and_unreachable_without_error_code() -> None:
    """The 'Unreachable + Running' contradiction shown in the original
    UI is exactly what derive_status must prevent: whenever
    running=True AND reachable=False, there has to be an errorCode."""
    s = derive_status(
        lifecycle=_lifecycle(installed=True, running=True),
        reachable=False,
        health_error="connection refused",
        active_sandbox="local",
    )
    assert s.running is True
    assert s.reachable is False
    assert s.errorCode is not None and s.errorCode != ""


def test_status_field_only_takes_documented_values() -> None:
    """Guards the state-machine contract the frontend depends on."""
    allowed = {
        "not_installed", "installing", "starting", "stopping",
        "checking", "ready", "needs_attention", "failed",
    }
    for lc, reach, err in [
        (_lifecycle(installed=False, running=False), False, None),
        (_lifecycle(installed=True, running=True), True, None),
        (_lifecycle(installed=True, running=True), False, "boom"),
        (_lifecycle(installed=True, running=False), False, None),
        (_lifecycle(docker_available=False), False, None),
    ]:
        s = derive_status(
            lifecycle=lc, reachable=reach,
            health_error=err, active_sandbox="subprocess",
        )
        assert s.status in allowed, f"unexpected status {s.status!r}"


def test_default_sandbox_images_use_dockerhub_namespace() -> None:
    """Regression: ``docker pull matrix-lab-sandbox-python:latest`` would
    404 because the image is published as ``ruslanmv/matrix-lab-sandbox-python``.
    Every default image must carry the namespace prefix that matches
    the matrixlab Makefile (and the docker-publish workflow that pushes
    them)."""
    from gitpilot.sandbox_api import DEFAULT_SANDBOX_IMAGES

    assert DEFAULT_SANDBOX_IMAGES, "expected at least one sandbox image"
    for ref in DEFAULT_SANDBOX_IMAGES:
        assert "/" in ref, f"image {ref!r} lacks a registry namespace"
        # Tag present
        assert ":" in ref.split("/", 1)[1], f"image {ref!r} lacks a tag"


# ---------------------------------------------------------------------
# Native install path (Advanced) — pidfile + status synthesis
# ---------------------------------------------------------------------

def test_native_status_reports_not_installed_for_empty_dir(tmp_path, monkeypatch) -> None:
    """Fresh host, nothing cloned: native status reports installed=False."""
    monkeypatch.setattr(mla, "MATRIXLAB_LOCAL_DIR", tmp_path / "ml")
    monkeypatch.setattr(mla, "MATRIXLAB_PID_FILE", tmp_path / "ml" / ".pid")

    import asyncio
    info = asyncio.get_event_loop().run_until_complete(mla._native_status())
    assert info["installed"] is False
    assert info["running"] is False
    assert info["repo_present"] is False
    assert info["venv_present"] is False


def test_native_status_detects_full_layout(tmp_path, monkeypatch) -> None:
    """Cloned repo + venv + runner/app/main.py present → installed=True."""
    ml = tmp_path / "ml"
    (ml / ".git").mkdir(parents=True)
    (ml / ".venv").mkdir()
    (ml / "runner" / "app").mkdir(parents=True)
    (ml / "runner" / "app" / "main.py").write_text("# stub")
    monkeypatch.setattr(mla, "MATRIXLAB_LOCAL_DIR", ml)
    monkeypatch.setattr(mla, "MATRIXLAB_PID_FILE", ml / ".pid")

    import asyncio
    info = asyncio.get_event_loop().run_until_complete(mla._native_status())
    assert info["installed"] is True
    # No pid yet so still considered not running.
    assert info["running"] is False


def test_install_local_returns_failed_when_lifecycle_disabled(client, monkeypatch) -> None:
    monkeypatch.delenv("GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE", raising=False)
    r = client.post("/api/matrixlab/install_local")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "failed"
    assert data["errorCode"] == "LIFECYCLE_DISABLED"


def test_install_local_returns_failed_when_git_missing(client, monkeypatch) -> None:
    monkeypatch.setenv("GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE", "1")
    # Pretend git is not on PATH.
    import shutil as _shutil
    real_which = _shutil.which
    monkeypatch.setattr("shutil.which", lambda name: None if name == "git" else real_which(name))

    r = client.post("/api/matrixlab/install_local")
    data = r.json()
    assert data["status"] == "failed"
    assert data["errorCode"] == "GIT_NOT_INSTALLED"


def test_message_never_contains_python_traceback_markers() -> None:
    """User-facing copy hygiene: nothing that looks like a stack trace
    should ever bleed through to the headline message."""
    for hostile in (
        "Traceback (most recent call last)",
        "json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)",
        "<class 'httpx.ConnectError'>",
        "ConnectionError(MaxRetryError(...))",
    ):
        s = derive_status(
            lifecycle=_lifecycle(installed=True, running=True),
            reachable=False,
            health_error=hostile,
            active_sandbox="local",
        )
        for marker in ("Traceback", "JSONDecodeError", "<class", "MaxRetryError"):
            assert marker not in s.message
