from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query, Path as FPath, Request, Response, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .version import __version__
from .github_api import list_user_repos, get_repo_tree, get_file, put_file
from .settings import AppSettings, get_settings, set_provider, update_settings, LLMProvider, GitHubAuthMode
from .agentic import generate_plan, execute_plan, PlanResult, get_flow_definition
from .auth import (
    session_store,
    get_oauth_config,
    is_oauth_configured,
    get_github_oauth_url,
    exchange_code_for_token,
    get_github_user,
    verify_github_token,
    create_session_token,
    create_csrf_token,
    set_session_cookie,
    clear_session_cookie,
    get_session_from_request,
    require_auth,
    UserSession,
)

app = FastAPI(
    title="GitPilota API",
    version=__version__,
    description="Agentic AI assistant for GitHub repositories.",
)


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
    github: dict
    openai: dict
    claude: dict
    watsonx: dict
    ollama: dict
    langflow_url: str
    has_langflow_plan_flow: bool
    setup_completed: bool


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


class AuthStatusResponse(BaseModel):
    authenticated: bool
    user: Optional[dict] = None
    setup_completed: bool


class UserResponse(BaseModel):
    user_id: int
    username: str
    email: Optional[str] = None
    avatar_url: Optional[str] = None


class GithubStatus(BaseModel):
    connected: bool
    mode: str  # "pat" or "app" or "none"
    app_installation_id: Optional[str] = None


class GithubAppInstallURL(BaseModel):
    url: str


class PATLoginRequest(BaseModel):
    token: str


# ============================================================================
# Authentication Endpoints
# ============================================================================

@app.get("/api/auth/status", response_model=AuthStatusResponse)
async def api_auth_status(request: Request):
    """Check if user is authenticated."""
    session = get_session_from_request(request)
    settings = get_settings()

    if session:
        return AuthStatusResponse(
            authenticated=True,
            user={
                "user_id": session.user_id,
                "username": session.username,
                "email": session.email,
                "avatar_url": session.avatar_url,
            },
            setup_completed=settings.setup_completed,
        )

    return AuthStatusResponse(
        authenticated=False,
        setup_completed=settings.setup_completed,
    )


@app.get("/api/auth/login")
async def api_auth_login():
    """Initiate GitHub OAuth login flow."""
    try:
        # Create CSRF token
        csrf_token = create_csrf_token()
        session_store.create_csrf_token(csrf_token)

        # Generate OAuth URL
        oauth_url = get_github_oauth_url(state=csrf_token)

        return {"url": oauth_url}
    except HTTPException as e:
        # OAuth not configured
        return JSONResponse(
            status_code=e.status_code,
            content={"error": e.detail}
        )


@app.post("/api/auth/login-pat")
async def api_auth_login_pat(request: Request, response: Response, payload: PATLoginRequest):
    """Login with GitHub Personal Access Token."""
    try:
        # Verify the token and get user info
        github_user = await verify_github_token(payload.token)

        # Create session
        session_id = create_session_token()
        user_session = UserSession(
            user_id=github_user["id"],
            username=github_user["login"],
            email=github_user.get("email"),
            avatar_url=github_user.get("avatar_url"),
            access_token=payload.token,
            auth_method="pat",
            created_at=time.time(),
            expires_at=time.time() + 86400 * 30,  # 30 days
        )

        session_store.create_session(session_id, user_session)

        # Set session cookie
        set_session_cookie(response, session_id)

        return {
            "success": True,
            "user": {
                "user_id": github_user["id"],
                "username": github_user["login"],
                "email": github_user.get("email"),
                "avatar_url": github_user.get("avatar_url"),
            }
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Login failed: {str(e)}")


@app.get("/api/auth/callback")
async def api_auth_callback(
    request: Request,
    response: Response,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    """Handle GitHub OAuth callback."""
    # Check for OAuth errors
    if error:
        return RedirectResponse(url=f"/?error={error}", status_code=302)

    if not code or not state:
        return RedirectResponse(url="/?error=missing_params", status_code=302)

    # Validate CSRF token
    if not session_store.validate_csrf_token(state):
        return RedirectResponse(url="/?error=invalid_state", status_code=302)

    try:
        # Exchange code for access token
        access_token = await exchange_code_for_token(code)

        # Get user information
        github_user = await get_github_user(access_token)

        # Create session
        session_id = create_session_token()
        user_session = UserSession(
            user_id=github_user["id"],
            username=github_user["login"],
            email=github_user.get("email"),
            avatar_url=github_user.get("avatar_url"),
            access_token=access_token,
            created_at=time.time(),
            expires_at=time.time() + 86400 * 30,  # 30 days
        )

        session_store.create_session(session_id, user_session)

        # Set session cookie
        set_session_cookie(response, session_id)

        # Redirect to main app
        return RedirectResponse(url="/", status_code=302)

    except HTTPException as e:
        return RedirectResponse(url=f"/?error={e.detail}", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/?error=auth_failed", status_code=302)


@app.post("/api/auth/logout")
async def api_auth_logout(request: Request, response: Response):
    """Logout user and clear session."""
    session_id = request.cookies.get("gitpilot_session")
    if session_id:
        session_store.delete_session(session_id)

    clear_session_cookie(response)
    return {"success": True}


@app.get("/api/auth/user", response_model=UserResponse)
async def api_get_current_user(session: UserSession = Depends(require_auth)):
    """Get current authenticated user."""
    return UserResponse(
        user_id=session.user_id,
        username=session.username,
        email=session.email,
        avatar_url=session.avatar_url,
    )


# ============================================================================
# GitHub Connection Status
# ============================================================================

@app.get("/api/github/status", response_model=GithubStatus)
async def api_github_status():
    """Get GitHub connection status."""
    settings = get_settings()
    github_config = settings.github

    # Check if PAT is configured
    if github_config.personal_token or os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN"):
        return GithubStatus(connected=True, mode="pat")

    # Check if GitHub App is configured
    if github_config.app_installation_id:
        return GithubStatus(
            connected=True, mode="app", app_installation_id=github_config.app_installation_id
        )

    return GithubStatus(connected=False, mode="none")


@app.get("/api/github/app-install-url", response_model=GithubAppInstallURL)
async def api_github_app_install_url():
    """Get GitHub App installation URL."""
    settings = get_settings()
    github_config = settings.github

    if not github_config.app_slug:
        raise HTTPException(500, "GitHub App slug not configured")

    base_url = "https://github.com/apps"
    return GithubAppInstallURL(url=f"{base_url}/{github_config.app_slug}/installations/new")


# ============================================================================
# Repository Endpoints
# ============================================================================

@app.get("/api/repos", response_model=List[RepoSummary])
async def api_list_repos(query: Optional[str] = Query(None)):
    data = await list_user_repos(query=query)
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
):
    tree = await get_repo_tree(owner, repo)
    return FileTreeResponse(files=[FileEntry(**f) for f in tree])


@app.get("/api/repos/{owner}/{repo}/file", response_model=FileContent)
async def api_get_file(
    owner: str = FPath(...),
    repo: str = FPath(...),
    path: str = Query(...),
):
    content = await get_file(owner, repo, path)
    return FileContent(path=path, content=content)


@app.post("/api/repos/{owner}/{repo}/file", response_model=CommitResponse)
async def api_put_file(
    owner: str = FPath(...),
    repo: str = FPath(...),
    payload: CommitRequest = ...,
):
    result = await put_file(
        owner, repo, payload.path, payload.content, payload.message
    )
    return CommitResponse(**result)


@app.get("/api/settings", response_model=SettingsResponse)
async def api_get_settings():
    s: AppSettings = get_settings()
    # Mask sensitive data in response
    github_data = s.github.model_dump()
    if github_data.get("personal_token"):
        github_data["personal_token"] = "***" if github_data["personal_token"] else ""
    if github_data.get("app_private_key_base64"):
        github_data["app_private_key_base64"] = "***" if github_data["app_private_key_base64"] else ""
    if github_data.get("app_client_secret"):
        github_data["app_client_secret"] = "***" if github_data["app_client_secret"] else ""

    return SettingsResponse(
        provider=s.provider,
        providers=[LLMProvider.openai, LLMProvider.claude, LLMProvider.watsonx, LLMProvider.ollama],
        github=github_data,
        openai=s.openai.model_dump(),
        claude=s.claude.model_dump(),
        watsonx=s.watsonx.model_dump(),
        ollama=s.ollama.model_dump(),
        langflow_url=s.langflow_url,
        has_langflow_plan_flow=bool(s.langflow_plan_flow_id),
        setup_completed=s.setup_completed,
    )


@app.post("/api/settings/provider", response_model=SettingsResponse)
async def api_set_provider(update: ProviderUpdate):
    s = set_provider(update.provider)
    github_data = s.github.model_dump()
    if github_data.get("personal_token"):
        github_data["personal_token"] = "***" if github_data["personal_token"] else ""
    if github_data.get("app_private_key_base64"):
        github_data["app_private_key_base64"] = "***" if github_data["app_private_key_base64"] else ""
    if github_data.get("app_client_secret"):
        github_data["app_client_secret"] = "***" if github_data["app_client_secret"] else ""

    return SettingsResponse(
        provider=s.provider,
        providers=[LLMProvider.openai, LLMProvider.claude, LLMProvider.watsonx, LLMProvider.ollama],
        github=github_data,
        openai=s.openai.model_dump(),
        claude=s.claude.model_dump(),
        watsonx=s.watsonx.model_dump(),
        ollama=s.ollama.model_dump(),
        langflow_url=s.langflow_url,
        has_langflow_plan_flow=bool(s.langflow_plan_flow_id),
        setup_completed=s.setup_completed,
    )


@app.put("/api/settings/llm", response_model=SettingsResponse)
async def api_update_llm_settings(updates: dict):
    """Update full LLM settings including provider-specific configs."""
    s = update_settings(updates)
    github_data = s.github.model_dump()
    if github_data.get("personal_token"):
        github_data["personal_token"] = "***" if github_data["personal_token"] else ""
    if github_data.get("app_private_key_base64"):
        github_data["app_private_key_base64"] = "***" if github_data["app_private_key_base64"] else ""
    if github_data.get("app_client_secret"):
        github_data["app_client_secret"] = "***" if github_data["app_client_secret"] else ""

    return SettingsResponse(
        provider=s.provider,
        providers=[LLMProvider.openai, LLMProvider.claude, LLMProvider.watsonx, LLMProvider.ollama],
        github=github_data,
        openai=s.openai.model_dump(),
        claude=s.claude.model_dump(),
        watsonx=s.watsonx.model_dump(),
        ollama=s.ollama.model_dump(),
        langflow_url=s.langflow_url,
        has_langflow_plan_flow=bool(s.langflow_plan_flow_id),
        setup_completed=s.setup_completed,
    )


@app.post("/api/chat/plan", response_model=PlanResult)
async def api_chat_plan(req: ChatPlanRequest):
    full_name = f"{req.repo_owner}/{req.repo_name}"
    plan = await generate_plan(req.goal, full_name)
    return plan


@app.post("/api/chat/execute")
async def api_chat_execute(req: ExecutePlanRequest):
    full_name = f"{req.repo_owner}/{req.repo_name}"
    result = await execute_plan(req.plan, full_name)
    return result


@app.get("/api/flow/current")
async def api_get_flow():
    """Return the current agent flow definition as a graph."""
    flow = await get_flow_definition()
    return flow


STATIC_DIR = Path(__file__).resolve().parent / "web"


@app.get("/", include_in_schema=False)
async def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse(
        {
            "message": "GitPilota UI not built. The static files directory is missing."
        },
        status_code=500,
    )


if STATIC_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR),
        name="static",
    )
