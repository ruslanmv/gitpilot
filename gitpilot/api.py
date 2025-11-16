from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query, Path as FPath
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .version import __version__
from .github_api import (
    list_user_repos,
    get_repo_tree,
    get_file,
    put_file,
)
from .settings import (
    AppSettings,
    get_settings,
    set_provider,
    update_settings,
    logout_github,
    LLMProvider,
    GitHubAuthMode,
    GitHubConfig,
)
from .agentic import generate_plan, execute_plan, PlanResult, get_flow_definition

app = FastAPI(
    title="GitPilot API",
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


class GithubStatus(BaseModel):
    connected: bool
    mode: str  # "pat" or "app" or "none"
    app_installation_id: Optional[str] = None


class GithubAppInstallURL(BaseModel):
    url: str


class GitHubOAuthURL(BaseModel):
    url: str


class GitHubOAuthCallback(BaseModel):
    code: str


@app.post("/api/github/logout")
async def api_github_logout():
    """Logout from GitHub (clear credentials)."""
    logout_github()
    return {"status": "logged_out"}


@app.get("/api/github/oauth/url")
async def api_github_oauth_url():
    """Get GitHub OAuth authorization URL."""
    settings = get_settings()
    github_config = settings.github

    # For OAuth, we can use a simple GitHub OAuth app or device flow
    # For now, let's create a URL that prompts for PAT as a fallback
    # In production, you'd register a GitHub OAuth app and use client_id

    # Check if we have OAuth client_id
    if github_config.app_client_id:
        # Use GitHub OAuth flow
        base_url = "https://github.com/login/oauth/authorize"
        redirect_uri = f"{os.getenv('BASE_PUBLIC_URL', 'http://localhost:8000')}/api/github/oauth/callback"
        scope = "repo,user"

        oauth_url = f"{base_url}?client_id={github_config.app_client_id}&redirect_uri={redirect_uri}&scope={scope}"
        return GitHubOAuthURL(url=oauth_url)
    else:
        # Fallback: Direct user to create PAT
        return GitHubOAuthURL(url="https://github.com/settings/tokens/new?scopes=repo&description=GitPilot")


@app.get("/api/github/oauth/callback")
async def api_github_oauth_callback(code: str):
    """Handle GitHub OAuth callback."""
    from fastapi import HTTPException
    from fastapi.responses import RedirectResponse

    settings = get_settings()
    github_config = settings.github

    if not github_config.app_client_id or not github_config.app_client_secret:
        raise HTTPException(500, "GitHub OAuth not configured")

    # Exchange code for access token
    async with httpx.AsyncClient() as client:
        token_url = "https://github.com/login/oauth/access_token"
        headers = {"Accept": "application/json"}
        data = {
            "client_id": github_config.app_client_id,
            "client_secret": github_config.app_client_secret,
            "code": code,
        }

        res = await client.post(token_url, headers=headers, data=data)
        if res.status_code >= 400:
            raise HTTPException(res.status_code, "Failed to exchange OAuth code")

        token_data = res.json()
        access_token = token_data.get("access_token")

        if not access_token:
            raise HTTPException(400, "No access token received")

        # Save token to settings
        update_settings({
            "github": {
                "auth_mode": "pat",
                "personal_token": access_token,
            }
        })

    # Redirect to home
    return RedirectResponse(url="/")


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
        from fastapi import HTTPException
        raise HTTPException(500, "GitHub App slug not configured")

    base_url = "https://github.com/apps"
    return GithubAppInstallURL(url=f"{base_url}/{github_config.app_slug}/installations/new")


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
