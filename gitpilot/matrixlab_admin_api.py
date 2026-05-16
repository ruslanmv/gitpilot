"""Enterprise install/lifecycle surface for the MatrixLab addon.

Sits on top of :mod:`gitpilot.sandbox_api` and normalises every response
into the single :class:`MatrixLabStatus` shape the install modal renders.
The goal is to keep raw Docker / HTTP failures out of the UI: every
error gets classified into a stable ``errorCode`` + a human ``message``,
with the verbatim trace surfaced only under ``technicalDetails`` for
operators who open the disclosure.

Endpoints under ``/api/matrixlab/*``:

* ``GET    /status``    — current lifecycle + health, normalised
* ``POST   /install``   — idempotent: pull images then start runner
* ``POST   /start``     — start (or no-op if running)
* ``POST   /stop``      — stop the runner
* ``POST   /restart``   — stop+start sequence
* ``POST   /test``      — re-probe /health, return normalised status
* ``POST   /activate``  — set sandbox backend = matrixlab once ready
* ``GET    /logs``      — tail of the lifecycle transcript

All of these are additive — :mod:`sandbox_api` continues to back the
existing Run button and the legacy Settings modal.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .sandbox_api import (
    DEFAULT_RUNNER_IMAGE,
    ENV_LIFECYCLE,
    MatrixLabLifecycleResponse,
    SandboxConfigUpdate,
    _docker_available,
    _gather_lifecycle_status,
    _lifecycle_enabled,
    _matrixlab_running,
    _run_shell,
    api_matrixlab_install as _legacy_install,
    api_matrixlab_start as _legacy_start,
    api_matrixlab_stop as _legacy_stop,
    api_sandbox_config as _legacy_config,
)
from .settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/matrixlab", tags=["matrixlab"])


# ----------------------------------------------------------------------
# Normalised response shape
# ----------------------------------------------------------------------

StatusName = Literal[
    "not_installed",
    "installing",
    "starting",
    "stopping",
    "checking",
    "ready",
    "needs_attention",
    "failed",
]


class TechnicalDetails(BaseModel):
    """Verbatim trace for operators; hidden behind a disclosure in the UI."""

    expected: Optional[str] = None
    actual: Optional[str] = None
    rawError: Optional[str] = None
    rawCommand: Optional[str] = None
    rawStdout: Optional[str] = None
    rawStderr: Optional[str] = None


class MatrixLabStatus(BaseModel):
    """Single source of truth the install modal binds to."""

    status: StatusName
    installed: bool
    running: bool
    reachable: bool
    lifecycleEnabled: bool
    dockerAvailable: bool
    activeSandbox: str
    runnerUrl: str
    message: str
    errorCode: Optional[str] = None
    technicalDetails: Optional[TechnicalDetails] = None
    runnerImage: str = DEFAULT_RUNNER_IMAGE
    version: Optional[str] = None
    # Last lifecycle step transcript — populated by install/start/stop
    # so the UI's "Open logs" affordance has something to show without
    # a second round trip.
    steps: List[Dict[str, Any]] = Field(default_factory=list)


# ----------------------------------------------------------------------
# Pure helpers — kept side-effect free so they're trivially unit-testable
# ----------------------------------------------------------------------

# Order matters: more specific phrases first.  Each entry is
# (substring-to-match, errorCode, user-facing message).  Match is
# case-insensitive on a lowercased raw_error string.
_ERROR_CLASSIFIERS: List[tuple[str, str, str]] = [
    (
        "expecting value",
        "INVALID_HEALTH_RESPONSE",
        "The runner responded, but not with the expected health response.",
    ),
    (
        "json",
        "INVALID_HEALTH_RESPONSE",
        "The runner responded, but not with the expected health response.",
    ),
    (
        "connection refused",
        "CONNECTION_REFUSED",
        "GitPilot could not connect to the MatrixLab runner.",
    ),
    (
        "refused",
        "CONNECTION_REFUSED",
        "GitPilot could not connect to the MatrixLab runner.",
    ),
    (
        "timed out",
        "TIMEOUT",
        "MatrixLab took too long to respond.",
    ),
    (
        "timeout",
        "TIMEOUT",
        "MatrixLab took too long to respond.",
    ),
    (
        "name or service not known",
        "DNS_ERROR",
        "The runner hostname could not be resolved.",
    ),
    (
        "cannot connect to the docker daemon",
        "DOCKER_NOT_RUNNING",
        "Docker is not running. Start Docker and try again.",
    ),
    (
        "docker daemon",
        "DOCKER_NOT_RUNNING",
        "Docker is not running. Start Docker and try again.",
    ),
    (
        "port is already allocated",
        "PORT_IN_USE",
        "The MatrixLab port is already in use by another service.",
    ),
    (
        "address already in use",
        "PORT_IN_USE",
        "The MatrixLab port is already in use by another service.",
    ),
    (
        "permission denied",
        "PERMISSION_DENIED",
        "GitPilot does not have permission to start MatrixLab.",
    ),
    (
        "no such image",
        "IMAGE_MISSING",
        "The MatrixLab runner image is missing. Try installing again.",
    ),
    (
        "not on path",
        "DOCKER_NOT_INSTALLED",
        "Docker is not installed on the GitPilot host.",
    ),
]


def classify_error(raw_error: Optional[str]) -> tuple[str, str]:
    """Map a raw error string into ``(errorCode, humanMessage)``.

    Pure. Never raises. Returns ``("", "")`` when ``raw_error`` is empty
    so callers can use truthiness to detect "no classifiable error".
    """
    if not raw_error:
        return ("", "")
    needle = raw_error.lower()
    for substring, code, message in _ERROR_CLASSIFIERS:
        if substring in needle:
            return (code, message)
    return ("UNKNOWN", "MatrixLab encountered an unexpected error.")


def derive_status(
    *,
    lifecycle: MatrixLabLifecycleResponse,
    reachable: bool,
    health_error: Optional[str],
    active_sandbox: str,
    in_flight: Optional[StatusName] = None,
) -> MatrixLabStatus:
    """Combine lifecycle + connection probe into a single ``MatrixLabStatus``.

    Pure. ``in_flight`` lets POST handlers report ``installing`` /
    ``starting`` / ``stopping`` while a long-running shell-out is mid
    way — GET /status never sets it.
    """
    docker_ok = lifecycle.docker_available
    installed = lifecycle.installed
    running = lifecycle.running

    if in_flight is not None:
        return MatrixLabStatus(
            status=in_flight,
            installed=installed,
            running=running,
            reachable=reachable,
            lifecycleEnabled=lifecycle.lifecycle_enabled,
            dockerAvailable=docker_ok,
            activeSandbox=active_sandbox,
            runnerUrl=lifecycle.matrixlab_url,
            runnerImage=lifecycle.runner_image,
            message=_in_flight_message(in_flight),
        )

    # Terminal states resolved from the lifecycle facts.
    if not docker_ok:
        code, msg = classify_error("docker not on path")
        return MatrixLabStatus(
            status="needs_attention",
            installed=installed, running=running, reachable=reachable,
            lifecycleEnabled=lifecycle.lifecycle_enabled,
            dockerAvailable=False,
            activeSandbox=active_sandbox,
            runnerUrl=lifecycle.matrixlab_url,
            runnerImage=lifecycle.runner_image,
            message=msg,
            errorCode=code,
        )

    if not installed and not running:
        return MatrixLabStatus(
            status="not_installed",
            installed=False, running=False, reachable=False,
            lifecycleEnabled=lifecycle.lifecycle_enabled,
            dockerAvailable=docker_ok,
            activeSandbox=active_sandbox,
            runnerUrl=lifecycle.matrixlab_url,
            runnerImage=lifecycle.runner_image,
            message="MatrixLab is not installed yet.",
        )

    if running and reachable:
        return MatrixLabStatus(
            status="ready",
            installed=True, running=True, reachable=True,
            lifecycleEnabled=lifecycle.lifecycle_enabled,
            dockerAvailable=docker_ok,
            activeSandbox=active_sandbox,
            runnerUrl=lifecycle.matrixlab_url,
            runnerImage=lifecycle.runner_image,
            message="MatrixLab is ready.",
        )

    # running but not reachable — runner is alive but /health failed
    if running and not reachable:
        code, msg = classify_error(health_error or "connection refused")
        details = TechnicalDetails(
            expected="JSON health response from /health",
            actual="empty or invalid response" if code == "INVALID_HEALTH_RESPONSE"
                   else "no response",
            rawError=health_error or None,
        )
        return MatrixLabStatus(
            status="needs_attention",
            installed=True, running=True, reachable=False,
            lifecycleEnabled=lifecycle.lifecycle_enabled,
            dockerAvailable=docker_ok,
            activeSandbox=active_sandbox,
            runnerUrl=lifecycle.matrixlab_url,
            runnerImage=lifecycle.runner_image,
            message=(
                f"MatrixLab is running, but GitPilot cannot connect to it at "
                f"{lifecycle.matrixlab_url}."
            ),
            errorCode=code or "CONNECTION_REFUSED",
            technicalDetails=details,
        )

    # installed but not running — typical post-Stop or crash state
    return MatrixLabStatus(
        status="needs_attention",
        installed=True, running=False, reachable=False,
        lifecycleEnabled=lifecycle.lifecycle_enabled,
        dockerAvailable=docker_ok,
        activeSandbox=active_sandbox,
        runnerUrl=lifecycle.matrixlab_url,
        runnerImage=lifecycle.runner_image,
        message="MatrixLab is installed but not running.",
        errorCode="STOPPED",
    )


def _in_flight_message(state: StatusName) -> str:
    return {
        "installing": "Installing MatrixLab…",
        "starting": "Starting MatrixLab…",
        "stopping": "Stopping MatrixLab…",
        "checking": "Checking connection…",
    }.get(state, "Working…")


async def _probe_health() -> tuple[bool, Optional[str], Optional[str]]:
    """Return ``(reachable, raw_error, version)`` for the configured runner.

    Distinct from :func:`sandbox_api._matrixlab_running` because we
    need the error text (to classify) and the version (to surface)
    rather than a yes/no.
    """
    cfg = get_settings().sandbox
    base = (cfg.matrixlab_url or "http://localhost:8000").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base}/health")
            if resp.status_code != 200:
                return (False, f"HTTP {resp.status_code}: {resp.text[:200]}", None)
            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001 - we want the raw text
                return (False, str(exc), None)
            return (True, None, str(data.get("version")) if data.get("version") else None)
    except httpx.HTTPError as exc:
        return (False, str(exc), None)


def _steps_to_dicts(lifecycle: MatrixLabLifecycleResponse) -> List[Dict[str, Any]]:
    """Pull the lifecycle step transcript out as plain dicts for the UI."""
    out: List[Dict[str, Any]] = []
    for step in lifecycle.steps or []:
        out.append({
            "cmd": step.cmd,
            "exitCode": step.exit_code,
            "stdout": step.stdout,
            "stderr": step.stderr,
            "durationMs": step.duration_ms,
        })
    return out


def _attach_steps(status: MatrixLabStatus, lifecycle: MatrixLabLifecycleResponse) -> MatrixLabStatus:
    """Return a copy of ``status`` with the lifecycle transcript attached."""
    return status.model_copy(update={"steps": _steps_to_dicts(lifecycle)})


async def _current_status() -> MatrixLabStatus:
    """Build a fresh :class:`MatrixLabStatus` from the live world."""
    lifecycle = await _gather_lifecycle_status()
    reachable, raw_error, version = await _probe_health()
    active_sandbox = (get_settings().sandbox.backend or "subprocess").lower()
    status = derive_status(
        lifecycle=lifecycle,
        reachable=reachable,
        health_error=raw_error,
        active_sandbox=active_sandbox,
    )
    if version and status.status == "ready":
        status = status.model_copy(update={"version": version})
    return status


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------

@router.get("/status", response_model=MatrixLabStatus)
async def matrixlab_status() -> MatrixLabStatus:
    """Single GET the install modal polls for state changes."""
    return await _current_status()


@router.post("/install", response_model=MatrixLabStatus)
async def matrixlab_install() -> MatrixLabStatus:
    """Idempotent install: pulls images then starts the runner.

    Safe to call repeatedly — running container is left alone, missing
    images are pulled, stopped containers are started.  All errors come
    back as a normalised :class:`MatrixLabStatus` rather than HTTP 4xx
    so the modal can render the right recovery action.
    """
    if not _lifecycle_enabled():
        return MatrixLabStatus(
            status="failed",
            installed=False, running=False, reachable=False,
            lifecycleEnabled=False,
            dockerAvailable=_docker_available(),
            activeSandbox=(get_settings().sandbox.backend or "subprocess").lower(),
            runnerUrl=get_settings().sandbox.matrixlab_url,
            message=(
                "Automatic installation is disabled on this GitPilot backend. "
                "Ask your administrator to enable MatrixLab lifecycle "
                "automation, or use manual setup."
            ),
            errorCode="LIFECYCLE_DISABLED",
            technicalDetails=TechnicalDetails(
                rawError=f"Set {ENV_LIFECYCLE}=1 on the GitPilot backend and restart it.",
            ),
        )

    if not _docker_available():
        return MatrixLabStatus(
            status="failed",
            installed=False, running=False, reachable=False,
            lifecycleEnabled=True,
            dockerAvailable=False,
            activeSandbox=(get_settings().sandbox.backend or "subprocess").lower(),
            runnerUrl=get_settings().sandbox.matrixlab_url,
            message="Docker is not installed on the GitPilot host.",
            errorCode="DOCKER_NOT_INSTALLED",
        )

    # Re-use the existing lifecycle handlers so we don't duplicate the
    # docker pull / docker run logic.  They already return the step
    # transcript; we just translate the final state.
    try:
        pull_lifecycle = await _legacy_install()
    except HTTPException as exc:
        return MatrixLabStatus(
            status="failed",
            installed=False, running=False, reachable=False,
            lifecycleEnabled=_lifecycle_enabled(),
            dockerAvailable=_docker_available(),
            activeSandbox=(get_settings().sandbox.backend or "subprocess").lower(),
            runnerUrl=get_settings().sandbox.matrixlab_url,
            message="MatrixLab could not be installed automatically.",
            errorCode="INSTALL_FAILED",
            technicalDetails=TechnicalDetails(rawError=str(exc.detail)),
        )

    if not pull_lifecycle.running:
        try:
            start_lifecycle = await _legacy_start()
        except HTTPException as exc:
            failure = derive_status(
                lifecycle=pull_lifecycle,
                reachable=False,
                health_error=str(exc.detail),
                active_sandbox=(get_settings().sandbox.backend or "subprocess").lower(),
            )
            return _attach_steps(
                failure.model_copy(update={
                    "status": "failed",
                    "errorCode": "START_FAILED",
                    "message": "MatrixLab installed but failed to start.",
                    "technicalDetails": TechnicalDetails(rawError=str(exc.detail)),
                }),
                pull_lifecycle,
            )
        merged_steps = list(pull_lifecycle.steps) + list(start_lifecycle.steps)
        pull_lifecycle = pull_lifecycle.model_copy(update={
            "installed": start_lifecycle.installed,
            "running": start_lifecycle.running,
            "steps": merged_steps,
        })

    reachable, raw_error, version = await _probe_health()
    status = derive_status(
        lifecycle=pull_lifecycle,
        reachable=reachable,
        health_error=raw_error,
        active_sandbox=(get_settings().sandbox.backend or "subprocess").lower(),
    )
    if version and status.status == "ready":
        status = status.model_copy(update={"version": version})
    return _attach_steps(status, pull_lifecycle)


@router.post("/start", response_model=MatrixLabStatus)
async def matrixlab_start() -> MatrixLabStatus:
    if not _lifecycle_enabled():
        status = await _current_status()
        return status.model_copy(update={
            "status": "failed",
            "errorCode": "LIFECYCLE_DISABLED",
            "message": "Automatic start is disabled on this GitPilot backend.",
        })
    try:
        lifecycle = await _legacy_start()
    except HTTPException as exc:
        status = await _current_status()
        return status.model_copy(update={
            "status": "failed",
            "errorCode": "START_FAILED",
            "message": "MatrixLab failed to start.",
            "technicalDetails": TechnicalDetails(rawError=str(exc.detail)),
        })

    reachable, raw_error, version = await _probe_health()
    status = derive_status(
        lifecycle=lifecycle,
        reachable=reachable,
        health_error=raw_error,
        active_sandbox=(get_settings().sandbox.backend or "subprocess").lower(),
    )
    if version and status.status == "ready":
        status = status.model_copy(update={"version": version})
    return _attach_steps(status, lifecycle)


@router.post("/stop", response_model=MatrixLabStatus)
async def matrixlab_stop() -> MatrixLabStatus:
    if not _lifecycle_enabled():
        status = await _current_status()
        return status.model_copy(update={
            "status": "failed",
            "errorCode": "LIFECYCLE_DISABLED",
            "message": "Automatic stop is disabled on this GitPilot backend.",
        })
    try:
        lifecycle = await _legacy_stop()
    except HTTPException as exc:
        status = await _current_status()
        return status.model_copy(update={
            "status": "failed",
            "errorCode": "STOP_FAILED",
            "message": "MatrixLab failed to stop.",
            "technicalDetails": TechnicalDetails(rawError=str(exc.detail)),
        })
    status = derive_status(
        lifecycle=lifecycle,
        reachable=False,
        health_error=None,
        active_sandbox=(get_settings().sandbox.backend or "subprocess").lower(),
    )
    return _attach_steps(status, lifecycle)


@router.post("/restart", response_model=MatrixLabStatus)
async def matrixlab_restart() -> MatrixLabStatus:
    """Stop then start. Lifecycle-gated like the underlying actions."""
    await matrixlab_stop()
    return await matrixlab_start()


@router.post("/test", response_model=MatrixLabStatus)
async def matrixlab_test() -> MatrixLabStatus:
    """Re-probe /health and return the normalised status.

    This is what the modal's ``Retry connection`` button drives.
    """
    return await _current_status()


@router.post("/activate", response_model=MatrixLabStatus)
async def matrixlab_activate() -> MatrixLabStatus:
    """Flip the active sandbox to matrixlab after a successful install.

    Only flips when MatrixLab is reachable, so users can't accidentally
    point the Run button at a dead backend by clicking activate too early.
    """
    status = await _current_status()
    if status.status != "ready":
        return status.model_copy(update={
            "message": "MatrixLab is not ready yet — connection check failed.",
        })
    try:
        await _legacy_config(SandboxConfigUpdate(backend="matrixlab"))
    except HTTPException as exc:
        return status.model_copy(update={
            "status": "needs_attention",
            "errorCode": "ACTIVATE_FAILED",
            "message": "MatrixLab is ready, but could not be set as the active sandbox.",
            "technicalDetails": TechnicalDetails(rawError=str(exc.detail)),
        })
    return status.model_copy(update={"activeSandbox": "matrixlab"})


@router.get("/logs")
async def matrixlab_logs(tail: int = 200) -> Dict[str, Any]:
    """Tail the gitpilot-matrixlab container log.

    Returns a structured payload so the modal can show stdout / stderr
    distinctly, with a clear ``error`` when docker isn't available.
    """
    from .sandbox_api import DEFAULT_CONTAINER_NAME

    if not _docker_available():
        return {
            "ok": False,
            "error": "Docker is not available.",
            "errorCode": "DOCKER_NOT_INSTALLED",
            "lines": [],
        }
    step = await _run_shell(
        ["docker", "logs", "--tail", str(max(1, int(tail))), DEFAULT_CONTAINER_NAME],
        timeout=15,
    )
    if step.exit_code != 0:
        return {
            "ok": False,
            "error": "Could not read MatrixLab logs.",
            "errorCode": "LOGS_UNAVAILABLE",
            "lines": (step.stderr or step.stdout or "").splitlines(),
        }
    combined = (step.stdout or "") + (step.stderr or "")
    return {"ok": True, "lines": combined.splitlines()}
