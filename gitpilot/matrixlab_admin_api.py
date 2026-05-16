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
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .sandbox_api import (
    DEFAULT_CONTAINER_NAME,
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
            # "Installed but cannot connect" reads as a single coherent
            # state in the install modal.  The runner URL itself is
            # promoted to ``technicalDetails`` rather than embedded in
            # the headline message — see derive_status caller.
            message="MatrixLab is installed, but GitPilot cannot connect to the runner.",
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
                "MatrixLab lifecycle automation is disabled. This GitPilot "
                "backend was started with GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE=0. "
                "Restart it without that variable (the default is enabled), or "
                "use manual setup."
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

    Drives the modal's ``Repair connection`` / one-off test affordances.
    """
    return await _current_status()


# ----------------------------------------------------------------------
# Repair and reinstall — recovery actions the install modal surfaces.
# ----------------------------------------------------------------------

@router.post("/repair", response_model=MatrixLabStatus)
async def matrixlab_repair() -> MatrixLabStatus:
    """Repair an unreachable MatrixLab install.

    Strategy: restart the runner container (``docker stop`` + ``docker
    start``), re-probe ``/health``, and re-activate if it recovers.
    Idempotent — safe to call from any non-``ready`` state.  Won't pull
    images or re-create the container; for that use ``/reinstall``.
    """
    if not _lifecycle_enabled():
        status = await _current_status()
        return status.model_copy(update={
            "status": "failed",
            "errorCode": "LIFECYCLE_DISABLED",
            "message": "Automatic repair is disabled on this GitPilot backend.",
        })

    # 1. Best-effort stop (no-op when already stopped).
    try:
        await _legacy_stop()
    except HTTPException:
        pass  # we'll try to start it anyway

    # 2. Start (also no-op when already running).
    try:
        await _legacy_start()
    except HTTPException as exc:
        status = await _current_status()
        return status.model_copy(update={
            "status": "failed",
            "errorCode": "REPAIR_FAILED",
            "message": "MatrixLab could not be restarted.",
            "technicalDetails": TechnicalDetails(rawError=str(exc.detail)),
        })

    # 3. Re-test and (if healthy) re-activate.
    status = await _current_status()
    if status.status == "ready":
        try:
            await _legacy_config(SandboxConfigUpdate(backend="matrixlab"))
            status = status.model_copy(update={"activeSandbox": "matrixlab"})
        except HTTPException:
            # Activation is non-fatal — surface the healthy status.
            pass
    return status


class _ReinstallRequest(BaseModel):
    """Toggles for :func:`matrixlab_reinstall`.

    ``remove_data`` is opt-in, off by default, so a misclick can't wipe
    the operator's docker images or job workspaces.  Reinstall itself
    is non-destructive otherwise.
    """
    remove_data: bool = False


@router.post("/reinstall", response_model=MatrixLabStatus)
async def matrixlab_reinstall(req: _ReinstallRequest | None = None) -> MatrixLabStatus:
    """Clean reinstall: stop → remove container → pull → start → test → activate.

    Idempotent — every step tolerates the "already done" case so the
    operator can click Reinstall again after a partial failure.

    When ``remove_data=True``, also removes the cached MatrixLab runner
    image so the next install pulls fresh.  Sandbox images and any data
    written to ``GITPILOT_MATRIXLAB_JOBS_DIR`` are preserved either way.
    """
    options = req or _ReinstallRequest()

    if not _lifecycle_enabled():
        status = await _current_status()
        return status.model_copy(update={
            "status": "failed",
            "errorCode": "LIFECYCLE_DISABLED",
            "message": "Automatic reinstall is disabled on this GitPilot backend.",
        })
    if not _docker_available():
        status = await _current_status()
        return status.model_copy(update={
            "status": "failed",
            "errorCode": "DOCKER_NOT_INSTALLED",
            "message": "Docker is not installed on the GitPilot host.",
        })

    steps: List[Dict[str, Any]] = []

    # 1. Stop (no-op if already stopped) and remove the container so
    #    `install` re-creates it with current env vars / mounts.
    stop_step = await _run_shell(["docker", "stop", DEFAULT_CONTAINER_NAME], timeout=30)
    steps.append({
        "cmd": stop_step.cmd, "exitCode": stop_step.exit_code,
        "stdout": stop_step.stdout, "stderr": stop_step.stderr,
        "durationMs": stop_step.duration_ms,
    })
    rm_step = await _run_shell(["docker", "rm", "-f", DEFAULT_CONTAINER_NAME], timeout=30)
    steps.append({
        "cmd": rm_step.cmd, "exitCode": rm_step.exit_code,
        "stdout": rm_step.stdout, "stderr": rm_step.stderr,
        "durationMs": rm_step.duration_ms,
    })

    # 2. Optionally drop the runner image so the next pull is fresh.
    if options.remove_data:
        rmi_step = await _run_shell(
            ["docker", "rmi", "-f", DEFAULT_RUNNER_IMAGE], timeout=60,
        )
        steps.append({
            "cmd": rmi_step.cmd, "exitCode": rmi_step.exit_code,
            "stdout": rmi_step.stdout, "stderr": rmi_step.stderr,
            "durationMs": rmi_step.duration_ms,
        })

    # 3. Re-run the full install (pull + start), bubbling its step list.
    installed = await matrixlab_install()
    merged_steps = steps + list(installed.steps or [])

    # 4. If install reached "ready", flip the active sandbox; otherwise
    #    surface the partial state so the modal can show recovery copy.
    if installed.status == "ready":
        try:
            await _legacy_config(SandboxConfigUpdate(backend="matrixlab"))
            installed = installed.model_copy(update={"activeSandbox": "matrixlab"})
        except HTTPException:
            pass
    return installed.model_copy(update={"steps": merged_steps})


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


# ----------------------------------------------------------------------
# Native install path (Advanced) — git clone + venv + runner
# ----------------------------------------------------------------------
#
# Operators who can't (or don't want to) run Docker on the GitPilot host
# can opt into a host-process install: clone the matrixlab repo, build
# a dedicated venv, install the runner's requirements, and start
# ``uvicorn app.main:app`` from ``runner/``.  Same lifecycle gate as
# the docker path so a web request can't shell out unless the operator
# explicitly enabled it.
#
# Note: the runner still spawns per-language *sandboxes* via Docker,
# so the host needs ``docker`` on PATH even with the native install —
# this option only avoids running the Runner itself inside a container.

MATRIXLAB_GIT_URL = os.environ.get(
    "GITPILOT_MATRIXLAB_GIT_URL",
    "https://github.com/agent-matrix/matrixlab.git",
)
MATRIXLAB_LOCAL_DIR = Path(os.environ.get(
    "GITPILOT_MATRIXLAB_LOCAL_DIR",
    str(Path.home() / ".gitpilot" / "matrixlab"),
))
MATRIXLAB_PID_FILE = MATRIXLAB_LOCAL_DIR / ".gitpilot-runner.pid"
MATRIXLAB_LOG_FILE = MATRIXLAB_LOCAL_DIR / ".gitpilot-runner.log"


def _venv_python(venv: Path) -> Path:
    """Return the python interpreter path inside ``venv`` (cross-platform)."""
    candidates = [
        venv / "bin" / "python",
        venv / "bin" / "python3",
        venv / "Scripts" / "python.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Fall back to the unix layout even if missing — the caller will
    # see the error from the subsequent shell-out.
    return venv / "bin" / "python"


def _read_pid() -> Optional[int]:
    try:
        return int(MATRIXLAB_PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _native_running() -> bool:
    pid = _read_pid()
    return pid is not None and _pid_alive(pid)


async def _native_status() -> Dict[str, Any]:
    """Synthesise a lifecycle-shaped dict for the native install path."""
    repo_present = (MATRIXLAB_LOCAL_DIR / ".git").exists()
    venv_present = (MATRIXLAB_LOCAL_DIR / ".venv").exists()
    runner_present = repo_present and (MATRIXLAB_LOCAL_DIR / "runner" / "app" / "main.py").exists()
    installed = repo_present and venv_present and runner_present
    running = installed and _native_running()
    return {
        "installed": installed,
        "running": running,
        "repo_present": repo_present,
        "venv_present": venv_present,
        "runner_present": runner_present,
        "local_dir": str(MATRIXLAB_LOCAL_DIR),
        "git_url": MATRIXLAB_GIT_URL,
        "pid": _read_pid() if running else None,
    }


@router.get("/native/status")
async def matrixlab_native_status() -> Dict[str, Any]:
    """Inspection-only — never raises, never blocks."""
    info = await _native_status()
    info["lifecycleEnabled"] = _lifecycle_enabled()
    return info


@router.post("/install_local", response_model=MatrixLabStatus)
async def matrixlab_install_local() -> MatrixLabStatus:
    """Native install: ``git clone`` → ``python -m venv`` → ``pip install``.

    Idempotent: an existing checkout is reused (``git pull`` skipped on
    purpose to keep the operator in control of which revision they
    run).  Lifecycle gated like every other mutating endpoint.
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
                "Ask your administrator to enable MatrixLab lifecycle automation."
            ),
            errorCode="LIFECYCLE_DISABLED",
            technicalDetails=TechnicalDetails(
                rawError=f"Set {ENV_LIFECYCLE}=1 on the GitPilot backend and restart it.",
            ),
        )

    import shutil as _shutil
    if not _shutil.which("git"):
        return MatrixLabStatus(
            status="failed",
            installed=False, running=False, reachable=False,
            lifecycleEnabled=True,
            dockerAvailable=_docker_available(),
            activeSandbox=(get_settings().sandbox.backend or "subprocess").lower(),
            runnerUrl=get_settings().sandbox.matrixlab_url,
            message="Git is not installed on the GitPilot host.",
            errorCode="GIT_NOT_INSTALLED",
        )

    MATRIXLAB_LOCAL_DIR.parent.mkdir(parents=True, exist_ok=True)
    steps: List[Dict[str, Any]] = []

    # 1. Clone (or skip if already present)
    if not (MATRIXLAB_LOCAL_DIR / ".git").exists():
        clone_step = await _run_shell(
            ["git", "clone", "--depth", "1", MATRIXLAB_GIT_URL, str(MATRIXLAB_LOCAL_DIR)],
            timeout=300,
        )
        steps.append({
            "cmd": clone_step.cmd, "exitCode": clone_step.exit_code,
            "stdout": clone_step.stdout, "stderr": clone_step.stderr,
            "durationMs": clone_step.duration_ms,
        })
        if clone_step.exit_code != 0:
            return _native_failure(
                "MatrixLab repository clone failed.",
                "CLONE_FAILED", clone_step.stderr or clone_step.stdout,
                steps,
            )

    # 2. Create venv if missing
    venv = MATRIXLAB_LOCAL_DIR / ".venv"
    if not venv.exists():
        import sys as _sys
        venv_step = await _run_shell(
            [_sys.executable, "-m", "venv", str(venv)],
            timeout=120,
        )
        steps.append({
            "cmd": venv_step.cmd, "exitCode": venv_step.exit_code,
            "stdout": venv_step.stdout, "stderr": venv_step.stderr,
            "durationMs": venv_step.duration_ms,
        })
        if venv_step.exit_code != 0:
            return _native_failure(
                "Python virtualenv could not be created.",
                "VENV_CREATE_FAILED", venv_step.stderr or venv_step.stdout,
                steps,
            )

    # 3. Install runner requirements into the venv
    py = _venv_python(venv)
    req = MATRIXLAB_LOCAL_DIR / "runner" / "requirements.txt"
    if not req.exists():
        return _native_failure(
            "Runner requirements.txt not found in the cloned repository.",
            "RUNNER_LAYOUT_UNEXPECTED",
            f"expected {req}",
            steps,
        )
    pip_step = await _run_shell(
        [str(py), "-m", "pip", "install", "--upgrade", "pip"],
        timeout=180,
    )
    steps.append({
        "cmd": pip_step.cmd, "exitCode": pip_step.exit_code,
        "stdout": pip_step.stdout, "stderr": pip_step.stderr,
        "durationMs": pip_step.duration_ms,
    })
    install_step = await _run_shell(
        [str(py), "-m", "pip", "install", "-r", str(req)],
        timeout=600,
    )
    steps.append({
        "cmd": install_step.cmd, "exitCode": install_step.exit_code,
        "stdout": install_step.stdout, "stderr": install_step.stderr,
        "durationMs": install_step.duration_ms,
    })
    if install_step.exit_code != 0:
        return _native_failure(
            "MatrixLab requirements installation failed.",
            "PIP_INSTALL_FAILED",
            install_step.stderr or install_step.stdout,
            steps,
        )

    # 4. Start the runner
    return await _start_native_runner(extra_steps=steps)


@router.post("/start_local", response_model=MatrixLabStatus)
async def matrixlab_start_local() -> MatrixLabStatus:
    """Start the native runner if installed but stopped."""
    if not _lifecycle_enabled():
        return _native_failure(
            "Automatic start is disabled on this GitPilot backend.",
            "LIFECYCLE_DISABLED", None, [],
        )
    info = await _native_status()
    if not info["installed"]:
        return _native_failure(
            "MatrixLab is not installed locally yet. Run the local install first.",
            "NOT_INSTALLED", None, [],
        )
    return await _start_native_runner(extra_steps=[])


@router.post("/stop_local", response_model=MatrixLabStatus)
async def matrixlab_stop_local() -> MatrixLabStatus:
    """Stop the native runner (if one is recorded in our pidfile)."""
    if not _lifecycle_enabled():
        return _native_failure(
            "Automatic stop is disabled on this GitPilot backend.",
            "LIFECYCLE_DISABLED", None, [],
        )
    pid = _read_pid()
    if pid and _pid_alive(pid):
        import signal as _signal
        try:
            os.kill(pid, _signal.SIGTERM)
        except OSError as exc:
            return _native_failure(
                "Could not signal the MatrixLab runner.",
                "STOP_FAILED", str(exc), [],
            )
    try:
        MATRIXLAB_PID_FILE.unlink(missing_ok=True)  # type: ignore[call-arg]
    except OSError:
        pass

    cfg = get_settings().sandbox
    return MatrixLabStatus(
        status="needs_attention",
        installed=True, running=False, reachable=False,
        lifecycleEnabled=True,
        dockerAvailable=_docker_available(),
        activeSandbox=(cfg.backend or "subprocess").lower(),
        runnerUrl=cfg.matrixlab_url,
        message="MatrixLab runner stopped.",
        errorCode="STOPPED",
    )


async def _start_native_runner(*, extra_steps: List[Dict[str, Any]]) -> MatrixLabStatus:
    """Spawn ``uvicorn app.main:app`` from the cloned runner directory."""
    info = await _native_status()
    cfg = get_settings().sandbox
    if info["running"]:
        # Already up — re-probe health and report.
        reachable, raw_error, version = await _probe_health()
        status = derive_status(
            lifecycle=await _gather_lifecycle_status(),
            reachable=reachable,
            health_error=raw_error,
            active_sandbox=(cfg.backend or "subprocess").lower(),
        )
        return status.model_copy(update={
            "installed": True, "running": True,
            "version": version,
            "steps": extra_steps,
        })

    py = _venv_python(MATRIXLAB_LOCAL_DIR / ".venv")
    if not py.exists():
        return _native_failure(
            "MatrixLab virtualenv is missing — run the local install first.",
            "VENV_MISSING", f"expected {py}", extra_steps,
        )

    # Resolve the configured URL's port so /api/sandbox/run reaches the
    # native runner without further config.
    port = 8000
    try:
        from urllib.parse import urlparse
        parsed = urlparse(cfg.matrixlab_url or "http://localhost:8000")
        if parsed.port:
            port = int(parsed.port)
    except Exception:  # noqa: BLE001
        port = 8000

    runner_cwd = MATRIXLAB_LOCAL_DIR / "runner"
    log_fh = MATRIXLAB_LOG_FILE.open("ab")
    import subprocess as _subprocess
    try:
        proc = _subprocess.Popen(
            [str(py), "-m", "uvicorn", "app.main:app",
             "--host", "0.0.0.0", "--port", str(port)],
            cwd=str(runner_cwd),
            stdout=log_fh, stderr=log_fh,
            stdin=_subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return _native_failure(
            "Could not launch the MatrixLab runner process.",
            "SPAWN_FAILED", str(exc), extra_steps,
        )
    MATRIXLAB_PID_FILE.write_text(str(proc.pid))

    # Give uvicorn a moment to come up before probing /health.
    import asyncio as _asyncio
    for _ in range(20):  # up to ~4s
        await _asyncio.sleep(0.2)
        reachable, raw_error, version = await _probe_health()
        if reachable:
            break

    lifecycle = await _gather_lifecycle_status()
    # Native install bypasses the docker image presence check, so the
    # lifecycle ``installed`` flag won't reflect us — patch it.
    lifecycle = lifecycle.model_copy(update={
        "installed": True, "running": True,
    })
    status = derive_status(
        lifecycle=lifecycle,
        reachable=reachable,
        health_error=raw_error,
        active_sandbox=(cfg.backend or "subprocess").lower(),
    )
    update = {"steps": extra_steps}
    if version and status.status == "ready":
        update["version"] = version
    return status.model_copy(update=update)


def _native_failure(
    message: str,
    code: str,
    raw: Optional[str],
    steps: List[Dict[str, Any]],
) -> MatrixLabStatus:
    cfg = get_settings().sandbox
    return MatrixLabStatus(
        status="failed",
        installed=False, running=False, reachable=False,
        lifecycleEnabled=_lifecycle_enabled(),
        dockerAvailable=_docker_available(),
        activeSandbox=(cfg.backend or "subprocess").lower(),
        runnerUrl=cfg.matrixlab_url,
        message=message,
        errorCode=code,
        technicalDetails=TechnicalDetails(rawError=raw) if raw else None,
        steps=steps,
    )


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
