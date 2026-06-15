"""Matrix-native run facade for GitPilot (Batch 2).

Exposes ``POST /api/v1/gitpilot/runs`` so Matrix Builder can hand a signed
Matrix Bundle to GitPilot as a controlled run. The facade maps the Matrix run
contract onto the existing repair pipeline (:class:`RepairRequest`) and never
lets a caller punch a hole in the Matrix control files: ``MATRIX_STANDARDS.lock``
and ``MATRIX_BLUEPRINT.yaml`` are always added to ``forbidden_paths``, on top of
the caller's ``forbidden_files`` and the pipeline's ``DEFAULT_FORBIDDEN_PATHS``.

Product rule
------------
Matrix Builder is the architect and judge; GitPilot is the implementation
worker. GitPilot may read the bundle and implement inside the contract, but it
can never edit the Matrix control files, approve its own work, or create a
Matrix Commit — those stay with Matrix Builder.

Security
--------
This router is gated by the **A2A shared secret**
(``GITPILOT_A2A_SHARED_SECRET`` / ``GITPILOT_A2A_REQUIRE_AUTH``) — the same
authenticated agent-to-agent channel used by the A2A adapter.

Scope of Batch 2
----------------
The facade validates the contract, applies the Matrix guardrails, maps to a
:class:`RepairRequest`, registers a *queued* run, and returns
``{run_id, status: "queued", url}``. Actually executing the run and exposing
``GET /api/v1/gitpilot/runs/{run_id}`` lands in a later batch (result sync).
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from gitpilot.a2a_adapter import _require_gateway_secret
from gitpilot.repair.pr_writer import DraftPRWriter, draft_pr_enabled
from gitpilot.repair.schema import DEFAULT_FORBIDDEN_PATHS, RepairMode, RepairRequest
from gitpilot.repair.service import run_repair

# Matrix control files GitPilot must never modify — always denied, even if a
# caller lists them in allowed_files. Basename + nested forms are both listed
# for clarity; the repair policy matches on basename too.
MATRIX_CONTROL_FILES: list[str] = [
    "MATRIX_STANDARDS.lock",
    "MATRIX_BLUEPRINT.yaml",
    "**/MATRIX_STANDARDS.lock",
    "**/MATRIX_BLUEPRINT.yaml",
]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


class MatrixRunRequest(BaseModel):
    """The Matrix run contract sent by Matrix Builder."""

    bundle_url: str
    task_id: str
    prompt: str = ""
    project_name: str = ""
    allowed_files: list[str] = Field(default_factory=list)
    forbidden_files: list[str] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)
    # Free-form Matrix mode ("ask" / "plan" / "apply"…); mapped conservatively.
    mode: str = "ask"


class MatrixRunResponse(BaseModel):
    run_id: str
    status: str = "queued"
    url: str


class MatrixRepairRequest(BaseModel):
    """A repair task for an existing run (Batch 8).

    Findings + the repair prompt become issues; the run re-executes inside the
    same contract (allowed/forbidden), always denying the Matrix control files.
    """

    validation_findings: list[str] = Field(default_factory=list)
    repair_prompt: str = ""
    allowed_files: list[str] = Field(default_factory=list)
    forbidden_files: list[str] = Field(default_factory=list)


class MatrixPrRequest(BaseModel):
    """Open a PR for a run's diff (Batch 11).

    Matrix Builder only calls this after a Matrix-approved verdict — GitPilot
    never opens a PR for its own un-validated work.
    """

    repo_url: str = ""
    title: str = ""
    base: str = "main"


class MatrixPrResponse(BaseModel):
    run_id: str
    pr_url: str | None = None
    status: str = "draft"  # draft | created | disabled | no_repo
    message: str = ""


class MatrixRunStatus(BaseModel):
    """Result-sync shape Matrix Builder polls (Batch 6).

    ``status`` is GitPilot's *implementation* status only — never a Matrix
    verdict. A passing run is ``completed`` with ``test_status: passed``; it is
    NOT approval. Matrix Builder runs its own validation to approve/reject.
    """

    run_id: str
    status: str
    summary: str = ""
    diff_url: str | None = None
    logs_url: str | None = None
    test_status: str = "not_run"
    changed_files: list[str] = Field(default_factory=list)


# In-memory run registry. Single-process only (HF Space / local). A durable
# store (DB / object storage) replaces this when runs must survive restarts.
_RUNS: dict[str, dict[str, Any]] = {}
_RUNS_LOCK = threading.Lock()

# GitPilot implementation status -> result-sync status. Deliberately never maps
# to "approved": Matrix approval is Matrix Builder's call, not GitPilot's.
_TERMINAL_STATUS = {
    "ok": "completed",
    "blocked": "blocked",
    "error": "error",
    "needs_approval": "needs_approval",
}


def _coder_demo() -> bool:
    """Default to deterministic offline previews unless explicitly disabled."""
    v = os.getenv("GITPILOT_CODER_DEMO")
    if v is None:
        return True
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _test_status_from(sandbox_result: dict[str, Any] | None) -> str:
    """Map a sandbox result to passed / failed / skipped."""
    if not sandbox_result:
        return "skipped"
    for key in ("passed", "ok", "success"):
        if sandbox_result.get(key) is True:
            return "passed"
        if sandbox_result.get(key) is False:
            return "failed"
    status = str(sandbox_result.get("status", "")).lower()
    if status in {"passed", "ok", "success"}:
        return "passed"
    if status in {"failed", "error"}:
        return "failed"
    return "skipped"


def _execute_run(run_id: str) -> None:
    """Run the repair pipeline for a queued run and record its result.

    Runs in a background task. Never raises — failures are recorded on the run
    as ``status: error`` so the poller always gets a terminal answer.
    """
    with _RUNS_LOCK:
        rec = _RUNS.get(run_id)
        if rec is None:
            return
        rec["status"] = "running"
        plan = dict(rec["repair_request"])
        task_id = rec.get("task_id", run_id)
    try:
        resp = run_repair(RepairRequest.from_json(plan), demo_mode=_coder_demo())
        diff = resp.patch_preview or ""
        logs = "\n".join([*resp.messages, *(f"WARNING: {w}" for w in resp.warnings)])
        update = {
            "status": _TERMINAL_STATUS.get(resp.status, "completed"),
            "summary": resp.review or f"Implemented {task_id}",
            "diff": diff,
            "logs": logs,
            "test_status": _test_status_from(resp.sandbox_result),
            "changed_files": [cf.path for cf in resp.changed_files],
            "risk_level": resp.risk_level.value,
        }
    except Exception:  # pragma: no cover - defensive; no secrets leaked
        update = {"status": "error", "logs": "internal error running repair pipeline"}
    with _RUNS_LOCK:
        rec = _RUNS.get(run_id)
        if rec is not None:
            rec.update(update)


def _map_mode(mode: str) -> RepairMode:
    """Map a Matrix run mode onto a repair mode, conservatively.

    Unknown / ``ask`` / ``plan`` modes default to a safe ``dry_run``. ``apply``
    is honored only when explicitly requested; the repair pipeline itself still
    fails closed (empty allowed_paths blocks, high risk needs approval).
    """
    normalized = (mode or "").strip().lower()
    if normalized in {"apply", "auto"}:
        return RepairMode.apply
    if normalized in {"draft_pr", "pr"}:
        return RepairMode.draft_pr
    return RepairMode.dry_run


def merge_forbidden(caller_forbidden: list[str] | None) -> list[str]:
    """Forbidden set = caller forbidden + Matrix control files + pipeline defaults.

    De-duplicated and order-preserving. The Matrix control files are always
    present so GitPilot can never edit them — even if the caller "allows" them.
    """
    merged: list[str] = []
    for group in (caller_forbidden or [], MATRIX_CONTROL_FILES, DEFAULT_FORBIDDEN_PATHS):
        for path in group:
            if path not in merged:
                merged.append(path)
    return merged


def to_repair_request(run: MatrixRunRequest) -> RepairRequest:
    """Map the Matrix run contract onto the existing repair pipeline request."""
    issues: list[dict[str, Any]] = []
    if run.prompt.strip():
        issues.append(
            {
                "id": run.task_id or "matrix-task",
                "severity": "medium",
                "description": run.prompt.strip(),
                "recommended_action": run.prompt.strip(),
            }
        )
    return RepairRequest.from_json(
        {
            "client_id": "matrix-builder",
            "workspace_id": run.task_id or uuid.uuid4().hex,
            "task_id": run.task_id or uuid.uuid4().hex,
            # The signed Matrix Bundle URL is the source GitPilot fetches; later
            # batches resolve it into a workspace before the pipeline runs.
            "repo_url": run.bundle_url,
            "mode": _map_mode(run.mode).value,
            "issues": issues,
            "allowed_paths": list(run.allowed_files or []),
            "forbidden_paths": merge_forbidden(run.forbidden_files),
            "sandbox": {"provider": "matrixlab", "required": False},
        }
    )


def verify_a2a_secret(
    authorization: str | None = Header(default=None),
    x_a2a_secret: str | None = Header(default=None, alias="X-A2A-Secret"),
) -> None:
    """Gate the facade behind the A2A shared secret (single source of truth)."""
    _require_gateway_secret(authorization, x_a2a_secret)


def _base_url(request: Request) -> str:
    base = os.getenv("GITPILOT_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if base:
        return base
    return str(request.base_url).rstrip("/")


def _matrix_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "gitpilot-matrix",
        "auth_required": _env_bool("GITPILOT_A2A_REQUIRE_AUTH", True),
    }


def _create_run(
    run: MatrixRunRequest, request: Request, background_tasks: BackgroundTasks
) -> MatrixRunResponse:
    # Build the controlled repair request now so a malformed contract fails fast
    # before a run is ever queued. Matrix guardrails are applied here.
    repair_request = to_repair_request(run)
    run_id = f"gp-run-{uuid.uuid4().hex[:12]}"
    with _RUNS_LOCK:
        _RUNS[run_id] = {
            "run_id": run_id,
            "status": "queued",
            "bundle_url": run.bundle_url,
            "task_id": run.task_id,
            "validation_commands": list(run.validation_commands or []),
            "repair_request": repair_request.model_dump(mode="json"),
            "summary": "",
            "diff": "",
            "logs": "",
            "test_status": "not_run",
            "changed_files": [],
        }
    # Execute asynchronously: the caller gets a queued run immediately and polls
    # GET /runs/{run_id} for the result.
    background_tasks.add_task(_execute_run, run_id)
    url = f"{_base_url(request)}/api/v1/gitpilot/runs/{run_id}"
    return MatrixRunResponse(run_id=run_id, status="queued", url=url)


def _require_run(run_id: str) -> dict[str, Any]:
    with _RUNS_LOCK:
        rec = _RUNS.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run not found")
    return rec


def _get_run(run_id: str, request: Request) -> MatrixRunStatus:
    rec = _require_run(run_id)
    base = _base_url(request)
    return MatrixRunStatus(
        run_id=run_id,
        status=rec["status"],
        summary=rec.get("summary", ""),
        diff_url=f"{base}/api/v1/gitpilot/runs/{run_id}/diff" if rec.get("diff") else None,
        logs_url=f"{base}/api/v1/gitpilot/runs/{run_id}/logs" if rec.get("logs") else None,
        test_status=rec.get("test_status", "not_run"),
        changed_files=list(rec.get("changed_files", [])),
    )


def _repair_run(
    run_id: str, repair: MatrixRepairRequest, request: Request, background_tasks: BackgroundTasks
) -> MatrixRunResponse:
    """Re-run an existing run with repair findings, inside the same contract."""
    original = _require_run(run_id)
    plan = dict(original["repair_request"])

    issues: list[dict[str, Any]] = [
        {
            "id": f"finding-{i + 1}",
            "severity": "medium",
            "description": finding,
            "recommended_action": finding,
        }
        for i, finding in enumerate(repair.validation_findings)
    ]
    if repair.repair_prompt.strip():
        issues.append(
            {
                "id": "repair-prompt",
                "severity": "medium",
                "description": repair.repair_prompt.strip(),
                "recommended_action": repair.repair_prompt.strip(),
            }
        )
    plan["issues"] = issues or plan.get("issues", [])
    if repair.allowed_files:
        plan["allowed_paths"] = list(repair.allowed_files)
    # Always re-assert the Matrix control files as forbidden.
    plan["forbidden_paths"] = merge_forbidden(
        repair.forbidden_files or plan.get("forbidden_paths", [])
    )

    new_run_id = f"gp-run-{uuid.uuid4().hex[:12]}"
    with _RUNS_LOCK:
        _RUNS[new_run_id] = {
            "run_id": new_run_id,
            "status": "queued",
            "bundle_url": original.get("bundle_url", ""),
            "task_id": original.get("task_id", new_run_id),
            "validation_commands": list(original.get("validation_commands", [])),
            "repair_request": plan,
            "parent_run_id": run_id,
            "summary": "",
            "diff": "",
            "logs": "",
            "test_status": "not_run",
            "changed_files": [],
        }
    background_tasks.add_task(_execute_run, new_run_id)
    url = f"{_base_url(request)}/api/v1/gitpilot/runs/{new_run_id}"
    return MatrixRunResponse(run_id=new_run_id, status="queued", url=url)


def _create_pr(run_id: str, pr: MatrixPrRequest) -> MatrixPrResponse:
    """Open a PR for a run's diff (Batch 11).

    Uses the repair pipeline's DraftPRWriter. In the first wave this is a safe
    stub that does not call a Git host; it returns a draft PR reference so the
    flow is demonstrable. Real PR creation is enabled per-deployment behind
    GITPILOT_DRAFT_PR_ENABLED + Git host credentials.
    """
    rec = _require_run(run_id)
    repo_url = (pr.repo_url or rec.get("bundle_url", "")).rstrip("/")
    title = pr.title or f"GitPilot: {rec.get('task_id', run_id)}"
    result = DraftPRWriter().create_draft_pr(
        repo_url=repo_url,
        branch=f"gitpilot/{rec.get('task_id', run_id)}",
        title=title,
        body=rec.get("summary", ""),
        base=pr.base or "main",
    )
    if result.url:
        return MatrixPrResponse(
            run_id=run_id, pr_url=result.url, status="created", message=result.message
        )
    if not repo_url:
        return MatrixPrResponse(run_id=run_id, status="no_repo", message="No repo_url for the PR.")
    # Draft stub: synthesize a reference so the UI has a link; clearly a draft.
    status = "draft" if draft_pr_enabled() else "disabled"
    pr_url = f"{repo_url}/pull/draft-{run_id[-6:]}" if repo_url.startswith("http") else None
    return MatrixPrResponse(run_id=run_id, pr_url=pr_url, status=status, message=result.message)


def _get_run_diff(run_id: str) -> PlainTextResponse:
    rec = _require_run(run_id)
    return PlainTextResponse(rec.get("diff", ""))


def _get_run_logs(run_id: str) -> PlainTextResponse:
    rec = _require_run(run_id)
    return PlainTextResponse(rec.get("logs", ""))


def _register_routes(router: APIRouter) -> None:
    """Register the health + runs routes on *router* (shared by both prefixes)."""
    router.add_api_route("/health", _matrix_health, methods=["GET"])
    router.add_api_route(
        "/runs",
        _create_run,
        methods=["POST"],
        response_model=MatrixRunResponse,
        dependencies=[Depends(verify_a2a_secret)],
    )
    router.add_api_route(
        "/runs/{run_id}",
        _get_run,
        methods=["GET"],
        response_model=MatrixRunStatus,
        dependencies=[Depends(verify_a2a_secret)],
    )
    router.add_api_route(
        "/runs/{run_id}/repair",
        _repair_run,
        methods=["POST"],
        response_model=MatrixRunResponse,
        dependencies=[Depends(verify_a2a_secret)],
    )
    router.add_api_route(
        "/runs/{run_id}/pr",
        _create_pr,
        methods=["POST"],
        response_model=MatrixPrResponse,
        dependencies=[Depends(verify_a2a_secret)],
    )
    router.add_api_route(
        "/runs/{run_id}/diff",
        _get_run_diff,
        methods=["GET"],
        dependencies=[Depends(verify_a2a_secret)],
    )
    router.add_api_route(
        "/runs/{run_id}/logs",
        _get_run_logs,
        methods=["GET"],
        dependencies=[Depends(verify_a2a_secret)],
    )


def build_matrix_runs_router() -> APIRouter:
    """Build the canonical Matrix runs facade router (``/api/v1/gitpilot/*``)."""
    router = APIRouter(prefix="/api/v1/gitpilot", tags=["matrix"])
    _register_routes(router)
    return router


def build_matrix_runs_alias_router() -> APIRouter:
    """Build the local-bridge alias router (``/api/matrix/*``, same handlers).

    The web "Send to local GitPilot" bridge POSTs to ``/api/matrix/runs`` per the
    Phase 2 contract; this exposes the identical facade under that namespace.
    """
    router = APIRouter(prefix="/api/matrix", tags=["matrix"])
    _register_routes(router)
    return router
