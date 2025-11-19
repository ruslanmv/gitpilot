from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query, Path as FPath
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .version import __version__
from .github_api import list_user_repos, get_repo_tree, get_file, put_file
from .settings import AppSettings, get_settings, set_provider, update_settings, LLMProvider, GitHubAuthMode
from .agentic import generate_plan, execute_plan, PlanResult, get_flow_definition
from .auth import get_auth_manager

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


class AuthStatusResponse(BaseModel):
    authenticated: bool
    auth_mode: GitHubAuthMode
    has_user_token: bool
    has_app_config: bool
    setup_completed: bool
    username: Optional[str] = None
    has_env_credentials: bool = False  # True if .env has credentials
    env_username: Optional[str] = None  # Username from .env credentials
    use_custom_auth: bool = False  # True if user chose custom auth over .env


class GitHubAppInstallUrlResponse(BaseModel):
    install_url: str


class LoginResponse(BaseModel):
    success: bool
    message: str


@app.get("/api/auth/status", response_model=AuthStatusResponse)
async def api_auth_status():
    """Get current authentication status."""
    # Reload settings from disk to pick up changes from CLI
    from .settings import AppSettings
    import os
    settings = AppSettings.from_disk()
    auth_manager = get_auth_manager()

    # Check for .env credentials (environment variables)
    has_env_credentials = False
    env_username = None

    # Check for .env GitHub App credentials
    if os.getenv("GITPILOT_GH_APP_ID") and os.getenv("GITPILOT_GH_APP_INSTALLATION_ID") and os.getenv("GITPILOT_GH_APP_PRIVATE_KEY_BASE64"):
        has_env_credentials = True
    # Check for .env PAT credentials
    elif os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN"):
        has_env_credentials = True

    # Get username from .env credentials if they exist and we're not using custom auth
    if has_env_credentials and not settings.use_custom_auth:
        try:
            import httpx
            # Try to get token from environment
            env_token = os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
            if env_token:
                response = await httpx.AsyncClient().get(
                    "https://api.github.com/user",
                    headers={"Authorization": f"Bearer {env_token}"}
                )
                if response.status_code == 200:
                    data = response.json()
                    env_username = data.get("login")
            # Try GitHub App from .env
            elif os.getenv("GITPILOT_GH_APP_ID") and os.getenv("GITPILOT_GH_APP_INSTALLATION_ID"):
                app_id = os.getenv("GITPILOT_GH_APP_ID")
                installation_id = os.getenv("GITPILOT_GH_APP_INSTALLATION_ID")
                private_key = os.getenv("GITPILOT_GH_APP_PRIVATE_KEY_BASE64")
                if private_key:
                    try:
                        token = await auth_manager.get_installation_token(app_id, installation_id, private_key)
                        response = await httpx.AsyncClient().get(
                            "https://api.github.com/user/installations",
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Accept": "application/vnd.github+json"
                            }
                        )
                        if response.status_code == 200:
                            data = response.json()
                            if data.get("total_count", 0) > 0 and data.get("installations"):
                                env_username = data["installations"][0]["account"]["login"]
                    except Exception:
                        pass
        except Exception:
            pass

    # Check for custom/configured GitHub App authentication
    has_app_config = False
    authenticated = False
    username = None

    # If user chose custom auth, ignore .env and use configured credentials
    if settings.use_custom_auth or not has_env_credentials:
        if settings.github.auth_mode == GitHubAuthMode.app:
            # Check if GitHub App is configured
            app_config = settings.github.app
            if app_config.app_id and app_config.installation_id:
                # Check if private key is available
                private_key = auth_manager.get_app_private_key() or app_config.private_key_base64
                if private_key:
                    has_app_config = True
                    # Try to get installation token to verify authentication works
                    try:
                        token = await auth_manager.get_installation_token(
                            app_config.app_id,
                            app_config.installation_id,
                            private_key,
                        )
                        authenticated = True

                        # Get username from GitHub API using the installation token
                        try:
                            import httpx
                            response = await httpx.AsyncClient().get(
                                "https://api.github.com/user/installations",
                                headers={
                                    "Authorization": f"Bearer {token}",
                                    "Accept": "application/vnd.github+json"
                                }
                            )
                            if response.status_code == 200:
                                data = response.json()
                                # Get the account username from installations
                                if data.get("total_count", 0) > 0 and data.get("installations"):
                                    username = data["installations"][0]["account"]["login"]
                        except Exception:
                            pass
                    except Exception:
                        # Authentication configured but not working
                        authenticated = False
        elif settings.github.auth_mode == GitHubAuthMode.pat:
            # Check for PAT authentication
            if settings.github.personal_token:
                authenticated = True
                has_app_config = False
                # Get username from PAT
                try:
                    import httpx
                    response = await httpx.AsyncClient().get(
                        "https://api.github.com/user",
                        headers={"Authorization": f"Bearer {settings.github.personal_token}"}
                    )
                    if response.status_code == 200:
                        data = response.json()
                        username = data.get("login")
                except Exception:
                    pass
    else:
        # Use .env credentials - they're already authenticated
        authenticated = True
        username = env_username

    return AuthStatusResponse(
        authenticated=authenticated,
        auth_mode=settings.github.auth_mode,
        has_user_token=False,  # OAuth removed
        has_app_config=has_app_config,
        setup_completed=settings.setup_completed,
        username=username,
        has_env_credentials=has_env_credentials,
        env_username=env_username,
        use_custom_auth=settings.use_custom_auth,
    )


@app.post("/api/auth/login", response_model=LoginResponse)
async def api_auth_login():
    """
    Initiate GitHub OAuth login flow.
    For CLI, this returns instructions.
    For web, this should be handled by frontend redirecting to GitHub.
    """
    settings = get_settings()

    if not settings.github.oauth.client_id:
        return LoginResponse(
            success=False,
            message="GitHub OAuth not configured. Please set GITPILOT_OAUTH_CLIENT_ID."
        )

    # For web-based OAuth, frontend should redirect to:
    # https://github.com/login/oauth/authorize?client_id=XXX&scope=repo,read:user

    return LoginResponse(
        success=True,
        message="Please complete OAuth flow via CLI: gitpilot login"
    )


@app.post("/api/auth/use-env", response_model=LoginResponse)
async def api_auth_use_env():
    """Switch to using .env credentials."""
    from .settings import AppSettings
    settings = AppSettings.from_disk()

    # Set flag to use .env credentials
    settings.use_custom_auth = False
    settings.save()

    return LoginResponse(
        success=True,
        message="Switched to .env credentials"
    )


@app.post("/api/auth/use-custom", response_model=LoginResponse)
async def api_auth_use_custom():
    """Switch to using custom credentials."""
    from .settings import AppSettings
    settings = AppSettings.from_disk()

    # Set flag to use custom credentials
    settings.use_custom_auth = True
    settings.save()

    return LoginResponse(
        success=True,
        message="Ready for custom authentication"
    )


class ConfigureAppRequest(BaseModel):
    app_id: str
    installation_id: str
    private_key_base64: str


@app.post("/api/auth/configure-app", response_model=LoginResponse)
async def api_auth_configure_app(request: ConfigureAppRequest):
    """Configure GitHub App credentials via web form."""
    from .settings import AppSettings
    settings = AppSettings.from_disk()
    auth_manager = get_auth_manager()

    try:
        # Decode and validate private key
        import base64
        try:
            private_key_pem = base64.b64decode(request.private_key_base64).decode('utf-8')
        except Exception as e:
            return LoginResponse(
                success=False,
                message=f"Invalid private key format: {str(e)}"
            )

        # Verify the credentials work by trying to get an installation token
        try:
            token = await auth_manager.get_installation_token(
                request.app_id,
                request.installation_id,
                request.private_key_base64
            )
            # If we get here, credentials are valid
        except Exception as e:
            return LoginResponse(
                success=False,
                message=f"Failed to authenticate with GitHub: {str(e)}"
            )

        # Store credentials
        auth_manager.save_app_private_key(request.private_key_base64)
        settings.github.app.app_id = request.app_id
        settings.github.app.installation_id = request.installation_id
        settings.github.app.private_key_base64 = request.private_key_base64
        settings.github.auth_mode = GitHubAuthMode.app
        settings.use_custom_auth = True
        settings.save()

        return LoginResponse(
            success=True,
            message="GitHub App configured successfully"
        )

    except Exception as e:
        return LoginResponse(
            success=False,
            message=f"Configuration failed: {str(e)}"
        )


@app.post("/api/auth/logout", response_model=LoginResponse)
async def api_auth_logout():
    """Logout user by clearing stored credentials."""
    # Reload settings to ensure we have the latest state
    from .settings import AppSettings
    settings = AppSettings.from_disk()
    auth_manager = get_auth_manager()

    # Clear keyring credentials
    auth_manager.logout()

    # Clear settings file (like CLI logout does)
    settings.github.app.app_id = ""
    settings.github.app.installation_id = ""
    settings.github.app.private_key_base64 = ""
    settings.github.personal_token = ""

    # Reset to .env credentials if available
    settings.use_custom_auth = False
    settings.save()

    return LoginResponse(
        success=True,
        message="Successfully logged out"
    )


@app.get("/api/auth/github-app-install-url", response_model=GitHubAppInstallUrlResponse)
async def api_github_app_install_url():
    """Get GitHub App installation URL."""
    settings = get_settings()

    if not settings.github.app.slug:
        # Return generic URL (default to gitpilota)
        install_url = "https://github.com/apps/gitpilota/installations/new"
    else:
        install_url = f"https://github.com/apps/{settings.github.app.slug}/installations/new"

    return GitHubAppInstallUrlResponse(install_url=install_url)


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
