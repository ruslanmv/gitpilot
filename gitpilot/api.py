from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query, Path as FPath, Header
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .version import __version__
from .github_api import list_user_repos, get_repo_tree, get_file, put_file
from .settings import AppSettings, get_settings, set_provider, update_settings, LLMProvider
from .agentic import generate_plan, execute_plan, PlanResult, get_flow_definition
from .github_oauth import (
    generate_authorization_url,
    exchange_code_for_token,
    validate_token,
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


# ============================================================================
# Authentication Endpoints
# ============================================================================


@app.get("/api/auth/url", response_model=AuthUrlResponse)
async def api_get_auth_url():
    """
    Generate GitHub OAuth authorization URL.

    Returns the URL to redirect users to for GitHub authentication.
    """
    auth_url, state = generate_authorization_url()
    return AuthUrlResponse(authorization_url=auth_url, state=state)


@app.post("/api/auth/callback", response_model=AuthSession)
async def api_auth_callback(request: AuthCallbackRequest):
    """
    Handle GitHub OAuth callback.

    Exchange the authorization code for an access token and return user session.
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

    Can be used with both OAuth tokens and Personal Access Tokens.
    """
    user = await validate_token(request.access_token)
    if user:
        return UserInfoResponse(user=user, authenticated=True)
    return UserInfoResponse(
        user=GitHubUser(login="", id=0, avatar_url=""),
        authenticated=False,
    )


@app.get("/api/auth/status")
async def api_auth_status():
    """
    Check if GitHub authentication is configured.

    Returns whether OAuth is set up or if PAT should be used.
    """
    import os
    has_oauth = bool(os.getenv("GITHUB_CLIENT_ID") and os.getenv("GITHUB_CLIENT_SECRET"))
    has_pat = bool(os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN"))
    has_github_app = bool(os.getenv("GITHUB_APP_ID"))

    return {
        "oauth_configured": has_oauth,
        "pat_configured": has_pat,
        "github_app_configured": has_github_app,
        "auth_method": "github_app" if has_github_app else "oauth" if has_oauth else "pat" if has_pat else "none",
    }


@app.get("/api/auth/app-url")
async def api_get_app_url():
    """
    Get GitHub App installation URL.

    Returns the URL where users can install the GitHub App.
    """
    import os

    # Get custom app URL if configured, otherwise use default
    app_slug = os.getenv("GITHUB_APP_SLUG", "gitpilot")
    app_url = f"https://github.com/apps/{app_slug}"

    return {
        "app_url": app_url,
        "app_slug": app_slug,
    }


@app.get("/api/auth/installation-status")
async def api_check_installation_status():
    """
    Check if GitHub App is installed for the current user.

    This endpoint checks if:
    1. A GitHub App is configured
    2. User has a valid token (PAT or from localStorage)
    3. User has access to repositories

    Returns installation status and user info if authenticated.
    """
    import os

    # Check if using PAT (simpler auth)
    pat_token = os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")

    if pat_token:
        # Validate PAT token
        user = await validate_token(pat_token)
        if user:
            return {
                "installed": True,
                "access_token": pat_token,
                "user": user,
                "auth_type": "pat",
            }

    # If no PAT, check if GitHub App is configured
    github_app_id = os.getenv("GITHUB_APP_ID")

    if not github_app_id:
        return {
            "installed": False,
            "message": "GitHub authentication not configured. Please set GITPILOT_GITHUB_TOKEN or configure GitHub App.",
            "auth_type": "none",
        }

    # TODO: Implement GitHub App installation check
    # For now, return not installed if no PAT
    return {
        "installed": False,
        "message": "GitHub App not installed. Please install the app or use a Personal Access Token.",
        "auth_type": "github_app",
    }


STATIC_DIR = Path(__file__).resolve().parent / "web"


@app.get("/", include_in_schema=False)
async def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse(
        {
            "message": "GitPilot UI not built. The static files directory is missing."
        },
        status_code=500,
    )


if STATIC_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR),
        name="static",
    )
