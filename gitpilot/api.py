# gitpilot/api.py
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query, Path as FPath, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .version import __version__
from .github_api import (
    list_user_repos,
    list_user_repos_paginated,  # Pagination support
    search_user_repos,  # Search across all repos
    get_repo_tree,
    get_file,
    put_file,
    execution_context,
    github_request,
)
from .github_app import check_repo_write_access
from .settings import AppSettings, get_settings, set_provider, update_settings, LLMProvider
from .agentic import generate_plan, execute_plan, PlanResult, get_flow_definition
from .github_oauth import (
    generate_authorization_url,
    exchange_code_for_token,
    validate_token,
    initiate_device_flow,
    poll_device_token,
    AuthSession,
    GitHubUser,
)
from .auth_store import save_session, load_session, clear_session
import os
import logging
from .model_catalog import list_models_for_provider

# Optional A2A adapter (MCP ContextForge)
from .a2a_adapter import router as a2a_router

logger = logging.getLogger(__name__)

app = FastAPI(
    title="GitPilot API",
    version=__version__,
    description="Agentic AI assistant for GitHub repositories.",
)

# ==========================================================================
# Optional A2A Adapter (MCP ContextForge)
# ==========================================================================
# This is feature-flagged and does not affect the existing UI/REST API unless
# explicitly enabled.
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


if _env_bool("GITPILOT_ENABLE_A2A", False):
    logger.info("A2A adapter enabled (mounting /a2a/* endpoints)")
    app.include_router(a2a_router)
else:
    logger.info("A2A adapter disabled (set GITPILOT_ENABLE_A2A=true to enable)")

# ============================================================================
# CORS Configuration
# ============================================================================
# Enable CORS to allow frontend (local dev or Vercel) to connect to backend
allowed_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:5173")
allowed_origins = [origin.strip() for origin in allowed_origins_str.split(",")]

logger.info(f"CORS enabled for origins: {allowed_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_github_token(authorization: Optional[str] = Header(None)) -> Optional[str]:
    """
    Extract GitHub token from Authorization header.

    Supports formats:
    - Bearer <token>
    - token <token>
    - <token>
    """
    if not authorization:
        return None

    if authorization.startswith("Bearer "):
        return authorization[7:]
    elif authorization.startswith("token "):
        return authorization[6:]
    else:
        return authorization


# --- FIXED: Added default_branch to model ---
class RepoSummary(BaseModel):
    id: int
    name: str
    full_name: str
    private: bool
    owner: str
    default_branch: str = "main"  # <--- CRITICAL FIX: Defaults to main, but can be master/dev


class PaginatedReposResponse(BaseModel):
    """Response model for paginated repository listing."""
    repositories: List[RepoSummary]
    page: int
    per_page: int
    total_count: Optional[int] = None
    has_more: bool
    query: Optional[str] = None


class FileEntry(BaseModel):
    path: str
    type: str


class FileTreeResponse(BaseModel):
    files: List[FileEntry] = Field(default_factory=list)


class FileContent(BaseModel):
    path: str
    encoding: str = "utf-8"
    content: str


class CommitRequest(BaseModel):
    path: str
    content: str
    message: str


class CommitResponse(BaseModel):
    path: str
    commit_sha: str
    commit_url: Optional[str] = None


class SettingsResponse(BaseModel):
    provider: LLMProvider
    providers: List[LLMProvider]
    openai: dict
    claude: dict
    watsonx: dict
    ollama: dict
    langflow_url: str
    has_langflow_plan_flow: bool


class ProviderModelsResponse(BaseModel):
    provider: LLMProvider
    models: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class ProviderUpdate(BaseModel):
    provider: LLMProvider


class ChatPlanRequest(BaseModel):
    repo_owner: str
    repo_name: str
    goal: str
    branch_name: Optional[str] = None


class ExecutePlanRequest(BaseModel):
    repo_owner: str
    repo_name: str
    plan: PlanResult
    branch_name: Optional[str] = None


class AuthUrlResponse(BaseModel):
    authorization_url: str
    state: str


class AuthCallbackRequest(BaseModel):
    code: str
    state: str


class TokenValidationRequest(BaseModel):
    access_token: str


class UserInfoResponse(BaseModel):
    user: GitHubUser
    authenticated: bool


class RepoAccessResponse(BaseModel):
    can_write: bool
    app_installed: bool
    auth_type: str


# ============================================================================
# Repository Endpoints - Enterprise Grade with Pagination & Search
# ============================================================================

@app.get("/api/repos", response_model=PaginatedReposResponse)
async def api_list_repos(
    query: Optional[str] = Query(None, description="Search query (searches across ALL repositories)"),
    page: int = Query(1, ge=1, description="Page number (starts at 1)"),
    per_page: int = Query(100, ge=1, le=100, description="Results per page (max 100)"),
    authorization: Optional[str] = Header(None),
):
    """
    List user repositories with enterprise-grade pagination and search.
    Includes default_branch information for correct frontend routing.
    """
    token = get_github_token(authorization)

    try:
        if query:
            # SEARCH MODE: Search across ALL repositories
            result = await search_user_repos(
                query=query,
                page=page,
                per_page=per_page,
                token=token
            )
        else:
            # PAGINATION MODE: Return repos page by page
            result = await list_user_repos_paginated(
                page=page,
                per_page=per_page,
                token=token
            )

        # --- FIXED: Mapping default_branch ---
        repos = [
            RepoSummary(
                id=r["id"],
                name=r["name"],
                full_name=r["full_name"],
                private=r["private"],
                owner=r["owner"],
                default_branch=r.get("default_branch", "main"),  # <--- CRITICAL FIX
            )
            for r in result["repositories"]
        ]

        return PaginatedReposResponse(
            repositories=repos,
            page=result["page"],
            per_page=result["per_page"],
            total_count=result.get("total_count"),
            has_more=result["has_more"],
            query=query,
        )

    except Exception as e:
        logging.exception("Error fetching repositories")
        return JSONResponse(
            content={
                "error": f"Failed to fetch repositories: {str(e)}",
                "repositories": [],
                "page": page,
                "per_page": per_page,
                "has_more": False,
            },
            status_code=500
        )


@app.get("/api/repos/all")
async def api_list_all_repos(
    query: Optional[str] = Query(None, description="Search query"),
    authorization: Optional[str] = Header(None),
):
    """
    Fetch ALL user repositories at once (no pagination).
    Useful for quick searches, but paginated endpoint is preferred.
    """
    token = get_github_token(authorization)

    try:
        # Fetch all repositories (this will make multiple API calls)
        all_repos = []
        page = 1
        max_pages = 15  # Safety limit: 1500 repos max (15 * 100)

        while page <= max_pages:
            result = await list_user_repos_paginated(
                page=page,
                per_page=100,
                token=token
            )

            all_repos.extend(result["repositories"])

            if not result["has_more"]:
                break

            page += 1

        # Filter by query if provided
        if query:
            query_lower = query.lower()
            all_repos = [
                r for r in all_repos
                if query_lower in r["name"].lower() or query_lower in r["full_name"].lower()
            ]

        # --- FIXED: Mapping default_branch ---
        repos = [
            RepoSummary(
                id=r["id"],
                name=r["name"],
                full_name=r["full_name"],
                private=r["private"],
                owner=r["owner"],
                default_branch=r.get("default_branch", "main"),  # <--- CRITICAL FIX
            )
            for r in all_repos
        ]

        return {
            "repositories": repos,
            "total_count": len(repos),
            "query": query,
        }

    except Exception as e:
        logging.exception("Error fetching all repositories")
        return JSONResponse(
            content={"error": f"Failed to fetch repositories: {str(e)}"},
            status_code=500
        )


@app.get("/api/repos/{owner}/{repo}/tree", response_model=FileTreeResponse)
async def api_repo_tree(
    owner: str = FPath(...),
    repo: str = FPath(...),
    ref: Optional[str] = Query(
        None,
        description="Git reference (branch, tag, or commit SHA). If omitted, defaults to HEAD.",
    ),
    authorization: Optional[str] = Header(None),
):
    """
    Get the file tree for a repository.
    Handles 'main' vs 'master' discrepancies and empty repositories gracefully.
    """
    token = get_github_token(authorization)

    # Keep legacy behavior: missing/empty ref behaves like HEAD.
    ref_value = (ref or "").strip() or "HEAD"

    try:
        tree = await get_repo_tree(owner, repo, token=token, ref=ref_value)
        return FileTreeResponse(files=[FileEntry(**f) for f in tree])

    except HTTPException as e:
        if e.status_code == 409:
            return FileTreeResponse(files=[])

        if e.status_code == 404:
            return JSONResponse(
                status_code=404,
                content={
                    "detail": f"Ref '{ref_value}' not found. The repository might be using a different default branch (e.g., 'master')."
                }
            )

        raise e


@app.get("/api/repos/{owner}/{repo}/file", response_model=FileContent)
async def api_get_file(
    owner: str = FPath(...),
    repo: str = FPath(...),
    path: str = Query(...),
    authorization: Optional[str] = Header(None),
):
    token = get_github_token(authorization)
    content = await get_file(owner, repo, path, token=token)
    return FileContent(path=path, content=content)


@app.post("/api/repos/{owner}/{repo}/file", response_model=CommitResponse)
async def api_put_file(
    owner: str = FPath(...),
    repo: str = FPath(...),
    payload: CommitRequest = ...,
    authorization: Optional[str] = Header(None),
):
    token = get_github_token(authorization)
    result = await put_file(
        owner, repo, payload.path, payload.content, payload.message, token=token
    )
    return CommitResponse(**result)


# ============================================================================
# Settings Endpoints
# ============================================================================

@app.get("/api/settings", response_model=SettingsResponse)
async def api_get_settings():
    s: AppSettings = get_settings()
    return SettingsResponse(
        provider=s.provider,
        providers=[LLMProvider.openai, LLMProvider.claude, LLMProvider.watsonx, LLMProvider.ollama],
        openai=s.openai.model_dump(),
        claude=s.claude.model_dump(),
        watsonx=s.watsonx.model_dump(),
        ollama=s.ollama.model_dump(),
        langflow_url=s.langflow_url,
        has_langflow_plan_flow=bool(s.langflow_plan_flow_id),
    )


@app.get("/api/settings/models", response_model=ProviderModelsResponse)
async def api_list_models(provider: Optional[LLMProvider] = Query(None)):
    """
    Return the list of LLM models available for a provider.

    If 'provider' is not given, use the currently active provider from settings.
    """
    s: AppSettings = get_settings()
    effective_provider = provider or s.provider

    models, error = list_models_for_provider(effective_provider, s)

    return ProviderModelsResponse(
        provider=effective_provider,
        models=models,
        error=error,
    )


@app.post("/api/settings/provider", response_model=SettingsResponse)
async def api_set_provider(update: ProviderUpdate):
    s = set_provider(update.provider)
    return SettingsResponse(
        provider=s.provider,
        providers=[LLMProvider.openai, LLMProvider.claude, LLMProvider.watsonx, LLMProvider.ollama],
        openai=s.openai.model_dump(),
        claude=s.claude.model_dump(),
        watsonx=s.watsonx.model_dump(),
        ollama=s.ollama.model_dump(),
        langflow_url=s.langflow_url,
        has_langflow_plan_flow=bool(s.langflow_plan_flow_id),
    )


@app.put("/api/settings/llm", response_model=SettingsResponse)
async def api_update_llm_settings(updates: dict):
    """Update full LLM settings including provider-specific configs."""
    s = update_settings(updates)
    return SettingsResponse(
        provider=s.provider,
        providers=[LLMProvider.openai, LLMProvider.claude, LLMProvider.watsonx, LLMProvider.ollama],
        openai=s.openai.model_dump(),
        claude=s.claude.model_dump(),
        watsonx=s.watsonx.model_dump(),
        ollama=s.ollama.model_dump(),
        langflow_url=s.langflow_url,
        has_langflow_plan_flow=bool(s.langflow_plan_flow_id),
    )


# ============================================================================
# Chat Endpoints
# ============================================================================

@app.post("/api/chat/plan", response_model=PlanResult)
async def api_chat_plan(req: ChatPlanRequest, authorization: Optional[str] = Header(None)):
    token = get_github_token(authorization)

    # ✅ Added logging for branch_name received
    logger.info(
        "PLAN REQUEST: %s/%s | branch_name=%r",
        req.repo_owner,
        req.repo_name,
        req.branch_name,
    )

    with execution_context(token, ref=req.branch_name):  # ✅ set ref context
        full_name = f"{req.repo_owner}/{req.repo_name}"
        plan = await generate_plan(req.goal, full_name, token=token, branch_name=req.branch_name)
        return plan


@app.post("/api/chat/execute")
async def api_chat_execute(
    req: ExecutePlanRequest,
    authorization: Optional[str] = Header(None)
):
    token = get_github_token(authorization)

    # ✅ FIX: use execution_context(token, ref=req.branch_name) so tool calls that rely on context
    # never accidentally run on HEAD/default when branch_name is provided.
    with execution_context(token, ref=req.branch_name):
        full_name = f"{req.repo_owner}/{req.repo_name}"
        result = await execute_plan(
            req.plan, full_name, token=token, branch_name=req.branch_name
        )
        if isinstance(result, dict):
            result.setdefault(
                "mode",
                "sticky" if req.branch_name else "hard-switch",
            )
        return result


@app.get("/api/flow/current")
async def api_get_flow():
    """Return the current agent flow definition as a graph."""
    flow = await get_flow_definition()
    return flow


# ============================================================================
# Authentication Endpoints (Web Flow + Device Flow)
# ============================================================================

@app.get("/api/auth/url", response_model=AuthUrlResponse)
async def api_get_auth_url():
    """
    Generate GitHub OAuth authorization URL (Web Flow).
    Requires Client Secret to be configured.
    """
    auth_url, state = generate_authorization_url()
    return AuthUrlResponse(authorization_url=auth_url, state=state)


@app.post("/api/auth/callback", response_model=AuthSession)
async def api_auth_callback(request: AuthCallbackRequest):
    """
    Handle GitHub OAuth callback (Web Flow).
    Exchange the authorization code for an access token.
    """
    try:
        session = await exchange_code_for_token(request.code, request.state)

        # Persist session for local deployments (best-effort)
        try:
            save_session(session.model_dump() if hasattr(session, "model_dump") else session.dict())
        except Exception as e:
            logger.warning(f"Failed to persist auth session: {e}")

        return session
    except ValueError as e:
        return JSONResponse(
            {"error": str(e)},
            status_code=400,
        )


@app.post("/api/auth/validate", response_model=UserInfoResponse)
async def api_validate_token(request: TokenValidationRequest):
    """
    Validate a GitHub access token and return user information.
    """
    user = await validate_token(request.access_token)
    if user:
        return UserInfoResponse(user=user, authenticated=True)
    return UserInfoResponse(
        user=GitHubUser(login="", id=0, avatar_url=""),
        authenticated=False,
    )


@app.post("/api/auth/device/code")
async def api_device_code():
    """
    Start the device login flow (Step 1).
    Does NOT require a client secret.
    """
    try:
        data = await initiate_device_flow()
        return data
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/auth/device/poll")
async def api_device_poll(payload: dict):
    """
    Poll GitHub to check if user authorized the device (Step 2).
    """
    device_code = payload.get("device_code")
    if not device_code:
        return JSONResponse({"error": "Missing device_code"}, status_code=400)

    try:
        session = await poll_device_token(device_code)
        if session:
            # Persist session for local deployments (best-effort)
            try:
                save_session(session.model_dump() if hasattr(session, "model_dump") else session.dict())
            except Exception as e:
                logger.warning(f"Failed to persist auth session: {e}")

            return session

        return JSONResponse({"status": "pending"}, status_code=202)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/auth/session", response_model=AuthSession)
async def api_auth_session():
    """Return persisted GitHub session if available (local deployments).

    This enables "login once" behavior for the UI.

    - If a cached token exists and is still valid, returns an AuthSession.
    - If missing/invalid/revoked, returns 404 (and clears the cache).
    """
    cached = load_session()
    if not cached or not cached.get("access_token"):
        raise HTTPException(status_code=404, detail="No cached session")

    user = await validate_token(str(cached["access_token"]))
    if not user:
        clear_session()
        raise HTTPException(status_code=404, detail="Cached session is invalid")

    # Keep the same shape as AuthSession returned by login flows.
    return AuthSession(
        access_token=str(cached["access_token"]),
        token_type=str(cached.get("token_type") or "bearer"),
        scope=str(cached.get("scope") or ""),
        user=user,
    )


@app.post("/api/auth/logout")
async def api_auth_logout():
    """Clear persisted GitHub session (local deployments)."""
    clear_session()
    return {"ok": True}


@app.get("/api/auth/status")
async def api_auth_status():
    """
    Smart check: Do we have a secret (Web Flow) or just ID (Device Flow)?
    This tells the frontend which UI to render.
    """
    has_secret = bool(os.getenv("GITHUB_CLIENT_SECRET"))
    has_id = bool(os.getenv("GITHUB_CLIENT_ID", "Iv23litmRp80Z6wmlyRn"))

    return {
        "mode": "web" if has_secret else "device",
        "configured": has_id,
        "oauth_configured": has_secret,
        "pat_configured": bool(os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")),
    }


@app.get("/api/auth/app-url")
async def api_get_app_url():
    """Get GitHub App installation URL."""
    app_slug = os.getenv("GITHUB_APP_SLUG", "gitpilota")
    app_url = f"https://github.com/apps/{app_slug}"
    return {
        "app_url": app_url,
        "app_slug": app_slug,
    }


@app.get("/api/auth/installation-status")
async def api_check_installation_status():
    """Check if GitHub App is installed for the current user."""
    pat_token = os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")

    if pat_token:
        user = await validate_token(pat_token)
        if user:
            return {
                "installed": True,
                "access_token": pat_token,
                "user": user,
                "auth_type": "pat",
            }

    github_app_id = os.getenv("GITHUB_APP_ID", "2313985")
    if not github_app_id:
        return {
            "installed": False,
            "message": "GitHub authentication not configured.",
            "auth_type": "none",
        }

    return {
        "installed": False,
        "message": "GitHub App not installed.",
        "auth_type": "github_app",
    }


@app.get("/api/auth/repo-access", response_model=RepoAccessResponse)
async def api_check_repo_access(
    owner: str = Query(...),
    repo: str = Query(...),
    authorization: Optional[str] = Header(None),
):
    """
    Check if we have write access to a repository via User token or GitHub App.

    This endpoint helps the frontend determine if it should show
    installation prompts or if the user already has sufficient permissions.
    """
    token = get_github_token(authorization)
    access_info = await check_repo_write_access(owner, repo, user_token=token)

    return RepoAccessResponse(
        can_write=access_info["can_write"],
        app_installed=access_info["app_installed"],
        auth_type=access_info["auth_type"],
    )


# ============================================================================
# Static Files & Frontend Serving (SPA Support)
# ============================================================================

STATIC_DIR = Path(__file__).resolve().parent / "web"
ASSETS_DIR = STATIC_DIR / "assets"

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/health")
async def health_check():
    """Health check endpoint for monitoring and diagnostics."""
    return {"status": "healthy", "service": "gitpilot-backend"}


@app.get("/healthz")
async def healthz():
    """Health check endpoint (Render/Kubernetes standard)."""
    return {"status": "healthy", "service": "gitpilot-backend"}


@app.get("/", include_in_schema=False)
async def index():
    """Serve the React App entry point."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse(
        {"message": "GitPilot UI not built. The static files directory is missing."},
        status_code=500,
    )


@app.get("/{full_path:path}", include_in_schema=False)
async def catch_all_spa_routes(full_path: str):
    """
    Catch-all route to serve index.html for frontend routing.
    Excludes '/api' paths to ensure genuine API 404s are returned as JSON.
    """
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)

    return JSONResponse(
        {"message": "GitPilot UI not built. The static files directory is missing."},
        status_code=500,
    )
