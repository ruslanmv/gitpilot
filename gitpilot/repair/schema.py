"""Pydantic models for the GitPilot repair request/response contract.

The ``repair-plan.json`` produced by SelfRepair has the same shape as
:class:`RepairRequest`.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# Default fail-closed forbidden globs (always denied even if not specified).
DEFAULT_FORBIDDEN_PATHS: list[str] = [
    ".env",
    "secrets/**",
    "**/*token*",
    "**/*secret*",
]


class RepairMode(str, Enum):
    dry_run = "dry_run"
    draft_pr = "draft_pr"
    apply = "apply"


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Issue(BaseModel):
    """A single issue SelfRepair / a caller wants GitPilot to fix."""

    id: str
    severity: Severity = Severity.medium
    description: str = ""
    recommended_action: str = ""


class CoderSpec(BaseModel):
    """Which inference provider/model to use for patch generation."""

    provider: str = "ollabridge"
    model: str = "code-coder"


class SandboxSpec(BaseModel):
    """Sandbox configuration for patch validation."""

    provider: str = "matrixlab"
    profile: str = "default"
    required: bool = False


class RepairRequest(BaseModel):
    """Repair request (a.k.a. SelfRepair repair-plan)."""

    client_id: str
    workspace_id: str
    task_id: str
    repo_url: str
    branch: str = "main"
    mode: RepairMode = RepairMode.dry_run

    issues: list[Issue] = Field(default_factory=list)

    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=lambda: list(DEFAULT_FORBIDDEN_PATHS))

    coder: CoderSpec = Field(default_factory=CoderSpec)
    sandbox: SandboxSpec = Field(default_factory=SandboxSpec)

    # Optional human-approval flag (required to proceed on high risk).
    human_approval: bool = False

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RepairRequest:
        return cls.model_validate(data)


class ChangedFile(BaseModel):
    path: str
    change_type: Literal["added", "modified", "deleted"] = "modified"


class RepairResponse(BaseModel):
    """Repair response returned to the caller / written to repair-response.json."""

    task_id: str
    status: Literal["ok", "blocked", "error", "needs_approval"] = "ok"
    mode: RepairMode = RepairMode.dry_run

    patch_preview: str = ""  # unified diff text
    changed_files: list[ChangedFile] = Field(default_factory=list)
    review: str = ""  # reviewer text/summary

    sandbox_result: dict[str, Any] | None = None
    risk_level: RiskLevel = RiskLevel.low

    pr_url: str | None = None
    messages: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_dict(self) -> dict[str, Any]:
        """Alias for :meth:`to_json` (JSON-serializable dict of the response)."""
        return self.to_json()
