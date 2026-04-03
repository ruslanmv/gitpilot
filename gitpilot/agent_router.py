# gitpilot/agent_router.py
"""Intelligent Agent Router for GitPilot.

Classifies user requests and delegates them to the appropriate specialised
agent (or a pipeline of agents).  The router itself does **not** use an LLM;
it relies on lightweight keyword / pattern matching so that routing is
instantaneous and deterministic.

The router returns a *WorkflowPlan* describing which agents should run and
in what order.  The actual agent execution is handled by the orchestrator
in ``agentic.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class AgentType(str, Enum):
    """Available specialised agents."""

    EXPLORER = "explorer"
    PLANNER = "planner"
    CODE_WRITER = "code_writer"
    CODE_REVIEWER = "code_reviewer"
    ISSUE_MANAGER = "issue_manager"
    PR_MANAGER = "pr_manager"
    SEARCH = "search"
    LEARNING = "learning"
    LOCAL_EDITOR = "local_editor"       # Phase 1: local file editing + shell
    TERMINAL = "terminal"              # Phase 1: dedicated terminal agent


class RequestCategory(str, Enum):
    """High-level intent category inferred from the user request."""

    PLAN_EXECUTE = "plan_execute"       # Existing explore -> plan -> execute workflow
    ISSUE_MANAGEMENT = "issue_management"
    PR_MANAGEMENT = "pr_management"
    CODE_SEARCH = "code_search"
    CODE_REVIEW = "code_review"
    LEARNING = "learning"
    CONVERSATIONAL = "conversational"   # Free-form chat / Q&A about the repo
    LOCAL_EDIT = "local_edit"           # Phase 1: direct file editing with verification
    TERMINAL = "terminal"              # Phase 1: shell command execution


@dataclass
class WorkflowPlan:
    """Describes which agents to invoke and in what order."""

    category: RequestCategory
    agents: List[AgentType]
    description: str
    requires_repo_context: bool = True
    # If the request mentions a specific issue/PR number, capture it.
    entity_number: Optional[int] = None
    # Additional metadata extracted from the request.
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pattern definitions (order matters -- first match wins)
# ---------------------------------------------------------------------------

_ISSUE_CREATE_RE = re.compile(
    r"\b(create|open|new|file|add)\b.*\bissue\b", re.IGNORECASE
)
_ISSUE_UPDATE_RE = re.compile(
    r"\b(update|modify|edit|change|close|reopen|label|assign|milestone)\b.*\bissue\b",
    re.IGNORECASE,
)
_ISSUE_LIST_RE = re.compile(
    r"\b(list|show|get|find|search)\b.*\bissues?\b", re.IGNORECASE
)
_ISSUE_COMMENT_RE = re.compile(
    r"\b(comment|reply|respond)\b.*\bissue\b", re.IGNORECASE
)
_ISSUE_NUMBER_RE = re.compile(r"#(\d+)")

_PR_CREATE_RE = re.compile(
    r"\b(create|open|new|make)\b.*\b(pull request|pr|pull)\b", re.IGNORECASE
)
_PR_MERGE_RE = re.compile(
    r"\b(merge|squash|rebase)\b.*\b(pull request|pr|pull)\b", re.IGNORECASE
)
_PR_REVIEW_RE = re.compile(
    r"\b(review|approve|request changes)\b.*\b(pull request|pr|pull)\b",
    re.IGNORECASE,
)
_PR_LIST_RE = re.compile(
    r"\b(list|show|get|find)\b.*\b(pull requests?|prs?|pulls?)\b", re.IGNORECASE
)

_SEARCH_CODE_RE = re.compile(
    r"\b(search|find|locate|grep|look for)\b.*\b(code|function|class|symbol|pattern|file)\b",
    re.IGNORECASE,
)
_SEARCH_USER_RE = re.compile(
    r"\b(search|find|who)\b.*\b(user|developer|org|organization|contributor)\b",
    re.IGNORECASE,
)
_SEARCH_REPO_RE = re.compile(
    r"\b(search|find|discover)\b.*\b(repo|repository|project)\b", re.IGNORECASE
)

_TERMINAL_RE = re.compile(
    r"\b(run|execute|launch)\b.*\b(command|test|tests|script|build|lint|npm|pip|make|docker|pytest|cargo|go)\b",
    re.IGNORECASE,
)
_LOCAL_EDIT_RE = re.compile(
    r"\b(edit|modify|change|update|fix|write|rewrite|patch)\b.*\b(file|code|function|class|method|module|line|lines)\b",
    re.IGNORECASE,
)

_REVIEW_RE = re.compile(
    r"\b(review|analyze|audit|check|inspect)\b.*\b(code|quality|security|performance)\b",
    re.IGNORECASE,
)

_LEARNING_RE = re.compile(
    r"\b(how (do|can|to)|explain|what is|guide|tutorial|best practice|help with)\b",
    re.IGNORECASE,
)
_GITHUB_TOPICS_RE = re.compile(
    r"\b(actions?|workflow|ci/?cd|pages?|packages?|discussions?|authentication|deploy|release)\b",
    re.IGNORECASE,
)


def _extract_issue_number(text: str) -> Optional[int]:
    m = _ISSUE_NUMBER_RE.search(text)
    if m:
        return int(m.group(1))
    # Also try "issue 42" / "issue number 42"
    m2 = re.search(r"\bissue\s*(?:number\s*)?(\d+)\b", text, re.IGNORECASE)
    return int(m2.group(1)) if m2 else None


def _extract_pr_number(text: str) -> Optional[int]:
    m = re.search(r"\b(?:pr|pull request|pull)\s*#?(\d+)\b", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def route(user_request: str) -> WorkflowPlan:
    """Classify *user_request* and return a ``WorkflowPlan``."""
    text = user_request.strip()

    # --- Issue management ------------------------------------------------
    if _ISSUE_CREATE_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.ISSUE_MANAGEMENT,
            agents=[AgentType.ISSUE_MANAGER],
            description="Create a new GitHub issue",
            entity_number=_extract_issue_number(text),
            metadata={"action": "create"},
        )
    if _ISSUE_COMMENT_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.ISSUE_MANAGEMENT,
            agents=[AgentType.ISSUE_MANAGER],
            description="Comment on an issue",
            entity_number=_extract_issue_number(text),
            metadata={"action": "comment"},
        )
    if _ISSUE_UPDATE_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.ISSUE_MANAGEMENT,
            agents=[AgentType.ISSUE_MANAGER],
            description="Update an existing issue",
            entity_number=_extract_issue_number(text),
            metadata={"action": "update"},
        )
    if _ISSUE_LIST_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.ISSUE_MANAGEMENT,
            agents=[AgentType.ISSUE_MANAGER],
            description="List or search issues",
            metadata={"action": "list"},
        )

    # --- PR management ---------------------------------------------------
    if _PR_CREATE_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.PR_MANAGEMENT,
            agents=[AgentType.PR_MANAGER],
            description="Create a pull request",
            metadata={"action": "create"},
        )
    if _PR_MERGE_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.PR_MANAGEMENT,
            agents=[AgentType.PR_MANAGER],
            description="Merge a pull request",
            entity_number=_extract_pr_number(text),
            metadata={"action": "merge"},
        )
    if _PR_REVIEW_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.PR_MANAGEMENT,
            agents=[AgentType.CODE_REVIEWER, AgentType.PR_MANAGER],
            description="Review a pull request",
            entity_number=_extract_pr_number(text),
            metadata={"action": "review"},
        )
    if _PR_LIST_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.PR_MANAGEMENT,
            agents=[AgentType.PR_MANAGER],
            description="List pull requests",
            metadata={"action": "list"},
        )

    # --- Code search -----------------------------------------------------
    if _SEARCH_USER_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.CODE_SEARCH,
            agents=[AgentType.SEARCH],
            description="Search for GitHub users or organisations",
            requires_repo_context=False,
            metadata={"search_type": "users"},
        )
    if _SEARCH_REPO_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.CODE_SEARCH,
            agents=[AgentType.SEARCH],
            description="Search for repositories",
            requires_repo_context=False,
            metadata={"search_type": "repositories"},
        )
    if _SEARCH_CODE_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.CODE_SEARCH,
            agents=[AgentType.SEARCH],
            description="Search for code in the repository",
            metadata={"search_type": "code"},
        )

    # --- Terminal / shell commands ----------------------------------------
    if _TERMINAL_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.TERMINAL,
            agents=[AgentType.TERMINAL],
            description="Run shell commands in the workspace",
            metadata={"action": "execute"},
        )

    # --- Local file editing -----------------------------------------------
    if _LOCAL_EDIT_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.LOCAL_EDIT,
            agents=[AgentType.LOCAL_EDITOR],
            description="Edit files directly in the local workspace",
        )

    # --- Code review -----------------------------------------------------
    if _REVIEW_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.CODE_REVIEW,
            agents=[AgentType.EXPLORER, AgentType.CODE_REVIEWER],
            description="Analyse code quality and suggest improvements",
        )

    # --- Learning & guidance ---------------------------------------------
    if _LEARNING_RE.search(text) or _GITHUB_TOPICS_RE.search(text):
        return WorkflowPlan(
            category=RequestCategory.LEARNING,
            agents=[AgentType.LEARNING],
            description="Provide guidance on GitHub features or best practices",
            requires_repo_context=False,
        )

    # --- Default: existing plan+execute workflow -------------------------
    return WorkflowPlan(
        category=RequestCategory.PLAN_EXECUTE,
        agents=[AgentType.EXPLORER, AgentType.PLANNER, AgentType.CODE_WRITER],
        description="Explore repository, create plan, and execute changes",
    )
