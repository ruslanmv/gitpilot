"""
GitPilot Redesign — Shared Models & Schemas
Centralized Pydantic models for the redesigned API contract.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# ─── Enums ────────────────────────────────────────────────

class WorkspaceMode(StrEnum):
    folder = "folder"
    local_git = "local_git"
    github = "github"


class ProviderName(StrEnum):
    openai = "openai"
    claude = "claude"
    watsonx = "watsonx"
    ollama = "ollama"
    ollabridge = "ollabridge"


class ProviderHealth(StrEnum):
    ok = "ok"
    warning = "warning"
    error = "error"
    unknown = "unknown"


class ProviderConnectionType(StrEnum):
    local = "local"
    api_key = "api_key"
    pairing = "pairing"
    cloud = "cloud"
    managed = "managed"


class SessionMode(StrEnum):
    folder = "folder"
    local_git = "local_git"
    github = "github"


# ─── Provider Models ─────────────────────────────────────

class ProviderSummary(BaseModel):
    configured: bool = False
    name: ProviderName = ProviderName.ollama
    source: Literal[".env", "settings", "unknown"] = "unknown"
    model: str | None = None
    base_url: str | None = None
    connection_type: ProviderConnectionType | None = None
    has_api_key: bool = False
    health: ProviderHealth | None = ProviderHealth.unknown
    models_available: bool | None = None
    warning: str | None = None


class ProviderStatusResponse(BaseModel):
    configured: bool
    name: ProviderName
    source: Literal[".env", "settings", "unknown"] = "unknown"
    model: str | None = None
    base_url: str | None = None
    connection_type: ProviderConnectionType | None = None
    has_api_key: bool = False
    health: ProviderHealth | None = ProviderHealth.unknown
    models_available: bool | None = None
    warning: str | None = None


# ─── Workspace Models ────────────────────────────────────

class WorkspaceCapabilitySummary(BaseModel):
    folder_mode_available: bool = False
    local_git_available: bool = False
    github_mode_available: bool = False


class WorkspaceSummary(BaseModel):
    folder_open: bool = False
    folder_path: str | None = None
    folder_name: str | None = None
    git_detected: bool = False
    repo_root: str | None = None
    repo_name: str | None = None
    branch: str | None = None
    remotes: list[str] = Field(default_factory=list)


# ─── GitHub Models ────────────────────────────────────────

class GithubStatusSummary(BaseModel):
    connected: bool = False
    token_configured: bool = False
    username: str | None = None


# ─── Status Response ─────────────────────────────────────

class StatusResponse(BaseModel):
    server_ready: bool = True
    provider: ProviderStatusResponse
    workspace: WorkspaceCapabilitySummary
    github: GithubStatusSummary


# ─── Session Models ──────────────────────────────────────

class StartSessionRequest(BaseModel):
    mode: WorkspaceMode
    folder_path: str | None = None
    repo_root: str | None = None
    repo_full_name: str | None = None
    branch: str | None = None


class StartSessionResponse(BaseModel):
    session_id: str
    mode: WorkspaceMode
    title: str
    status: Literal["active"] = "active"
    folder_path: str | None = None
    repo_root: str | None = None
    repo_full_name: str | None = None
    branch: str | None = None


# ─── Chat Models ─────────────────────────────────────────

class PlanStepSummary(BaseModel):
    step: int
    title: str
    action: str
    file: str | None = None
    description: str
    status: Literal["pending", "ready", "applied", "failed"] | None = "pending"


class PlanSummary(BaseModel):
    goal: str
    summary: str
    steps: list[PlanStepSummary]


class FileReference(BaseModel):
    path: str
    line: int | None = None


class StructuredProjectContext(BaseModel):
    mode: str | None = None
    workspaceRoot: str | None = None
    repoRoot: str | None = None
    repoName: str | None = None
    branch: str | None = None
    languages: list[str] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)
    keyFiles: list[str] = Field(default_factory=list)
    readmePreview: str | None = None
    treeSummary: list[dict] = Field(default_factory=list)
    indexedAt: str | None = None


class StructuredWorkingSet(BaseModel):
    currentFile: str | None = None
    languageId: str | None = None
    currentSelection: str | None = None
    openTabs: list[str] = Field(default_factory=list)
    recentFiles: list[str] = Field(default_factory=list)
    relatedFiles: list[str] = Field(default_factory=list)


class StructuredTaskContext(BaseModel):
    intent: str | None = None
    scope: Literal["workspace", "selection", "file"] | None = None
    summary: str | None = None


class ChatMessageRequest(BaseModel):
    session_id: str
    message: str
    scope: Literal["workspace", "selection", "file"] = "workspace"
    topology_id: str | None = None
    intent: str | None = None
    project_context: StructuredProjectContext | None = None
    working_set: StructuredWorkingSet | None = None
    task_context: StructuredTaskContext | None = None


class ChatMessageResponse(BaseModel):
    session_id: str
    answer: str
    message_id: str | None = None
    plan: PlanSummary | None = None
    references: list[FileReference] = Field(default_factory=list)


# ─── Provider Test Models ────────────────────────────────

class OpenAIProviderInput(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


class ClaudeProviderInput(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


class WatsonxProviderInput(BaseModel):
    api_key: str | None = None
    project_id: str | None = None
    base_url: str | None = None
    model_id: str | None = None


class OllamaProviderInput(BaseModel):
    base_url: str | None = None
    model: str | None = None


class OllaBridgeProviderInput(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    connection_type: ProviderConnectionType | None = None


class ProviderTestRequest(BaseModel):
    provider: ProviderName
    openai: OpenAIProviderInput | None = None
    claude: ClaudeProviderInput | None = None
    watsonx: WatsonxProviderInput | None = None
    ollama: OllamaProviderInput | None = None
    ollabridge: OllaBridgeProviderInput | None = None


class ProviderTestResponse(ProviderStatusResponse):
    details: str | None = None


# ─── OllaBridge Health ────────────────────────────────────

class OllaBridgeHealthResponse(BaseModel):
    status: Literal["ok", "error"]
    base_url: str
    effective_api_base: str
    models_available: bool = False
    auth_mode: str = "unknown"
    warning: str | None = None
