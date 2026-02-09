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
from .agentic import (
    generate_plan,
    execute_plan,
    PlanResult,
    get_flow_definition,
    dispatch_request,
    create_pr_after_execution,
)
from .agent_router import route as route_request
from . import github_issues
from . import github_pulls
from . import github_search
from .session import SessionManager, Session
from .hooks import HookManager, HookEvent
from .permissions import PermissionManager, PermissionMode
from .memory import MemoryManager
from .github_oauth import (
    generate_authorization_url,
    exchange_code_for_token,
    validate_token,
    initiate_device_flow,
    poll_device_token,
    AuthSession,
    GitHubUser,
)
import os
import logging
from .model_catalog import list_models_for_provider

# Optional A2A adapter (MCP ContextForge)
from .a2a_adapter import router as a2a_router

logger = logging.getLogger(__name__)

# --- Phase 1 singletons ---
_session_mgr = SessionManager()
_hook_mgr = HookManager()
_perm_mgr = PermissionManager()

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


# --- v2 Request/Response models ---

class ChatRequest(BaseModel):
    """Unified chat request for the conversational dispatcher."""
    repo_owner: str
    repo_name: str
    message: str
    branch_name: Optional[str] = None
    auto_pr: bool = False


class IssueCreateRequest(BaseModel):
    title: str
    body: Optional[str] = None
    labels: Optional[List[str]] = None
    assignees: Optional[List[str]] = None
    milestone: Optional[int] = None


class IssueUpdateRequest(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    state: Optional[str] = None
    labels: Optional[List[str]] = None
    assignees: Optional[List[str]] = None
    milestone: Optional[int] = None


class IssueCommentRequest(BaseModel):
    body: str


class PRCreateRequest(BaseModel):
    title: str
    head: str
    base: str
    body: Optional[str] = None
    draft: bool = False


class PRMergeRequest(BaseModel):
    merge_method: str = "merge"
    commit_title: Optional[str] = None
    commit_message: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    per_page: int = 30
    page: int = 1


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
# Conversational Chat Endpoint (v2 upgrade)
# ============================================================================

@app.post("/api/chat/message")
async def api_chat_message(req: ChatRequest, authorization: Optional[str] = Header(None)):
    """
    Unified conversational endpoint.  The router analyses the message and
    dispatches to the appropriate agent (issue, PR, search, review, learning,
    or the existing plan+execute pipeline).
    """
    token = get_github_token(authorization)

    logger.info(
        "CHAT MESSAGE: %s/%s | message=%r | branch=%r",
        req.repo_owner,
        req.repo_name,
        req.message[:80],
        req.branch_name,
    )

    with execution_context(token, ref=req.branch_name):
        full_name = f"{req.repo_owner}/{req.repo_name}"
        result = await dispatch_request(
            req.message, full_name, token=token, branch_name=req.branch_name,
        )

        # If auto_pr is requested and execution completed, create PR
        if (
            req.auto_pr
            and isinstance(result, dict)
            and result.get("category") == "plan_execute"
            and result.get("plan")
        ):
            result["auto_pr_hint"] = (
                "Plan generated. Execute it first, then auto-PR will be created."
            )

        return result


@app.post("/api/chat/execute-with-pr")
async def api_chat_execute_with_pr(
    req: ExecutePlanRequest,
    authorization: Optional[str] = Header(None),
):
    """Execute a plan AND automatically create a pull request afterwards."""
    token = get_github_token(authorization)

    with execution_context(token, ref=req.branch_name):
        full_name = f"{req.repo_owner}/{req.repo_name}"
        result = await execute_plan(
            req.plan, full_name, token=token, branch_name=req.branch_name,
        )

        if isinstance(result, dict) and result.get("status") == "completed":
            branch = result.get("branch", req.branch_name)
            if branch:
                pr = await create_pr_after_execution(
                    full_name,
                    branch,
                    req.plan.goal,
                    result.get("executionLog", {}),
                    token=token,
                )
                if pr:
                    result["pull_request"] = {
                        "number": pr.get("number"),
                        "url": pr.get("html_url"),
                        "title": pr.get("title"),
                    }

            result.setdefault(
                "mode",
                "sticky" if req.branch_name else "hard-switch",
            )

        return result


# ============================================================================
# Issue Endpoints (v2 upgrade)
# ============================================================================

@app.get("/api/repos/{owner}/{repo}/issues")
async def api_list_issues(
    owner: str = FPath(...),
    repo: str = FPath(...),
    state: str = Query("open"),
    labels: Optional[str] = Query(None),
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
    authorization: Optional[str] = Header(None),
):
    """List issues for a repository."""
    token = get_github_token(authorization)
    issues = await github_issues.list_issues(
        owner, repo, state=state, labels=labels,
        per_page=per_page, page=page, token=token,
    )
    return {"issues": issues, "page": page, "per_page": per_page}


@app.get("/api/repos/{owner}/{repo}/issues/{issue_number}")
async def api_get_issue(
    owner: str = FPath(...),
    repo: str = FPath(...),
    issue_number: int = FPath(...),
    authorization: Optional[str] = Header(None),
):
    """Get a single issue."""
    token = get_github_token(authorization)
    return await github_issues.get_issue(owner, repo, issue_number, token=token)


@app.post("/api/repos/{owner}/{repo}/issues")
async def api_create_issue(
    owner: str = FPath(...),
    repo: str = FPath(...),
    payload: IssueCreateRequest = ...,
    authorization: Optional[str] = Header(None),
):
    """Create a new issue."""
    token = get_github_token(authorization)
    return await github_issues.create_issue(
        owner, repo, payload.title,
        body=payload.body, labels=payload.labels,
        assignees=payload.assignees, milestone=payload.milestone,
        token=token,
    )


@app.patch("/api/repos/{owner}/{repo}/issues/{issue_number}")
async def api_update_issue(
    owner: str = FPath(...),
    repo: str = FPath(...),
    issue_number: int = FPath(...),
    payload: IssueUpdateRequest = ...,
    authorization: Optional[str] = Header(None),
):
    """Update an existing issue."""
    token = get_github_token(authorization)
    return await github_issues.update_issue(
        owner, repo, issue_number,
        title=payload.title, body=payload.body, state=payload.state,
        labels=payload.labels, assignees=payload.assignees,
        milestone=payload.milestone, token=token,
    )


@app.get("/api/repos/{owner}/{repo}/issues/{issue_number}/comments")
async def api_list_issue_comments(
    owner: str = FPath(...),
    repo: str = FPath(...),
    issue_number: int = FPath(...),
    authorization: Optional[str] = Header(None),
):
    """List comments on an issue."""
    token = get_github_token(authorization)
    return await github_issues.list_issue_comments(owner, repo, issue_number, token=token)


@app.post("/api/repos/{owner}/{repo}/issues/{issue_number}/comments")
async def api_add_issue_comment(
    owner: str = FPath(...),
    repo: str = FPath(...),
    issue_number: int = FPath(...),
    payload: IssueCommentRequest = ...,
    authorization: Optional[str] = Header(None),
):
    """Add a comment to an issue."""
    token = get_github_token(authorization)
    return await github_issues.add_issue_comment(
        owner, repo, issue_number, payload.body, token=token,
    )


# ============================================================================
# Pull Request Endpoints (v2 upgrade)
# ============================================================================

@app.get("/api/repos/{owner}/{repo}/pulls")
async def api_list_pulls(
    owner: str = FPath(...),
    repo: str = FPath(...),
    state: str = Query("open"),
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
    authorization: Optional[str] = Header(None),
):
    """List pull requests."""
    token = get_github_token(authorization)
    prs = await github_pulls.list_pull_requests(
        owner, repo, state=state, per_page=per_page, page=page, token=token,
    )
    return {"pull_requests": prs, "page": page, "per_page": per_page}


@app.get("/api/repos/{owner}/{repo}/pulls/{pull_number}")
async def api_get_pull(
    owner: str = FPath(...),
    repo: str = FPath(...),
    pull_number: int = FPath(...),
    authorization: Optional[str] = Header(None),
):
    """Get a single pull request."""
    token = get_github_token(authorization)
    return await github_pulls.get_pull_request(owner, repo, pull_number, token=token)


@app.post("/api/repos/{owner}/{repo}/pulls")
async def api_create_pull(
    owner: str = FPath(...),
    repo: str = FPath(...),
    payload: PRCreateRequest = ...,
    authorization: Optional[str] = Header(None),
):
    """Create a new pull request."""
    token = get_github_token(authorization)
    return await github_pulls.create_pull_request(
        owner, repo, title=payload.title, head=payload.head,
        base=payload.base, body=payload.body, draft=payload.draft,
        token=token,
    )


@app.put("/api/repos/{owner}/{repo}/pulls/{pull_number}/merge")
async def api_merge_pull(
    owner: str = FPath(...),
    repo: str = FPath(...),
    pull_number: int = FPath(...),
    payload: PRMergeRequest = ...,
    authorization: Optional[str] = Header(None),
):
    """Merge a pull request."""
    token = get_github_token(authorization)
    return await github_pulls.merge_pull_request(
        owner, repo, pull_number,
        merge_method=payload.merge_method,
        commit_title=payload.commit_title,
        commit_message=payload.commit_message,
        token=token,
    )


@app.get("/api/repos/{owner}/{repo}/pulls/{pull_number}/files")
async def api_list_pr_files(
    owner: str = FPath(...),
    repo: str = FPath(...),
    pull_number: int = FPath(...),
    authorization: Optional[str] = Header(None),
):
    """List files changed in a pull request."""
    token = get_github_token(authorization)
    return await github_pulls.list_pr_files(owner, repo, pull_number, token=token)


# ============================================================================
# Search Endpoints (v2 upgrade)
# ============================================================================

@app.get("/api/search/code")
async def api_search_code(
    q: str = Query(..., description="Search query"),
    owner: Optional[str] = Query(None),
    repo: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
    authorization: Optional[str] = Header(None),
):
    """Search for code across GitHub."""
    token = get_github_token(authorization)
    return await github_search.search_code(
        q, owner=owner, repo=repo, language=language,
        per_page=per_page, page=page, token=token,
    )


@app.get("/api/search/issues")
async def api_search_issues(
    q: str = Query(..., description="Search query"),
    owner: Optional[str] = Query(None),
    repo: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    label: Optional[str] = Query(None),
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
    authorization: Optional[str] = Header(None),
):
    """Search issues and pull requests."""
    token = get_github_token(authorization)
    return await github_search.search_issues(
        q, owner=owner, repo=repo, state=state, label=label,
        per_page=per_page, page=page, token=token,
    )


@app.get("/api/search/repositories")
async def api_search_repositories(
    q: str = Query(..., description="Search query"),
    language: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
    authorization: Optional[str] = Header(None),
):
    """Search for repositories."""
    token = get_github_token(authorization)
    return await github_search.search_repositories(
        q, language=language, sort=sort,
        per_page=per_page, page=page, token=token,
    )


@app.get("/api/search/users")
async def api_search_users(
    q: str = Query(..., description="Search query"),
    type_filter: Optional[str] = Query(None, alias="type"),
    location: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
    authorization: Optional[str] = Header(None),
):
    """Search for GitHub users and organizations."""
    token = get_github_token(authorization)
    return await github_search.search_users(
        q, type_filter=type_filter, location=location, language=language,
        per_page=per_page, page=page, token=token,
    )


# ============================================================================
# Route Analysis Endpoint (v2 upgrade)
# ============================================================================

@app.post("/api/chat/route")
async def api_chat_route(payload: dict):
    """Preview how a message would be routed without executing it.

    Useful for the frontend to display which agent(s) will handle the request.
    """
    message = payload.get("message", "")
    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)

    workflow = route_request(message)
    return {
        "category": workflow.category.value,
        "agents": [a.value for a in workflow.agents],
        "description": workflow.description,
        "requires_repo_context": workflow.requires_repo_context,
        "entity_number": workflow.entity_number,
        "metadata": workflow.metadata,
    }


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

        return JSONResponse({"status": "pending"}, status_code=202)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


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
# Session Endpoints (Phase 1)
# ============================================================================

@app.get("/api/sessions")
async def api_list_sessions():
    """List all saved sessions."""
    return {"sessions": _session_mgr.list_sessions()}


@app.post("/api/sessions")
async def api_create_session(payload: dict):
    """Create a new session."""
    repo = payload.get("repo_full_name", "")
    branch = payload.get("branch")
    session = _session_mgr.create(repo_full_name=repo, branch=branch)
    _session_mgr.save(session)
    return {"session_id": session.id, "status": session.status}


@app.get("/api/sessions/{session_id}")
async def api_get_session(session_id: str):
    """Get session details."""
    session = _session_mgr.load(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "id": session.id,
        "status": session.status,
        "repo_full_name": session.repo_full_name,
        "branch": session.branch,
        "created_at": session.created_at,
        "message_count": len(session.messages),
        "checkpoint_count": len(session.checkpoints),
    }


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    """Delete a session."""
    deleted = _session_mgr.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": True}


@app.post("/api/sessions/{session_id}/checkpoint")
async def api_create_checkpoint(session_id: str, payload: dict):
    """Create a checkpoint for a session."""
    session = _session_mgr.load(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    label = payload.get("label", "checkpoint")
    cp = _session_mgr.create_checkpoint(session, label=label)
    return {"checkpoint_id": cp.id, "label": cp.label, "created_at": cp.created_at}


# ============================================================================
# Hooks Endpoints (Phase 1)
# ============================================================================

@app.get("/api/hooks")
async def api_list_hooks():
    """List registered hooks."""
    return {"hooks": _hook_mgr.list_hooks()}


@app.post("/api/hooks")
async def api_register_hook(payload: dict):
    """Register a new hook."""
    from .hooks import HookDefinition
    try:
        hook = HookDefinition(
            event=HookEvent(payload["event"]),
            name=payload["name"],
            command=payload.get("command"),
            blocking=payload.get("blocking", False),
            timeout=payload.get("timeout", 30),
        )
        _hook_mgr.register(hook)
        return {"registered": True, "name": hook.name, "event": hook.event.value}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/hooks/{event}/{name}")
async def api_unregister_hook(event: str, name: str):
    """Unregister a hook by event and name."""
    try:
        _hook_mgr.unregister(HookEvent(event), name)
        return {"unregistered": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# Permissions Endpoints (Phase 1)
# ============================================================================

@app.get("/api/permissions")
async def api_get_permissions():
    """Get current permission policy."""
    return _perm_mgr.to_dict()


@app.put("/api/permissions/mode")
async def api_set_permission_mode(payload: dict):
    """Set the permission mode (normal, plan, auto)."""
    mode_str = payload.get("mode", "normal")
    try:
        _perm_mgr.policy.mode = PermissionMode(mode_str)
        return {"mode": _perm_mgr.policy.mode.value}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {mode_str}")


# ============================================================================
# Project Context / Memory Endpoints (Phase 1)
# ============================================================================

@app.get("/api/repos/{owner}/{repo}/context")
async def api_get_project_context(
    owner: str = FPath(...),
    repo: str = FPath(...),
):
    """Get project conventions and memory for a repository workspace."""
    from pathlib import Path as StdPath
    workspace_path = StdPath.home() / ".gitpilot" / "workspaces" / owner / repo
    if not workspace_path.exists():
        return {"conventions": "", "rules": [], "auto_memory": {}, "system_prompt": ""}
    mgr = MemoryManager(workspace_path)
    ctx = mgr.load_context()
    return {
        "conventions": ctx.conventions,
        "rules": ctx.rules,
        "auto_memory": ctx.auto_memory,
        "system_prompt": ctx.to_system_prompt(),
    }


@app.post("/api/repos/{owner}/{repo}/context/init")
async def api_init_project_context(
    owner: str = FPath(...),
    repo: str = FPath(...),
):
    """Initialize .gitpilot/ directory with template GITPILOT.md."""
    from pathlib import Path as StdPath
    workspace_path = StdPath.home() / ".gitpilot" / "workspaces" / owner / repo
    workspace_path.mkdir(parents=True, exist_ok=True)
    mgr = MemoryManager(workspace_path)
    md_path = mgr.init_project()
    return {"initialized": True, "path": str(md_path)}


@app.post("/api/repos/{owner}/{repo}/context/pattern")
async def api_add_learned_pattern(
    owner: str = FPath(...),
    repo: str = FPath(...),
    payload: dict = ...,
):
    """Add a learned pattern to auto-memory."""
    from pathlib import Path as StdPath
    pattern = payload.get("pattern", "")
    if not pattern:
        raise HTTPException(status_code=400, detail="pattern is required")
    workspace_path = StdPath.home() / ".gitpilot" / "workspaces" / owner / repo
    workspace_path.mkdir(parents=True, exist_ok=True)
    mgr = MemoryManager(workspace_path)
    mgr.add_learned_pattern(pattern)
    return {"added": True, "pattern": pattern}


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
