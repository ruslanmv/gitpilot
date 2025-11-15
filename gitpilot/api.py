from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query, Path as FPath
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .version import __version__
from .github_api import list_user_repos, get_repo_tree, get_file, put_file
from .settings import AppSettings, get_settings, set_provider, update_settings, LLMProvider
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
