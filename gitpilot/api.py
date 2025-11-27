from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query, Path as FPath, Header
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .version import __version__
from .github_api import (
    list_user_repos, 
    get_repo_tree, 
    get_file, 
    put_file, 
    execution_context  # Imported Context Manager
)
from .github_app import check_repo_write_access  # NEW: GitHub App integration
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
import os  # if not already imported
import requests  # if not already imported
from .model_catalog import list_models_for_provider


app = FastAPI(
    title="GitPilot API",
    version=__version__,
    description="Agentic AI assistant for GitHub repositories.",
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

    # Remove "Bearer " or "token " prefix if present
    if authorization.startswith("Bearer "):
        return authorization[7:]
    elif authorization.startswith("token "):
        return authorization[6:]
    else:
        return authorization


class RepoSummary(BaseModel):
    id: int
    name: str
    full_name: str
    private: bool
    owner: str


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


class ExecutePlanRequest(BaseModel):
    repo_owner: str
    repo_name: str
    plan: PlanResult


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


@app.get("/api/repos", response_model=List[RepoSummary])
async def api_list_repos(
    query: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
):
    token = get_github_token(authorization)
    data = await list_user_repos(query=query, token=token)
    return [
        RepoSummary(
            id=r["id"],
            name=r["name"],
            full_name=r["full_name"],
            private=r["private"],
            owner=r["owner"],
        )
        for r in data
    ]


@app.get("/api/repos/{owner}/{repo}/tree", response_model=FileTreeResponse)
async def api_repo_tree(
    owner: str = FPath(...),
    repo: str = FPath(...),
    authorization: Optional[str] = Header(None),
):
    token = get_github_token(authorization)
    tree = await get_repo_tree(owner, repo, token=token)
    return FileTreeResponse(files=[FileEntry(**f) for f in tree])


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


# --- UPDATED CHAT ENDPOINTS TO USE CONTEXT ---

@app.post("/api/chat/plan", response_model=PlanResult)
async def api_chat_plan(
    req: ChatPlanRequest,
    authorization: Optional[str] = Header(None)
):
    token = get_github_token(authorization)
    # Inject token into context so tools called deep in 'generate_plan' can use it
    with execution_context(token):
        full_name = f"{req.repo_owner}/{req.repo_name}"
        # FIX: Pass token explicitly to ensure it propagates to tools in threads
        plan = await generate_plan(req.goal, full_name, token=token)
        return plan


@app.post("/api/chat/execute")
async def api_chat_execute(
    req: ExecutePlanRequest,
    authorization: Optional[str] = Header(None)
):
    token = get_github_token(authorization)
    # Inject token into context so tools called deep in 'execute_plan' can use it
    with execution_context(token):
        full_name = f"{req.repo_owner}/{req.repo_name}"
        # FIX: Pass token explicitly to ensure it propagates to tools in threads
        result = await execute_plan(req.plan, full_name, token=token)
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


# --- Device Flow Endpoints ---

@app.post("/api/auth/device/code")
async def api_device_code():
    """
    Start the device login flow (Step 1).
    Does NOT require a client secret.
    """
    try:
        data = await initiate_device_flow()
        return data  # Returns { device_code, user_code, verification_uri, interval, ... }
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
            return session # Success!
        
        # 202 Accepted indicates "Pending - Keep Polling"
        return JSONResponse({"status": "pending"}, status_code=202)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/auth/status")
async def api_auth_status():
    """
    Smart check: Do we have a secret (Web Flow) or just ID (Device Flow)?
    This tells the frontend which UI to render.
    """
    import os
    has_secret = bool(os.getenv("GITHUB_CLIENT_SECRET"))
    # Default Client ID is provided for convenience if environment var is missing
    has_id = bool(os.getenv("GITHUB_CLIENT_ID", "Iv23litmRp80Z6wmlyRn"))
    
    return {
        "mode": "web" if has_secret else "device", 
        "configured": has_id,
        "oauth_configured": has_secret, # Legacy compat
        "pat_configured": bool(os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")),
    }


@app.get("/api/auth/app-url")
async def api_get_app_url():
    """Get GitHub App installation URL."""
    import os
    app_slug = os.getenv("GITHUB_APP_SLUG", "gitpilota")
    app_url = f"https://github.com/apps/{app_slug}"
    return {
        "app_url": app_url,
        "app_slug": app_slug,
    }


@app.get("/api/auth/installation-status")
async def api_check_installation_status():
    """Check if GitHub App is installed for the current user."""
    import os
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


# NEW: Repository write access check endpoint
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

# 1. Mount assets explicitly (Fixes React/Vite loading issues)
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

# 2. Mount static folder generic
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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

# 3. Catch-All Route for SPA Routing (Must be last)
# This allows paths like /login or /workspace to work on refresh
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