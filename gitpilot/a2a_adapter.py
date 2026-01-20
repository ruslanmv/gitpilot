"""Optional A2A adapter for GitPilot (MCP ContextForge compatible).

This module is feature-flagged. Nothing changes in GitPilot unless the main app
mounts this router when GITPILOT_ENABLE_A2A=true.

Supported protocols
- JSON-RPC 2.0 (preferred)
- ContextForge custom A2A envelope (fallback)

Security model (recommended)
- Gateway injects a shared secret:
    X-A2A-Secret: <secret>
  or
    Authorization: Bearer <secret>

- GitHub token (if needed) should be provided via:
    X-Github-Token: <token>
  (avoid passing tokens in JSON bodies to reduce leak risk in logs)

Environment
- GITPILOT_A2A_REQUIRE_AUTH=true
- GITPILOT_A2A_SHARED_SECRET=<long random>
- GITPILOT_A2A_MAX_BODY_MB=2
- GITPILOT_A2A_ALLOW_GITHUB_TOKEN_IN_PARAMS=false
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .agentic import PlanResult, execute_plan, generate_plan
from .github_api import get_file, get_repo_tree, github_request, put_file

router = APIRouter(tags=["a2a"])


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


def _extract_bearer(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if value.startswith("Bearer "):
        return value[7:]
    if value.startswith("token "):
        return value[6:]
    return value


def _get_trace_id(x_request_id: Optional[str]) -> str:
    return (x_request_id or "").strip() or str(uuid.uuid4())


def _require_gateway_secret(authorization: Optional[str], x_a2a_secret: Optional[str]) -> None:
    require_auth = _env_bool("GITPILOT_A2A_REQUIRE_AUTH", True)
    if not require_auth:
        return

    expected = os.getenv("GITPILOT_A2A_SHARED_SECRET", "").strip()
    if not expected:
        raise HTTPException(
            status_code=500,
            detail="A2A is enabled but GITPILOT_A2A_SHARED_SECRET is not set",
        )

    candidate = _extract_bearer(authorization) or (x_a2a_secret or "").strip()
    if not candidate or candidate != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _split_full_name(repo_full_name: str) -> Tuple[str, str]:
    if not repo_full_name or "/" not in repo_full_name:
        raise HTTPException(status_code=400, detail="repo_full_name must be 'owner/repo'")
    owner, repo = repo_full_name.split("/", 1)
    owner, repo = owner.strip(), repo.strip()
    if not owner or not repo:
        raise HTTPException(status_code=400, detail="repo_full_name must be 'owner/repo'")
    return owner, repo


def _jsonrpc_error(id_value: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "error": err, "id": id_value}


def _jsonrpc_result(id_value: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "result": result, "id": id_value}


async def _dispatch(method: str, params: Dict[str, Any], github_token: Optional[str]) -> Any:
    if method == "repo.connect":
        repo_full_name = params.get("repo_full_name")
        owner, repo = _split_full_name(str(repo_full_name))
        info = await github_request(f"/repos/{owner}/{repo}", token=github_token)
        return {
            "repo": {
                "id": info.get("id"),
                "full_name": info.get("full_name"),
                "private": info.get("private"),
                "html_url": info.get("html_url"),
            },
            "default_branch": info.get("default_branch"),
            "permissions": info.get("permissions"),
        }

    if method == "repo.tree":
        repo_full_name = params.get("repo_full_name")
        ref = (params.get("ref") or "").strip() or "HEAD"
        owner, repo = _split_full_name(str(repo_full_name))
        tree = await get_repo_tree(owner, repo, token=github_token, ref=ref)
        return {"entries": tree, "ref": ref}

    if method == "repo.read":
        repo_full_name = params.get("repo_full_name")
        path = params.get("path")
        if not path:
            raise HTTPException(status_code=400, detail="Missing required param: path")
        owner, repo = _split_full_name(str(repo_full_name))
        # NOTE: current get_file() reads from default branch/ref in this repo.
        # You can extend github_api.get_file to accept ref and pass it here later.
        content = await get_file(owner, repo, str(path), token=github_token)
        return {"path": str(path), "content": content, "encoding": "utf-8"}

    if method == "repo.write":
        repo_full_name = params.get("repo_full_name")
        path = params.get("path")
        content = params.get("content")
        message = params.get("message") or "Update via GitPilot A2A"
        branch = params.get("branch") or params.get("branch_name")
        if not path:
            raise HTTPException(status_code=400, detail="Missing required param: path")
        if content is None:
            raise HTTPException(status_code=400, detail="Missing required param: content")
        owner, repo = _split_full_name(str(repo_full_name))
        result = await put_file(
            owner,
            repo,
            str(path),
            str(content),
            str(message),
            token=github_token,
            branch=branch,
        )
        return result

    if method == "plan.generate":
        repo_full_name = params.get("repo_full_name")
        goal = params.get("goal")
        branch_name = params.get("branch") or params.get("branch_name")
        if not goal:
            raise HTTPException(status_code=400, detail="Missing required param: goal")
        if not repo_full_name:
            raise HTTPException(status_code=400, detail="Missing required param: repo_full_name")
        plan = await generate_plan(str(goal), str(repo_full_name), token=github_token, branch_name=branch_name)
        return plan.model_dump() if hasattr(plan, "model_dump") else plan

    if method == "plan.execute":
        repo_full_name = params.get("repo_full_name")
        branch_name = params.get("branch") or params.get("branch_name")
        plan_raw = params.get("plan")
        if not repo_full_name:
            raise HTTPException(status_code=400, detail="Missing required param: repo_full_name")
        if plan_raw is None:
            raise HTTPException(status_code=400, detail="Missing required param: plan")
        if isinstance(plan_raw, PlanResult):
            plan_obj = plan_raw
        else:
            try:
                plan_obj = PlanResult.model_validate(plan_raw)  # pydantic v2
            except Exception:
                plan_obj = PlanResult.parse_obj(plan_raw)  # pydantic v1
        result = await execute_plan(plan_obj, str(repo_full_name), token=github_token, branch_name=branch_name)
        return result

    if method == "repo.search":
        query = params.get("query")
        if not query:
            raise HTTPException(status_code=400, detail="Missing required param: query")
        result = await github_request(
            "/search/repositories",
            params={"q": str(query), "per_page": 20},
            token=github_token,
        )
        items = (result or {}).get("items", []) if isinstance(result, dict) else []
        return {
            "repos": [
                {
                    "full_name": i.get("full_name"),
                    "private": i.get("private"),
                    "html_url": i.get("html_url"),
                    "description": i.get("description"),
                    "default_branch": i.get("default_branch"),
                }
                for i in items
            ]
        }

    raise HTTPException(status_code=404, detail=f"Unknown method: {method}")


@router.get("/a2a/health")
async def a2a_health() -> Dict[str, Any]:
    return {"status": "ok", "ts": int(time.time())}


@router.get("/a2a/manifest")
async def a2a_manifest() -> Dict[str, Any]:
    # Best-effort schemas (kept intentionally simple and stable)
    return {
        "name": "gitpilot",
        "a2a_version": "1.0",
        "protocols": ["jsonrpc-2.0", "a2a-envelope-1.0"],
        "auth": {"type": "shared_secret", "header": "X-A2A-Secret"},
        "rate_limits": {"hint": "apply gateway rate limiting; server enforces body size"},
        "methods": {
            "repo.connect": {
                "params": {"repo_full_name": "string"},
                "result": {"repo": "object", "default_branch": "string", "permissions": "object?"},
            },
            "repo.tree": {
                "params": {"repo_full_name": "string", "ref": "string?"},
                "result": {"entries": "array", "ref": "string"},
            },
            "repo.read": {
                "params": {"repo_full_name": "string", "path": "string"},
                "result": {"path": "string", "content": "string"},
            },
            "repo.write": {
                "params": {
                    "repo_full_name": "string",
                    "path": "string",
                    "content": "string",
                    "message": "string?",
                    "branch": "string?",
                },
                "result": "object",
            },
            "plan.generate": {
                "params": {"repo_full_name": "string", "goal": "string", "branch": "string?"},
                "result": "PlanResult",
            },
            "plan.execute": {
                "params": {"repo_full_name": "string", "plan": "PlanResult", "branch": "string?"},
                "result": "object",
            },
            "repo.search": {
                "params": {"query": "string"},
                "result": {"repos": "array"},
            },
        },
    }


async def _handle_invoke(
    request: Request,
    authorization: Optional[str],
    x_a2a_secret: Optional[str],
    x_github_token: Optional[str],
    x_request_id: Optional[str],
) -> JSONResponse:
    trace_id = _get_trace_id(x_request_id)
    _require_gateway_secret(authorization=authorization, x_a2a_secret=x_a2a_secret)

    # Body size guard (helps protect from abuse)
    max_mb = _env_int("GITPILOT_A2A_MAX_BODY_MB", 2)
    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > max_mb * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Request entity too large")
        except ValueError:
            pass

    started = time.time()
    payload = await request.json()

    github_token = _extract_bearer(x_github_token) or None
    if not github_token:
        github_token = _extract_bearer(authorization)

    # JSON-RPC mode
    if isinstance(payload, dict) and payload.get("jsonrpc") == "2.0" and "method" in payload:
        rpc_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            return JSONResponse(_jsonrpc_error(rpc_id, -32602, "Invalid params"), status_code=400)

        allow_in_params = _env_bool("GITPILOT_A2A_ALLOW_GITHUB_TOKEN_IN_PARAMS", False)
        if allow_in_params and not github_token:
            github_token = _extract_bearer(params.get("github_token"))

        try:
            result = await _dispatch(str(method), params, github_token)
            resp = _jsonrpc_result(rpc_id, result)
            return JSONResponse(resp, headers={"X-Trace-Id": trace_id})
        except HTTPException as e:
            resp = _jsonrpc_error(rpc_id, e.status_code, str(e.detail), {"trace_id": trace_id})
            return JSONResponse(resp, status_code=200, headers={"X-Trace-Id": trace_id})
        except Exception as e:
            resp = _jsonrpc_error(rpc_id, -32000, "Server error", {"trace_id": trace_id, "error": str(e)})
            return JSONResponse(resp, status_code=200, headers={"X-Trace-Id": trace_id})
        finally:
            _ = time.time() - started

    # Custom envelope fallback
    if isinstance(payload, dict) and payload.get("interaction_type"):
        interaction_type = str(payload.get("interaction_type"))
        parameters = payload.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise HTTPException(status_code=400, detail="Invalid parameters")

        if interaction_type == "query":
            repo_full_name = parameters.get("repo_full_name")
            goal = parameters.get("query") or parameters.get("goal")
            params = {
                "repo_full_name": repo_full_name,
                "goal": goal,
                "branch": parameters.get("branch") or parameters.get("branch_name"),
            }
            result = await _dispatch("plan.generate", params, github_token)
            return JSONResponse(
                {"response": result, "protocol_version": payload.get("protocol_version", "1.0")},
                headers={"X-Trace-Id": trace_id},
            )

        raise HTTPException(status_code=404, detail=f"Unsupported interaction_type: {interaction_type}")

    raise HTTPException(status_code=400, detail=f"Invalid A2A payload (trace_id={trace_id})")


@router.post("/a2a/invoke")
async def a2a_invoke(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_a2a_secret: Optional[str] = Header(None, alias="X-A2A-Secret"),
    x_github_token: Optional[str] = Header(None, alias="X-Github-Token"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
) -> JSONResponse:
    return await _handle_invoke(request, authorization, x_a2a_secret, x_github_token, x_request_id)


@router.post("/a2a/v1/invoke")
async def a2a_v1_invoke(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_a2a_secret: Optional[str] = Header(None, alias="X-A2A-Secret"),
    x_github_token: Optional[str] = Header(None, alias="X-Github-Token"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-Id"),
) -> JSONResponse:
    # Alias for versioned clients. Keep behavior identical to /a2a/invoke.
    return await _handle_invoke(request, authorization, x_a2a_secret, x_github_token, x_request_id)
