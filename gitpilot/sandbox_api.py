"""HTTP surface for the sandbox runtime switch.

Three endpoints, all additive:

* ``GET  /api/sandbox/status``   — what's configured, can we reach it?
* ``PUT  /api/sandbox/config``   — update the persisted SandboxSettings.
* ``POST /api/sandbox/run``      — execute one ``{language, code}`` snippet
                                   through the currently-selected backend.

The chat UI uses :func:`run_snippet` to power the per-codeblock Run button
introduced in the AssistantMessage component, so a user can ask "write a
hello-world in Python", click Run, and see the output inline without
leaving GitPilot.  Which sandbox actually executes the snippet (local
subprocess vs MatrixLab Runner) is controlled by Settings → Sandbox
Runtime.

Routes are mounted from :mod:`gitpilot.api` via ``app.include_router``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .sandbox import (
    BACKEND_MATRIXLAB,
    BACKEND_OFF,
    BACKEND_SUBPROCESS,
    DEFAULT_TIMEOUT_SEC,
    SandboxPolicy,
    SandboxResult,
    SandboxUnavailableError,
    SandboxRunError,
    MatrixLabSandbox,
    NullSandbox,
    SubprocessSandbox,
)
from .settings import AppSettings, SandboxSettings, get_settings, update_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])

# How each fenced-code language is launched inside the sandbox.  Anything
# the user can opt-in to from the Run button has to be listed here; the
# whitelist keeps random shells (``ruby``, ``perl``, ...) from being
# silently executed just because an LLM tagged a fence with that name.
LANGUAGE_RUNNERS: Dict[str, Dict[str, Any]] = {
    "python": {"suffix": ".py", "argv": ["python3", "{file}"]},
    "py": {"suffix": ".py", "argv": ["python3", "{file}"]},
    "javascript": {"suffix": ".js", "argv": ["node", "{file}"]},
    "js": {"suffix": ".js", "argv": ["node", "{file}"]},
    "node": {"suffix": ".js", "argv": ["node", "{file}"]},
    "bash": {"suffix": ".sh", "argv": ["bash", "{file}"]},
    "sh": {"suffix": ".sh", "argv": ["bash", "{file}"]},
    "shell": {"suffix": ".sh", "argv": ["bash", "{file}"]},
}

ALLOWED_BACKENDS = {BACKEND_OFF, BACKEND_SUBPROCESS, BACKEND_MATRIXLAB}


# ----------------------------------------------------------------------
# Request / response models
# ----------------------------------------------------------------------

class SandboxStatusResponse(BaseModel):
    backend: str
    available_backends: list[str]
    matrixlab_url: str
    matrixlab_image: str
    allow_network: bool
    timeout_sec: int
    has_token: bool
    ok: bool
    error: Optional[str] = None
    remote: Optional[Dict[str, Any]] = None
    # Name of the env var currently shadowing the persisted backend
    # choice, if any.  Used by the Settings panel to render an "env
    # override" badge so users understand why their UI selection isn't
    # taking effect.  ``None`` when persistence is authoritative.
    env_override: Optional[str] = None


class SandboxConfigUpdate(BaseModel):
    backend: Optional[str] = None
    matrixlab_url: Optional[str] = None
    matrixlab_token: Optional[str] = None
    matrixlab_image: Optional[str] = None
    allow_network: Optional[bool] = None
    timeout_sec: Optional[int] = Field(default=None, ge=1, le=600)


class SandboxRunRequest(BaseModel):
    language: str
    code: str
    timeout_sec: Optional[int] = Field(default=None, ge=1, le=600)
    #: Proof that an ExecutionPlan for exactly this code was approved
    #: (Batch V4-D6, F7).  Minted by ``POST /api/sandbox/approve``.
    approval_token: Optional[str] = None
    #: Which session is running this, so a token cannot be spent by another and
    #: so the *persisted* mode can be consulted.  There is deliberately no
    #: ``permission_mode`` field: a request body is not an authorisation channel.
    session_id: Optional[str] = None


class SandboxRunResponse(BaseModel):
    backend: str
    language: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False
    timed_out: bool = False
    sandbox_id: Optional[str] = None


# ----------------------------------------------------------------------
# Execution-plan models (approval-first sandbox)
# ----------------------------------------------------------------------
#
# Every sandbox run starts as a deterministic ExecutionPlan the user
# must approve.  These models are the over-the-wire shape — the
# canonical builder lives in :mod:`gitpilot.sandbox_plan` and stays
# free of FastAPI / Pydantic so it can be unit-tested in isolation.

class SandboxPlanRequest(BaseModel):
    # Exactly one of (file) or (code+language) must be set.
    file: Optional[str] = None
    code: Optional[str] = None
    language: Optional[str] = None
    # Source of the request — drives the badge in the approval card
    # and lets us tell file-run vs code-block-run apart in analytics.
    source: str = Field(default="chat")
    # Optional Rerun lineage so the new ExecutionCard can reference
    # the run it replays.
    parent_run_id: Optional[str] = None
    # Override timeout per-plan; falls back to sandbox.timeout_sec.
    timeout_sec: Optional[int] = Field(default=None, ge=1, le=600)
    #: The session the plan belongs to, so its approval token is bound to it.
    session_id: Optional[str] = None
    # The list of files in the active repo, used to verify file-run
    # plans.  Sent by the client so the planner is stateless and
    # repo-aware without re-reading the tree on every plan call.
    repo_files: Optional[List[str]] = None
    # Inline plans only: override the synthetic "inline.<ext>" name
    # in the displayed command.  Used by the file-aware Canvas so a
    # repo file opened for editing produces a plan that reads
    # ``python demo.py`` instead of ``python inline.py``.  Cosmetic.
    display_filename: Optional[str] = None


class SandboxPlanResponse(BaseModel):
    plan: Dict[str, Any]


class SandboxApproveRequest(BaseModel):
    plan_id: str
    session_id: Optional[str] = None


class SandboxApproveResponse(BaseModel):
    approval_token: str
    plan_id: str
    expires_in: int


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _build_sandbox(cfg: SandboxSettings, *, workspace: Path, timeout: int):
    """Construct the right sandbox instance from persisted settings.

    Distinct from :func:`gitpilot.sandbox.get_sandbox` because that
    factory reads ``settings={"tools": {"sandbox": ...}}`` for backwards
    compatibility with the older shape; here we already have the typed
    :class:`SandboxSettings` so the indirection isn't needed.
    """
    policy = SandboxPolicy(
        workspace=workspace,
        timeout_sec=timeout,
        allow_network=cfg.allow_network,
        image=cfg.matrixlab_image or None,
    )
    backend = (cfg.backend or BACKEND_SUBPROCESS).strip().lower()
    if backend == BACKEND_OFF:
        return NullSandbox(policy)
    if backend == BACKEND_MATRIXLAB:
        return MatrixLabSandbox(
            policy,
            base_url=cfg.matrixlab_url or None,
            token=cfg.matrixlab_token or None,
        )
    return SubprocessSandbox(policy)


def _detect_env_override() -> Optional[str]:
    """Return the env var name currently shadowing the persisted backend
    choice, or None if persistence wins.  Mirrors the precedence rules
    in :func:`gitpilot.sandbox._resolve_backend_name` so what we surface
    in the UI matches what actually executes."""
    import os as _os

    for name in (
        "GITPILOT_SANDBOX",
        "GITPILOT_MATRIXLAB_URL",
        "GITPILOT_MATRIXLAB_TOKEN",
        "GITPILOT_MATRIXLAB_IMAGE",
    ):
        if _os.environ.get(name):
            return name
    return None


def _status_from(cfg: SandboxSettings, health: Dict[str, Any]) -> SandboxStatusResponse:
    return SandboxStatusResponse(
        backend=cfg.backend,
        available_backends=sorted(ALLOWED_BACKENDS),
        matrixlab_url=cfg.matrixlab_url,
        matrixlab_image=cfg.matrixlab_image,
        allow_network=cfg.allow_network,
        timeout_sec=cfg.timeout_sec,
        has_token=bool(cfg.matrixlab_token),
        ok=bool(health.get("ok")),
        error=health.get("error"),
        remote=health.get("remote"),
        env_override=_detect_env_override(),
    )


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------

@router.get("/status", response_model=SandboxStatusResponse)
async def api_sandbox_status() -> SandboxStatusResponse:
    """Report which backend is selected and whether it's reachable."""
    s: AppSettings = get_settings()
    cfg = s.sandbox
    workspace = Path.cwd()
    sb = _build_sandbox(cfg, workspace=workspace, timeout=cfg.timeout_sec)
    try:
        health = await sb.health()
    finally:
        # MatrixLabSandbox owns an httpx client; close it so we don't
        # leak sockets on every status poll from the settings page.
        aclose = getattr(sb, "aclose", None)
        if aclose is not None:
            await aclose()
    return _status_from(cfg, health)


@router.put("/config", response_model=SandboxStatusResponse)
async def api_sandbox_config(update: SandboxConfigUpdate) -> SandboxStatusResponse:
    """Persist new sandbox settings and return the resulting status."""
    if update.backend is not None and update.backend not in ALLOWED_BACKENDS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown sandbox backend {update.backend!r}; "
                   f"expected one of {sorted(ALLOWED_BACKENDS)}",
        )

    s: AppSettings = get_settings()
    merged: Dict[str, Any] = s.sandbox.model_dump()
    for field, value in update.model_dump(exclude_none=True).items():
        merged[field] = value

    updated = update_settings({"sandbox": merged})
    cfg = updated.sandbox

    # Probe the new configuration so the UI can flip its health pill in
    # one round-trip (mirrors what /status does).
    sb = _build_sandbox(cfg, workspace=Path.cwd(), timeout=cfg.timeout_sec)
    try:
        health = await sb.health()
    finally:
        aclose = getattr(sb, "aclose", None)
        if aclose is not None:
            await aclose()
    return _status_from(cfg, health)


@router.post("/plan", response_model=SandboxPlanResponse)
async def api_sandbox_plan(req: SandboxPlanRequest) -> SandboxPlanResponse:
    """Return a deterministic ExecutionPlan for the next sandbox run.

    The plan is *not* executed here — the UI shows it to the user and
    POSTs to :func:`api_sandbox_run` (Batch 2 will introduce
    ``/execute`` with streaming) only after explicit approval. The
    plan is built without any LLM round-trip so the user sees the
    approval card in milliseconds and the "empty plan" failure mode
    is eliminated for run requests entirely.

    Exactly one of ``file`` or ``code + language`` must be set:
      * ``file`` — file-run path (chat command, sidebar ▶, Rerun).
      * ``code`` + ``language`` — inline path (code-block, Canvas).
    """
    from .sandbox_plan import (  # local import: keeps cold start lean
        build_execution_plan_for_file,
        build_execution_plan_for_inline,
    )

    s: AppSettings = get_settings()
    cfg = s.sandbox
    backend_name = (cfg.backend or BACKEND_SUBPROCESS).strip().lower()
    # The plan card surfaces the *effective* backend.  We collapse
    # "off" into "subprocess" for display because pass-through runs
    # still execute through the subprocess pipeline; the safety
    # advisory ("Network: disabled") is identical either way.
    sandbox_label = "matrixlab" if backend_name == BACKEND_MATRIXLAB else "subprocess"
    timeout = int(req.timeout_sec or cfg.timeout_sec or DEFAULT_TIMEOUT_SEC)

    source = (req.source or "chat").strip().lower()
    if source not in {"chat", "code_block", "file_run", "canvas", "rerun"}:
        source = "chat"

    if req.file:
        plan = build_execution_plan_for_file(
            file=req.file,
            repo_files=req.repo_files or [req.file],
            sandbox=sandbox_label,  # type: ignore[arg-type]
            timeout_sec=timeout,
            network=bool(cfg.allow_network),
            source=source,  # type: ignore[arg-type]
            parent_run_id=req.parent_run_id,
        )
        if plan is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"cannot plan run for {req.file!r}: file not in repo "
                    f"or extension not in the runnable allowlist."
                ),
            )
        _remember_plan(plan, req.session_id, language=plan.language, code=plan.inline_code or "")
        return SandboxPlanResponse(plan=plan.to_dict())

    if req.code is not None:
        if not (req.language or "").strip():
            raise HTTPException(
                status_code=400, detail="inline plan requires a language tag",
            )
        plan = build_execution_plan_for_inline(
            code=req.code,
            language=req.language or "",
            source=source,  # type: ignore[arg-type]
            sandbox=sandbox_label,  # type: ignore[arg-type]
            timeout_sec=timeout,
            network=bool(cfg.allow_network),
            parent_run_id=req.parent_run_id,
            display_filename=req.display_filename,
        )
        if plan is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"cannot plan run for language {req.language!r}: "
                    f"snippet is empty or language is not runnable."
                ),
            )
        _remember_plan(plan, req.session_id, language=req.language or "", code=req.code)
        return SandboxPlanResponse(plan=plan.to_dict())

    raise HTTPException(
        status_code=400,
        detail="provide either 'file' or 'code'+'language' to build a plan",
    )


#: Set ``GITPILOT_SANDBOX_REQUIRE_APPROVAL=0`` to run without a token.  It exists
#: for a single-user local install that drives the endpoint from a script, and it
#: is opt-out rather than opt-in because a shipped default of "trust the client"
#: is what F7 was.
_APPROVAL_ENV = "GITPILOT_SANDBOX_REQUIRE_APPROVAL"


def _approval_required() -> bool:
    return (os.environ.get(_APPROVAL_ENV, "1").strip().lower()
            not in ("0", "false", "no", "off"))


def _require_approval(req: SandboxRunRequest) -> None:
    """Refuse a run nobody approved — Batch V4-D6 (F7)."""
    if not _approval_required():
        return

    from .sandbox_tokens import get_store, session_runs_without_asking

    session_id = (req.session_id or "").strip()

    if req.approval_token:
        ok, reason = get_store().consume(
            req.approval_token,
            language=req.language, code=req.code, session_id=session_id,
        )
        if ok:
            return
        raise HTTPException(status_code=403, detail=reason)

    # The only other way through: a session the user themselves switched to
    # `auto`, read from the persisted record.  Never from this request body.
    if session_runs_without_asking(session_id):
        return

    raise HTTPException(
        status_code=403,
        detail=(
            "this run has not been approved: POST /api/sandbox/plan, then "
            "/api/sandbox/approve, and send the resulting approval_token"
        ),
    )


def _remember_plan(plan: Any, session_id: Optional[str], *, language: str, code: str) -> None:
    """Keep the plan server-side so its approval can be verified (F7).

    A file-run plan has no captured code — it reads the file at execute time —
    so its digest covers the command instead. Either way the token is bound to
    something the server chose, not to something the client sends back.
    """
    from .sandbox_tokens import get_store

    payload = code if code else " ".join(plan.command)
    get_store().remember(
        plan_id=plan.plan_id,
        session_id=session_id or "",
        language=language or plan.language,
        code=payload,
        plan=plan.to_dict(),
    )


@router.post("/approve", response_model=SandboxApproveResponse)
async def api_sandbox_approve(req: SandboxApproveRequest) -> SandboxApproveResponse:
    """Approve a planned run and mint a single-use token — Batch V4-D6 (F7).

    The ExecutionPlan card is unchanged; what changes is that its Approve button
    now produces something the server will check. Before this, approval was
    entirely client-side and ``/run`` executed whatever it was handed.
    """
    from .sandbox_tokens import TOKEN_TTL_S, get_store

    token = get_store().approve(req.plan_id, req.session_id or "")
    if token is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no plan {req.plan_id!r} awaiting approval for this session; "
                "request a plan first"
            ),
        )
    return SandboxApproveResponse(
        approval_token=token.token, plan_id=token.plan_id, expires_in=int(TOKEN_TTL_S),
    )


@router.post("/run", response_model=SandboxRunResponse)
async def api_sandbox_run(req: SandboxRunRequest) -> SandboxRunResponse:
    """Execute a fenced-code snippet through the configured sandbox.

    Powers the per-codeblock Run button in AssistantMessage: the chat
    UI POSTs ``{language, code}`` and renders ``stdout`` / ``stderr`` /
    ``exit_code`` next to the snippet.  The selected backend (local
    subprocess vs MatrixLab) is whatever the user picked in Settings.

    Requires an approval token, or a session whose **persisted** mode is
    ``auto`` — Batch V4-D6 (F7). Until then this endpoint executed arbitrary
    code for anyone who could reach it, with the approval card providing the
    appearance of a gate and none of the substance.
    """
    _require_approval(req)
    lang = req.language.strip().lower()
    spec = LANGUAGE_RUNNERS.get(lang)
    if spec is None:
        raise HTTPException(
            status_code=400,
            detail=f"language {req.language!r} is not runnable; "
                   f"allowed: {sorted(set(LANGUAGE_RUNNERS))}",
        )
    if not req.code.strip():
        raise HTTPException(status_code=400, detail="code is empty")

    s: AppSettings = get_settings()
    cfg = s.sandbox
    timeout = req.timeout_sec or cfg.timeout_sec or DEFAULT_TIMEOUT_SEC

    # MatrixLab has a purpose-built snippet endpoint (POST /code/run) that
    # accepts {language, code} directly and dispatches into the right
    # per-language sandbox image.  When the user selected the matrixlab
    # backend, route there instead of running the snippet locally and
    # asking MatrixLab to re-execute the resulting argv via /repo/run —
    # /code/run is what the Runner is designed to serve for this flow.
    if (cfg.backend or "").strip().lower() == BACKEND_MATRIXLAB:
        return await _run_via_matrixlab_code_endpoint(cfg, lang, req.code, timeout)

    # Materialise the snippet in a fresh tempdir so the workspace jail
    # in SubprocessSandbox has somewhere to point at.  MatrixLabSandbox
    # mounts the same path into the container via ``mount_workspace``,
    # so the runner sees the same file at the same path — keeping the
    # contract identical across backends.
    with tempfile.TemporaryDirectory(prefix="gitpilot-run-") as tmp:
        workspace = Path(tmp)
        snippet_path = workspace / f"snippet{spec['suffix']}"
        snippet_path.write_text(req.code, encoding="utf-8")
        argv = [
            tok.replace("{file}", str(snippet_path)) for tok in spec["argv"]
        ]
        command_str = shlex.join(argv)

        sb = _build_sandbox(cfg, workspace=workspace, timeout=timeout)
        try:
            try:
                result: SandboxResult = await sb.run(
                    argv, cwd=workspace, timeout=timeout
                )
            except SandboxUnavailableError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"sandbox backend {cfg.backend!r} is unreachable: {exc}",
                ) from exc
            except SandboxRunError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"sandbox backend {cfg.backend!r} returned an error: {exc}",
                ) from exc
            except PermissionError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            aclose = getattr(sb, "aclose", None)
            if aclose is not None:
                await aclose()

    return SandboxRunResponse(
        backend=result.backend,
        language=lang,
        command=command_str,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        truncated=result.truncated,
        timed_out=result.timed_out,
        sandbox_id=result.sandbox_id,
    )


# MatrixLab's CodeRunRequest only accepts these literals; aliases ("py",
# "js", "node", "sh", "shell") get normalised before the call.
_MATRIXLAB_LANGUAGE = {
    "python": "python",
    "py": "python",
    "javascript": "javascript",
    "js": "javascript",
    "node": "javascript",
    "bash": "bash",
    "sh": "bash",
    "shell": "bash",
}


async def _run_via_matrixlab_code_endpoint(
    cfg: SandboxSettings, lang: str, code: str, timeout: int
) -> SandboxRunResponse:
    """POST /code/run on the MatrixLab Runner.

    Direct call (not via :class:`MatrixLabSandbox`) because the snippet
    endpoint takes ``{language, code}`` rather than the
    command-with-mounted-workspace shape that ``/repo/run`` expects.
    """
    target_lang = _MATRIXLAB_LANGUAGE.get(lang)
    if target_lang is None:
        raise HTTPException(
            status_code=400,
            detail=f"language {lang!r} is not supported by MatrixLab /code/run",
        )
    base_url = (cfg.matrixlab_url or "http://localhost:8765").rstrip("/")
    headers = {"Content-Type": "application/json"}
    if cfg.matrixlab_token:
        headers["Authorization"] = f"Bearer {cfg.matrixlab_token}"
    body = {
        "language": target_lang,
        "code": code,
        "timeout": timeout,
        "allow_network": cfg.allow_network,
    }
    if cfg.matrixlab_image:
        body["image"] = cfg.matrixlab_image

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout + 5) as client:
            resp = await client.post(f"{base_url}/code/run", json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"sandbox backend 'matrixlab' is unreachable: {exc}",
        ) from exc
    duration_ms = int((time.monotonic() - start) * 1000)

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"MatrixLab /code/run returned {resp.status_code}: {resp.text[:400]}",
        )
    data = resp.json()
    return SandboxRunResponse(
        backend=BACKEND_MATRIXLAB,
        language=lang,
        command=f"{target_lang} <snippet>",
        exit_code=int(data.get("exit_code", -1)),
        stdout=str(data.get("stdout", "")),
        stderr=str(data.get("stderr", "")),
        duration_ms=int(data.get("duration_ms", duration_ms)),
        truncated=bool(data.get("truncated", False)),
        timed_out=bool(data.get("timed_out", False)),
        sandbox_id=data.get("sandbox_id"),
    )


# ----------------------------------------------------------------------
# MatrixLab lifecycle (install / start) — opt-in via env flag
# ----------------------------------------------------------------------
#
# Lifecycle endpoints shell out to the host (``docker pull``,
# ``docker run``), so they are gated behind ``GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE=1``.
# When the gate is off, GET /lifecycle still works — it just reports
# the inventory and surfaces a clear "operator must enable" message
# on the action booleans, and POST /install / /start return 403.  This
# keeps the default GitPilot deployment honest: no shell from a web
# endpoint unless the operator opted in.

ENV_LIFECYCLE = "GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE"
# Image the Runner ships under (matches matrixlab/Makefile's
# $(REGISTRY)/$(DOCKERHUB_NAMESPACE)/matrixlab-runner).  Operator can
# override via env when running a custom build.
DEFAULT_RUNNER_IMAGE = os.environ.get(
    "GITPILOT_MATRIXLAB_RUNNER_IMAGE",
    "ruslanmv/matrixlab-runner:latest",
)
# Sandbox images the Runner spawns per language.  Pulling these at
# install time means the first /code/run from the chat UI doesn't
# stall on a multi-hundred-MB image fetch.  Names match the
# ``$(REGISTRY)/$(DOCKERHUB_NAMESPACE)/matrix-lab-sandbox-*`` tags
# the matrixlab repo's Makefile builds and pushes via the
# ``docker-publish`` GitHub workflow.
_MATRIXLAB_NAMESPACE = os.environ.get(
    "GITPILOT_MATRIXLAB_NAMESPACE", "ruslanmv",
)
DEFAULT_SANDBOX_IMAGES = [
    f"{_MATRIXLAB_NAMESPACE}/matrix-lab-sandbox-python:latest",
    f"{_MATRIXLAB_NAMESPACE}/matrix-lab-sandbox-node:latest",
    f"{_MATRIXLAB_NAMESPACE}/matrix-lab-sandbox-utils:latest",
]
DEFAULT_CONTAINER_NAME = os.environ.get(
    "GITPILOT_MATRIXLAB_CONTAINER",
    "gitpilot-matrixlab",
)


class _StepResult(BaseModel):
    cmd: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0


class MatrixLabLifecycleResponse(BaseModel):
    docker_available: bool
    installed: bool
    running: bool
    lifecycle_enabled: bool
    runner_image: str
    sandbox_images: List[str]
    container_name: str
    matrixlab_url: str
    instructions: Optional[str] = None
    error: Optional[str] = None
    steps: List[_StepResult] = Field(default_factory=list)


def _lifecycle_enabled() -> bool:
    """Decide whether ``/api/matrixlab/install`` / ``start`` / ``stop`` are allowed.

    GitPilot ships as a single-tenant local tool; anyone who can reach
    the backend can already execute arbitrary code via the existing
    subprocess sandbox, so the addon-install endpoints don't materially
    expand the attack surface.  Default is therefore **enabled** —
    clicking ``Install MatrixLab Addon`` in the UI is the user's opt-in.

    Hardened deployments (future multi-tenant, untrusted-reverse-proxy)
    can flip this off by setting ``GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE=0``
    (or ``false`` / ``no`` / ``off``).  The env var name is preserved
    for backward compatibility; only the polarity inverted.
    """
    raw = os.environ.get(ENV_LIFECYCLE, "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    # Anything else (empty, "1", "true", "yes", garbage) → enabled.
    return True


async def _run_shell(cmd: List[str], *, timeout: int = 600) -> _StepResult:
    """Run a host command, capture stdout/stderr, never raise.

    Used for the docker / matrixlab lifecycle commands so the response
    body always carries the full transcript even when a step fails —
    matches the "errors are first-class signals" UX of the agent loop.
    """
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return _StepResult(
                cmd=shlex.join(cmd),
                exit_code=-1,
                stderr=f"timed out after {timeout}s",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return _StepResult(
            cmd=shlex.join(cmd),
            exit_code=proc.returncode or 0,
            stdout=stdout_b.decode("utf-8", errors="replace")[:8_000],
            stderr=stderr_b.decode("utf-8", errors="replace")[:8_000],
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except FileNotFoundError as exc:
        return _StepResult(
            cmd=shlex.join(cmd),
            exit_code=-2,
            stderr=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )


def _docker_available() -> bool:
    return shutil.which("docker") is not None


async def _docker_image_present(name: str) -> bool:
    """True when ``docker images -q <name>`` returns at least one ID."""
    if not _docker_available():
        return False
    step = await _run_shell(["docker", "images", "-q", name], timeout=10)
    return step.exit_code == 0 and bool(step.stdout.strip())


async def _matrixlab_running() -> bool:
    """Probe the configured Runner URL for a healthy /health response.

    "Answered with 200" is not the same as "can execute": the Runner
    replies while its own Docker daemon is unreachable, and reporting
    that as running produced a green lifecycle state followed by a
    failure on every run.  Its ``ok`` flag is the authority; a body we
    cannot parse falls back to the status code, which is what an older
    Runner without the flag deserves.
    """
    cfg = get_settings().sandbox
    base = (cfg.matrixlab_url or "http://localhost:8765").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base}/health")
            if resp.status_code != 200:
                return False
            try:
                payload = resp.json()
            except Exception:  # noqa: BLE001
                return True
            if isinstance(payload, dict) and payload.get("ok") is False:
                return False
            return True
    except httpx.HTTPError:
        return False


async def _gather_lifecycle_status(steps: Optional[List[_StepResult]] = None) -> MatrixLabLifecycleResponse:
    cfg = get_settings().sandbox
    docker_ok = _docker_available()
    runner_installed = await _docker_image_present(DEFAULT_RUNNER_IMAGE)
    running = await _matrixlab_running()
    enabled = _lifecycle_enabled()
    instructions: Optional[str] = None
    if not docker_ok:
        instructions = (
            "Docker is not installed or not on PATH on the GitPilot host. "
            "Install Docker (https://docs.docker.com/get-docker/) before "
            "the Install / Start buttons can do anything."
        )
    elif not enabled:
        instructions = (
            "Lifecycle automation is off. To let GitPilot pull and start "
            f"MatrixLab from the Settings panel set the {ENV_LIFECYCLE}=1 "
            "environment variable on the GitPilot backend and restart. "
            "Until then, run 'docker compose up -d' from a MatrixLab "
            "checkout (https://github.com/agent-matrix/matrixlab) yourself."
        )
    return MatrixLabLifecycleResponse(
        docker_available=docker_ok,
        installed=runner_installed,
        running=running,
        lifecycle_enabled=enabled,
        runner_image=DEFAULT_RUNNER_IMAGE,
        sandbox_images=DEFAULT_SANDBOX_IMAGES,
        container_name=DEFAULT_CONTAINER_NAME,
        matrixlab_url=cfg.matrixlab_url,
        instructions=instructions,
        steps=steps or [],
    )


@router.get("/matrixlab/lifecycle", response_model=MatrixLabLifecycleResponse)
async def api_matrixlab_lifecycle() -> MatrixLabLifecycleResponse:
    """Report whether MatrixLab is installed locally and running.

    Used by the Settings panel to decide which button to show:
    Install (when no runner image present) → Start (image present but
    URL unreachable) → Running (URL healthy).  Always safe to call —
    pure inspection, no side effects.
    """
    return await _gather_lifecycle_status()


@router.post("/matrixlab/install", response_model=MatrixLabLifecycleResponse)
async def api_matrixlab_install() -> MatrixLabLifecycleResponse:
    """Pull the MatrixLab runner + sandbox images.

    Gated by GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE.  Each pull is a
    distinct step in the response so the UI can show a per-image
    progress strip (and the operator can re-read failures verbatim).
    """
    if not _lifecycle_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                f"set {ENV_LIFECYCLE}=1 on the GitPilot backend to enable "
                "the Install button"
            ),
        )
    if not _docker_available():
        raise HTTPException(status_code=503, detail="docker is not on PATH")
    steps: List[_StepResult] = []
    images = [DEFAULT_RUNNER_IMAGE, *DEFAULT_SANDBOX_IMAGES]
    for image in images:
        steps.append(await _run_shell(["docker", "pull", image], timeout=900))
    return await _gather_lifecycle_status(steps=steps)


@router.post("/matrixlab/start", response_model=MatrixLabLifecycleResponse)
async def api_matrixlab_start() -> MatrixLabLifecycleResponse:
    """Start the MatrixLab runner as a detached container.

    Gated by GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE.  The container name
    is deterministic (``gitpilot-matrixlab`` by default) so repeated
    clicks reuse it — ``docker start`` an existing stopped container,
    or ``docker run`` if it doesn't exist yet.  The Docker socket is
    bind-mounted so the runner can spawn per-language sandbox
    containers — matches what 'make run' inside a MatrixLab checkout
    does.
    """
    if not _lifecycle_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                f"set {ENV_LIFECYCLE}=1 on the GitPilot backend to enable "
                "the Start button"
            ),
        )
    if not _docker_available():
        raise HTTPException(status_code=503, detail="docker is not on PATH")

    steps: List[_StepResult] = []
    # Determine which port to expose locally — derive it from the
    # configured matrixlab_url so 'Start' agrees with /status.  The
    # 8765 fallback matches MatrixLab's own docker-compose default,
    # which moved off :8000 to avoid clashing with GitPilot's backend.
    cfg = get_settings().sandbox
    port = 8765
    try:
        from urllib.parse import urlparse

        parsed = urlparse(cfg.matrixlab_url)
        if parsed.port:
            port = int(parsed.port)
    except Exception:  # noqa: BLE001
        port = 8765

    # Does a container with the canonical name already exist?
    inspect = await _run_shell(
        ["docker", "inspect", "--format", "{{.State.Status}}", DEFAULT_CONTAINER_NAME],
        timeout=15,
    )
    steps.append(inspect)
    if inspect.exit_code == 0:
        # Container exists — start it if stopped, otherwise leave it be.
        steps.append(await _run_shell(["docker", "start", DEFAULT_CONTAINER_NAME], timeout=60))
    else:
        # Docker-in-Docker bind-mount fix.  The runner writes the per-
        # request workspace under MATRIXLAB_LOCAL_JOBS_DIR (inside its
        # own container), then asks the HOST docker daemon to mount
        # that path into the sandbox at /workspace.  If the path isn't
        # also visible on the host filesystem the bind ends up empty
        # and ``python: can't open file '/workspace/main.py'`` results.
        # Share a single host directory at the same path on both sides
        # so docker's path-translation is a no-op.
        jobs_dir = os.environ.get(
            "GITPILOT_MATRIXLAB_JOBS_DIR",
            "/tmp/gitpilot-matrixlab-jobs",
        )
        os.makedirs(jobs_dir, exist_ok=True)
        try:
            os.chmod(jobs_dir, 0o777)
        except OSError:
            pass

        run_cmd = [
            "docker", "run", "-d",
            "--name", DEFAULT_CONTAINER_NAME,
            "-p", f"{port}:8000",
            "-v", "/var/run/docker.sock:/var/run/docker.sock",
            "-v", f"{jobs_dir}:{jobs_dir}",
            "-e", f"MATRIXLAB_LOCAL_JOBS_DIR={jobs_dir}",
            "-e", f"MATRIXLAB_HOST_JOBS_DIR={jobs_dir}",
            "--restart", "unless-stopped",
            DEFAULT_RUNNER_IMAGE,
        ]
        steps.append(await _run_shell(run_cmd, timeout=120))

    return await _gather_lifecycle_status(steps=steps)


@router.post("/matrixlab/stop", response_model=MatrixLabLifecycleResponse)
async def api_matrixlab_stop() -> MatrixLabLifecycleResponse:
    """Stop the GitPilot-managed MatrixLab container.

    Only affects the deterministic ``gitpilot-matrixlab`` container —
    won't touch containers an operator launched manually.  Gated by
    the same env flag as install/start.
    """
    if not _lifecycle_enabled():
        raise HTTPException(
            status_code=403,
            detail=f"set {ENV_LIFECYCLE}=1 to enable lifecycle actions",
        )
    if not _docker_available():
        raise HTTPException(status_code=503, detail="docker is not on PATH")
    step = await _run_shell(["docker", "stop", DEFAULT_CONTAINER_NAME], timeout=30)
    return await _gather_lifecycle_status(steps=[step])
