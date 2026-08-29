# gitpilot/toolkit/forge.py
"""Forge tools — Batch V4-A6.

Issues, pull requests and code search on the hosting platform, wrapping
:mod:`gitpilot.github_issues`, :mod:`gitpilot.github_pulls` and
:mod:`gitpilot.github_search`.

Ids are namespaced by platform (``github.*``) rather than a neutral ``forge.*``
because the payload shapes are GitHub's, and pretending otherwise would mean
inventing a lowest common denominator now and discovering it fits nothing later.
GitLab lands as ``gitlab.*`` with the same *tool shapes* when someone needs it;
a topology that grants "the forge can create PRs" does so by naming the
capability (``github.pr.write``), so the grant survives the second platform even
though the tool ids do not.

Reads are SAFE.  Writes are APPROVAL and carry ``FORGE_WRITE`` — they are
visible to other people the moment they land, and no local checkpoint can take
them back.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from .registry import (
    Effect,
    Risk,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)

_PAGE_PROPERTIES = {
    "per_page": {"type": "integer", "minimum": 1, "maximum": 100},
    "page": {"type": "integer", "minimum": 1},
}


def _brief_issue(issue: Dict[str, Any]) -> str:
    return (
        f"#{issue.get('number')} [{issue.get('state')}] {issue.get('title')}"
        f" — {(issue.get('user') or {}).get('login', 'unknown')}"
    )


def _brief_pr(pull: Dict[str, Any]) -> str:
    head = (pull.get("head") or {}).get("ref", "?")
    base = (pull.get("base") or {}).get("ref", "?")
    draft = " (draft)" if pull.get("draft") else ""
    return (
        f"#{pull.get('number')} [{pull.get('state')}]{draft} {pull.get('title')}"
        f" — {head} → {base}"
    )


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

ISSUE_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "state": {"type": "string", "enum": ["open", "closed", "all"]},
        "labels": {"type": "string", "description": "Comma-separated label names."},
        "assignee": {"type": "string"},
        **_PAGE_PROPERTIES,
    },
    "additionalProperties": False,
}


async def _issue_list(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    from ..github_issues import list_issues

    repo = ctx.require_repo()
    issues = await list_issues(
        repo.owner,
        repo.repo,
        state=call.arguments.get("state", "open"),
        labels=call.arguments.get("labels"),
        assignee=call.arguments.get("assignee"),
        per_page=int(call.arguments.get("per_page") or 30),
        page=int(call.arguments.get("page") or 1),
        token=repo.token,
    )
    if not issues:
        return ToolResult.success(call, "No issues matched.", data={"issues": [], "count": 0})
    lines = [_brief_issue(issue) for issue in issues]
    return ToolResult.success(
        call, "\n".join(lines), data={"count": len(issues), "numbers": [i.get("number") for i in issues]},
    )


ISSUE_GET_SCHEMA = {
    "type": "object",
    "properties": {"number": {"type": "integer", "minimum": 1}},
    "required": ["number"],
    "additionalProperties": False,
}


async def _issue_get(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    from ..github_issues import get_issue

    repo = ctx.require_repo()
    issue = await get_issue(repo.owner, repo.repo, int(call.arguments["number"]), token=repo.token)
    body = issue.get("body") or "(no description)"
    content = f"{_brief_issue(issue)}\n\n{body}"
    return ToolResult.success(
        call, content, data={"number": issue.get("number"), "state": issue.get("state")},
    )


ISSUE_CREATE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
        "labels": {"type": "array", "items": {"type": "string"}},
        "assignees": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title"],
    "additionalProperties": False,
}


async def _issue_create(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    from ..github_issues import create_issue

    repo = ctx.require_repo()
    issue = await create_issue(
        repo.owner,
        repo.repo,
        call.arguments["title"],
        body=call.arguments.get("body"),
        labels=call.arguments.get("labels"),
        assignees=call.arguments.get("assignees"),
        token=repo.token,
    )
    return ToolResult.success(
        call,
        f"Created issue #{issue.get('number')}: {issue.get('html_url', '')}",
        data={"number": issue.get("number"), "url": issue.get("html_url")},
    )


ISSUE_COMMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "number": {"type": "integer", "minimum": 1},
        "body": {"type": "string"},
    },
    "required": ["number", "body"],
    "additionalProperties": False,
}


async def _issue_comment(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    from ..github_issues import add_issue_comment

    repo = ctx.require_repo()
    comment = await add_issue_comment(
        repo.owner, repo.repo, int(call.arguments["number"]), call.arguments["body"], token=repo.token,
    )
    return ToolResult.success(
        call,
        f"Commented on #{call.arguments['number']}: {comment.get('html_url', '')}",
        data={"number": call.arguments["number"], "url": comment.get("html_url")},
    )


# ---------------------------------------------------------------------------
# Pull requests
# ---------------------------------------------------------------------------

PR_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "state": {"type": "string", "enum": ["open", "closed", "all"]},
        "base": {"type": "string"},
        "head": {"type": "string"},
        **_PAGE_PROPERTIES,
    },
    "additionalProperties": False,
}


async def _pr_list(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    from ..github_pulls import list_pull_requests

    repo = ctx.require_repo()
    pulls = await list_pull_requests(
        repo.owner,
        repo.repo,
        state=call.arguments.get("state", "open"),
        base=call.arguments.get("base"),
        head=call.arguments.get("head"),
        per_page=int(call.arguments.get("per_page") or 30),
        page=int(call.arguments.get("page") or 1),
        token=repo.token,
    )
    if not pulls:
        return ToolResult.success(call, "No pull requests matched.", data={"count": 0})
    return ToolResult.success(
        call,
        "\n".join(_brief_pr(pull) for pull in pulls),
        data={"count": len(pulls), "numbers": [p.get("number") for p in pulls]},
    )


PR_GET_SCHEMA = ISSUE_GET_SCHEMA


async def _pr_get(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    from ..github_pulls import get_pull_request

    repo = ctx.require_repo()
    pull = await get_pull_request(repo.owner, repo.repo, int(call.arguments["number"]), token=repo.token)
    body = pull.get("body") or "(no description)"
    return ToolResult.success(
        call,
        f"{_brief_pr(pull)}\n\n{body}",
        data={
            "number": pull.get("number"),
            "state": pull.get("state"),
            "mergeable": pull.get("mergeable"),
            "draft": pull.get("draft"),
        },
    )


PR_CREATE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "head": {"type": "string", "description": "Branch containing the changes."},
        "base": {"type": "string", "description": "Branch to merge into."},
        "body": {"type": "string"},
        "draft": {"type": "boolean"},
    },
    "required": ["title", "head", "base"],
    "additionalProperties": False,
}


async def _pr_create(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    from ..github_pulls import create_pull_request

    repo = ctx.require_repo()
    pull = await create_pull_request(
        repo.owner,
        repo.repo,
        title=call.arguments["title"],
        head=call.arguments["head"],
        base=call.arguments["base"],
        body=call.arguments.get("body"),
        draft=bool(call.arguments.get("draft", False)),
        token=repo.token,
    )
    return ToolResult.success(
        call,
        f"Opened PR #{pull.get('number')}: {pull.get('html_url', '')}",
        data={"number": pull.get("number"), "url": pull.get("html_url")},
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

SEARCH_CODE_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "language": {"type": "string"},
        "path": {"type": "string"},
        "this_repo_only": {
            "type": "boolean",
            "description": "Scope the search to the bound repository (default true).",
        },
        **_PAGE_PROPERTIES,
    },
    "required": ["query"],
    "additionalProperties": False,
}


async def _search_code(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    from ..github_search import search_code

    repo = ctx.require_repo()
    scoped = call.arguments.get("this_repo_only", True)
    result = await search_code(
        call.arguments["query"],
        owner=repo.owner if scoped else None,
        repo=repo.repo if scoped else None,
        language=call.arguments.get("language"),
        path=call.arguments.get("path"),
        per_page=int(call.arguments.get("per_page") or 30),
        page=int(call.arguments.get("page") or 1),
        token=repo.token,
    )
    items = result.get("items", []) or []
    if not items:
        return ToolResult.success(call, "No code matched.", data={"count": 0})
    lines = [f"  {item.get('path')} ({(item.get('repository') or {}).get('full_name', '')})" for item in items]
    return ToolResult.success(
        call,
        f"Found {result.get('total_count', len(items))} match(es):\n" + "\n".join(lines),
        data={"count": len(items), "total": result.get("total_count")},
    )


SEARCH_ISSUES_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "state": {"type": "string", "enum": ["open", "closed"]},
        "label": {"type": "string"},
        "is_pr": {"type": "boolean", "description": "Restrict to pull requests."},
        "this_repo_only": {"type": "boolean"},
        **_PAGE_PROPERTIES,
    },
    "required": ["query"],
    "additionalProperties": False,
}


async def _search_issues(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    from ..github_search import search_issues

    repo = ctx.require_repo()
    scoped = call.arguments.get("this_repo_only", True)
    result = await search_issues(
        call.arguments["query"],
        owner=repo.owner if scoped else None,
        repo=repo.repo if scoped else None,
        state=call.arguments.get("state"),
        label=call.arguments.get("label"),
        is_pr=call.arguments.get("is_pr"),
        per_page=int(call.arguments.get("per_page") or 30),
        page=int(call.arguments.get("page") or 1),
        token=repo.token,
    )
    items = result.get("items", []) or []
    if not items:
        return ToolResult.success(call, "No issues matched.", data={"count": 0})
    return ToolResult.success(
        call,
        f"Found {result.get('total_count', len(items))} match(es):\n"
        + "\n".join(f"  {_brief_issue(item)}" for item in items),
        data={"count": len(items), "total": result.get("total_count")},
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

SPECS: List[Tuple[ToolSpec, Any]] = [
    (
        ToolSpec(
            id="github.issue.list",
            title="List issues",
            description="List issues in the repository, filtered by state, label or assignee.",
            params_schema=ISSUE_LIST_SCHEMA,
            capability="github.read",
            risk=Risk.SAFE,
            effects=frozenset({Effect.NETWORK}),
            priority=60,
        ),
        _issue_list,
    ),
    (
        ToolSpec(
            id="github.issue.get",
            title="Read an issue",
            description="Read one issue including its description.",
            params_schema=ISSUE_GET_SCHEMA,
            capability="github.read",
            risk=Risk.SAFE,
            effects=frozenset({Effect.NETWORK}),
            priority=50,
        ),
        _issue_get,
    ),
    (
        ToolSpec(
            id="github.issue.create",
            title="Open an issue",
            description="Open a new issue. Visible to everyone with access to the repository.",
            params_schema=ISSUE_CREATE_SCHEMA,
            capability="github.issue.write",
            risk=Risk.APPROVAL,
            effects=frozenset({Effect.FORGE_WRITE, Effect.NETWORK}),
            priority=70,
        ),
        _issue_create,
    ),
    (
        ToolSpec(
            id="github.issue.comment",
            title="Comment on an issue",
            description="Add a comment to an issue or pull request.",
            params_schema=ISSUE_COMMENT_SCHEMA,
            capability="github.issue.write",
            risk=Risk.APPROVAL,
            effects=frozenset({Effect.FORGE_WRITE, Effect.NETWORK}),
            priority=70,
        ),
        _issue_comment,
    ),
    (
        ToolSpec(
            id="github.pr.list",
            title="List pull requests",
            description="List pull requests, filtered by state or branch.",
            params_schema=PR_LIST_SCHEMA,
            capability="github.read",
            risk=Risk.SAFE,
            effects=frozenset({Effect.NETWORK}),
            priority=60,
        ),
        _pr_list,
    ),
    (
        ToolSpec(
            id="github.pr.get",
            title="Read a pull request",
            description="Read one pull request including its description and merge state.",
            params_schema=PR_GET_SCHEMA,
            capability="github.read",
            risk=Risk.SAFE,
            effects=frozenset({Effect.NETWORK}),
            priority=50,
        ),
        _pr_get,
    ),
    (
        ToolSpec(
            id="github.pr.create",
            title="Open a pull request",
            description=(
                "Open a pull request from one branch into another. Push the branch first. "
                "Visible to reviewers immediately."
            ),
            params_schema=PR_CREATE_SCHEMA,
            capability="github.pr.write",
            risk=Risk.APPROVAL,
            effects=frozenset({Effect.FORGE_WRITE, Effect.NETWORK}),
            priority=70,
        ),
        _pr_create,
    ),
    (
        ToolSpec(
            id="github.search.code",
            title="Search code on GitHub",
            description=(
                "Search code through GitHub's index. Prefer fs.grep for the current "
                "repository; use this to look across repositories."
            ),
            params_schema=SEARCH_CODE_SCHEMA,
            capability="github.read",
            risk=Risk.SAFE,
            effects=frozenset({Effect.NETWORK}),
            priority=80,
        ),
        _search_code,
    ),
    (
        ToolSpec(
            id="github.search.issues",
            title="Search issues and pull requests",
            description="Search issues and pull requests by text and qualifiers.",
            params_schema=SEARCH_ISSUES_SCHEMA,
            capability="github.read",
            risk=Risk.SAFE,
            effects=frozenset({Effect.NETWORK}),
            priority=80,
        ),
        _search_issues,
    ),
]


#: GitLab is not implemented.  The tool *shapes* above transfer directly — list
#: / get / create over issues and merge requests — but GitPilot has no GitLab
#: client, no token plumbing and no ``RepoBinding`` variant that knows about
#: projects instead of owner/repo.  Registering half of that would give a model
#: tools that fail on the first call, so nothing is registered: an absent
#: provider means an absent tool (see :mod:`gitpilot.toolkit.web` for the same
#: rule applied to web search).  ``gitlab.mr.*`` is the naming to use when it
#: lands, so that ``github.pr.write`` and ``gitlab.mr.write`` can both satisfy
#: one topology capability.
GITLAB_SPECS: List[Tuple[ToolSpec, Any]] = []


def gitlab_available() -> bool:
    """Whether a GitLab client is configured.  Always ``False`` today."""
    return False


def register(registry: ToolRegistry) -> None:
    """Add the forge tools to *registry*."""
    for spec, handler in SPECS:
        registry.register(spec, handler)
    if gitlab_available():   # pragma: no cover - unreachable until GitLab lands
        for spec, handler in GITLAB_SPECS:
            registry.register(spec, handler)
