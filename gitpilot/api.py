# gitpilot/api.py
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Literal

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
    github_request,
    execution_context,
)
from .github_app import (
    is_github_app_configured,
    get_installation_for_repo,
)
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

    if authorization.startswith("Bearer "):
        return authorization[7:]
    if authorization.startswith("token "):
        return authorization[6:]
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


class RepoAccessStatus(BaseModel):
    # Is there some write-capable auth (user token or GitHub App)?
    installed: bool
    # Can we actually write to this repo?
    can_write: bool
    auth_type: Literal["user_token", "github_app", "none"]
    # Specifically: is the GitHub App installed on this repo?
    app_installed: bool = False


class SettingsResponse(BaseModel):
    provider: LLMProvider
    providers: List[LLMProvider]
    openai: dict
    claude: dict
    watsonx: dict
    ollama: dict
    langflow_url: str
    has_langflow_plan_flow: bool


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


# ============================================================================
# Repo Access Endpoint (User token vs GitHub App)
# ============================================================================


@app.get("/api/auth/repo-access", response_model=RepoAccessStatus)
async def api_repo_access(
    owner: str = Query(...),
    repo: str = Query(...),
    authorization: Optional[str] = Header(None),
):
    """
    Check how we can access a specific repository:
    - via user token (OAuth/PAT) with push permission
    - via GitHub App installation (installation token)
    - or not at all.
    """
    token = get_github_token(authorization)

    # 1) Try with the user's token: do we have push permission?
    if token:
        try:
            data = await github_request(f"/repos/{owner}/{repo}", token=token)
            perms = (data or {}).get("permissions") or {}
            if perms.get("push"):
                return RepoAccessStatus(
                    installed=True,
                    can_write=True,
                    auth_type="user_token",
                    app_installed=False,
                )
        except Exception:
            # ignore and fall-through to app check
            pass

    # 2) Try GitHub App – per-repo installation
    if is_github_app_configured():
        installation_id = await get_installation_for_repo(owner, repo)
        if installation_id:
            # App installed; we assume it has contents: read/write permission
            return RepoAccessStatus(
                installed=True,
                can_write=True,
                auth_type="github_app",
                app_installed=True,
            )
        else:
            # App configured on server, but not installed on this repo
            return RepoAccessStatus(
                installed=False,
                can_write=False,
                auth_type="github_app",
                app_installed=False,
            )

    # 3) Nothing configured
    return RepoAccessStatus(
        installed=False,
        can_write=False,
        auth_type="none",
        app_installed=False,
    )


# ============================================================================
# Repository Endpoints
# ============================================================================


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


# ============================================================================
# Settings Endpoints
# ============================================================================


@app.get("/api/settings", response_model=SettingsResponse)
async def api_get_settings():
    s: AppSettings = get_settings()
    return SettingsResponse(
        provider=s.provider,
        providers=[
            LLMProvider.openai,
            LLMProvider.claude,
            LLMProvider.watsonx,
            LLMProvider.ollama,
        ],
        openai=s.openai.model_dump(),
        claude=s.claude.model_dump(),
        watsonx=s.watsonx.model_dump(),
        ollama=s.ollama.model_dump(),
        langflow_url=s.langflow_url,
        has_langflow_plan_flow=bool(s.langflow_plan_flow_id),
    )


@app.post("/api/settings/provider", response_model=SettingsResponse)
async def api_set_provider(update: ProviderUpdate):
    s = set_provider(update.provider)
    return SettingsResponse(
        provider=s.provider,
        providers=[
            LLMProvider.openai,
            LLMProvider.claude,
            LLMProvider.watsonx,
            LLMProvider.ollama,
        ],
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
        providers=[
            LLMProvider.openai,
            LLMProvider.claude,
            LLMProvider.watsonx,
            LLMProvider.ollama,
        ],
        openai=s.openai.model_dump(),
        claude=s.claude.model_dump(),
        watsonx=s.watsonx.model_dump(),
        ollama=s.ollama.model_dump(),
        langflow_url=s.langflow_url,
        has_langflow_plan_flow=bool(s.langflow_plan_flow_id),
    )


# ============================================================================
# Chat & Agent Endpoints
# ============================================================================


@app.post("/api/chat/plan", response_model=PlanResult)
async def api_chat_plan(
    req: ChatPlanRequest,
    authorization: Optional[str] = Header(None),
):
    token = get_github_token(authorization)
    # Inject token into context so tools called deep in 'generate_plan' can use it
    with execution_context(token):
        full_name = f"{req.repo_owner}/{req.repo_name}"
        plan = await generate_plan(req.goal, full_name, token=token)
        return plan


@app.post("/api/chat/execute")
async def api_chat_execute(
    req: ExecutePlanRequest,
    authorization: Optional[str] = Header(None),
):
    token = get_github_token(authorization)
    # Inject token into context so tools called deep in 'execute_plan' can use it
    with execution_context(token):
        full_name = f"{req.repo_owner}/{req.repo_name}"
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
        return JSONResponse({"error": str(e)}, status_code=400)


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
            return session

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
    import os

    app_slug = os.getenv("GITHUB_APP_SLUG", "gitpilota")
    app_url = f"https://github.com/apps/{app_slug}"
    return {
        "app_url": app_url,
        "app_slug": app_slug,
    }


@app.get("/api/auth/installation-status")
async def api_check_installation_status():
    """Legacy endpoint: check if PAT or GitHub App is configured."""
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
