"""Tool handler implementations for the GitPilot MCP server.

Each handler is a thin facade over an existing GitPilot module. The goal
is *zero* duplication of business logic: handlers call the same code
that the FastAPI routes call. Handlers are also defensive — if an
optional GitPilot subsystem is missing (e.g. github_api in offline
mode), they degrade to a clear error payload rather than crashing the
MCP transport.

This module is imported from :mod:`gitpilot.api` only when the MCP
server is enabled, so its side-effect-free imports never run in the
default off-by-default path.
"""
from __future__ import annotations

import logging
from typing import Any

from .mcp_server import (
    TOOL_CATALOG,
    CallContext,
    GitPilotMCPServer,
    MCPServerError,
)
from .version import __version__

logger = logging.getLogger(__name__)


def _safe_import(modname: str):
    """Import a module by name, returning None if not available."""
    try:
        return __import__(modname, fromlist=["_"])
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Read-only handlers
# ---------------------------------------------------------------------------
async def _healthz(args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "tools": [t.name for t in TOOL_CATALOG],
    }


async def _list_repos(args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
    page = int(args.get("page", 1))
    per_page = max(1, min(int(args.get("per_page", 30)), 100))
    query = args.get("query")

    gh = _safe_import("gitpilot.github_api")
    if gh is None or not hasattr(gh, "list_repositories"):
        return {
            "available": False,
            "reason": "github_api unavailable; configure GitHub auth in GitPilot",
            "repos": [],
        }
    try:
        repos = await gh.list_repositories(  # type: ignore[attr-defined]
            page=page, per_page=per_page, query=query
        )
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc), "repos": []}

    # Trim to what HomePilot's wizard step 4 needs.
    trimmed = [
        {
            "owner": r.get("owner", {}).get("login") or r.get("owner"),
            "name": r.get("name"),
            "default_branch": r.get("default_branch", "main"),
            "private": bool(r.get("private", False)),
            "updated_at": r.get("updated_at"),
        }
        for r in (repos or [])
    ]
    return {"available": True, "page": page, "per_page": per_page, "repos": trimmed}


async def _list_branches(args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
    owner = args["owner"]
    repo = args["repo"]
    gh = _safe_import("gitpilot.github_api")
    if gh is None or not hasattr(gh, "list_branches"):
        return {"available": False, "branches": []}
    try:
        branches = await gh.list_branches(owner, repo)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc), "branches": []}
    return {
        "available": True,
        "owner": owner,
        "repo": repo,
        "branches": [
            {"name": b.get("name"), "protected": bool(b.get("protected", False))}
            for b in (branches or [])
        ],
    }


async def _describe_repo(args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
    owner = args["owner"]
    repo = args["repo"]
    branch = args.get("branch")
    max_files = max(1, min(int(args.get("max_files", 50)), 500))

    gh = _safe_import("gitpilot.github_api")
    if gh is None:
        return {"available": False, "reason": "github_api unavailable"}

    summary: dict[str, Any] = {"owner": owner, "repo": repo, "branch": branch}
    try:
        if hasattr(gh, "get_repo"):
            meta = await gh.get_repo(owner, repo)  # type: ignore[attr-defined]
            summary["description"] = meta.get("description")
            summary["default_branch"] = meta.get("default_branch")
            summary["language"] = meta.get("language")
            summary["topics"] = meta.get("topics", [])
        if hasattr(gh, "get_tree"):
            tree = await gh.get_tree(  # type: ignore[attr-defined]
                owner, repo, branch=branch, max_items=max_files
            )
            summary["tree"] = tree
    except Exception as exc:  # noqa: BLE001
        summary["error"] = str(exc)
    return summary


async def _list_skills(args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
    skills_mod = _safe_import("gitpilot.skills")
    if skills_mod is None:
        return {"available": False, "skills": []}
    manager_cls = getattr(skills_mod, "SkillManager", None)
    if manager_cls is None:
        return {"available": False, "skills": []}
    # ``list_skills`` is the real method (Batch V4-0B).  The previous
    # ``list``/``all`` probe matched nothing, so this tool always reported an
    # empty catalogue — silently, which is worse than an error.  The aliases
    # stay as fallbacks for third-party managers.
    try:
        manager = manager_cls()
        for candidate in ("list_skills", "list", "all"):
            lister = getattr(manager, candidate, None)
            if callable(lister):
                entries = lister()
                break
        else:
            return {
                "available": False,
                "reason": f"{manager_cls.__name__} exposes no skill listing method",
                "skills": [],
            }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc), "skills": []}

    serialised: list[dict[str, Any]] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            serialised.append(entry)
        else:
            serialised.append(
                {
                    "slug": getattr(entry, "slug", None),
                    "name": getattr(entry, "name", None),
                    "description": getattr(entry, "description", None),
                }
            )
    return {"available": True, "skills": serialised}


async def _classify_topology(args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
    prompt = args["prompt"]
    topology_mod = _safe_import("gitpilot.topology_registry")
    if topology_mod is None:
        return {"available": False, "topology_id": None}
    classify_fn = getattr(topology_mod, "classify", None) or getattr(
        topology_mod, "classify_prompt", None
    )
    if classify_fn is None:
        return {"available": False, "topology_id": None}
    try:
        result = classify_fn(prompt)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc), "topology_id": None}
    if isinstance(result, dict):
        return {"available": True, **result}
    return {"available": True, "topology_id": str(result)}


# ---------------------------------------------------------------------------
# Plan / execute (read-mostly; exposed at SCOPE_PLAN)
# ---------------------------------------------------------------------------
async def _plan(args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
    """Produce a read-only plan via :func:`gitpilot.agentic.generate_plan`.

    Batch V4-0B: this probed for ``agentic.build_plan``/``agentic.plan``, and
    neither has ever existed — the real entrypoint is ``generate_plan`` — so
    the tool reported ``no plan() entrypoint`` on every call.  The kwargs it
    passed (``owner``/``repo``/``branch``/``workspace_root``) did not match
    that function either, so simply renaming the lookup would still have
    failed; the call is now built against the real signature.

    ``workspace_root`` is accepted by the declared schema but unused: planning
    runs against a GitHub repository, not a local checkout.
    """
    prompt = args["prompt"]
    agentic = _safe_import("gitpilot.agentic")
    if agentic is None:
        return {"available": False, "reason": "agentic engine not loaded"}

    plan_fn = (
        getattr(agentic, "generate_plan", None)
        or getattr(agentic, "build_plan", None)   # third-party/legacy aliases
        or getattr(agentic, "plan", None)
    )
    if plan_fn is None:
        return {"available": False, "reason": "no plan() entrypoint"}

    owner, repo = args.get("owner"), args.get("repo")
    if not owner or not repo:
        return {
            "available": False,
            "reason": "owner and repo are required to plan against a repository",
        }

    try:
        plan = await _maybe_await(
            plan_fn(prompt, f"{owner}/{repo}", branch_name=args.get("branch"))
        )
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}

    if hasattr(plan, "model_dump"):
        plan = plan.model_dump()
    return {"available": True, "plan": plan, "call_id": ctx.call_id}


async def _execute(args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
    """Report honestly that plan-id execution is not implemented.

    Batch V4-0B: this probed ``agentic.execute_plan``, found it, and then
    called it with ``plan_id=``/``approval_token=`` — arguments it does not
    take.  The real function needs the plan *object* plus a repository, and
    GitPilot persists no server-side plan store to resolve an id against, so
    the declared contract cannot be honoured by any renaming.  Callers got a
    ``TypeError`` string that read like a bug in their request.

    Until the agentic runtime lands (a run id replaces the plan id, and
    ``/api/v2/agent/*`` becomes the execution surface), this returns a reason
    that says what to use instead.
    """
    _ = args.get("plan_id")
    return {
        "available": False,
        "reason": (
            "gitpilot.execute is not implemented: plans are not persisted "
            "server-side, so a plan_id cannot be resolved. Use POST "
            "/api/chat/execute with the plan object returned by gitpilot.plan."
        ),
        "call_id": ctx.call_id,
    }


# ---------------------------------------------------------------------------
# Mutation handlers
# ---------------------------------------------------------------------------
async def _run_skill(args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
    slug = args["slug"]
    arguments = dict(args.get("arguments", {}))
    skills_mod = _safe_import("gitpilot.skills")
    if skills_mod is None:
        return {"available": False, "reason": "skills module not loaded"}
    manager_cls = getattr(skills_mod, "SkillManager", None)
    if manager_cls is None:
        return {"available": False, "reason": "SkillManager unavailable"}
    try:
        manager = manager_cls()
        run_fn = getattr(manager, "run", None) or getattr(manager, "invoke", None)
        if run_fn is None:
            return {"available": False, "reason": "no run() on SkillManager"}
        result = await _maybe_await(run_fn(slug, arguments))
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}
    return {"available": True, "slug": slug, "result": result}


async def _create_pr(args: dict[str, Any], ctx: CallContext) -> dict[str, Any]:
    pr_mod = _safe_import("gitpilot.pr_tools") or _safe_import("gitpilot.github_pulls")
    if pr_mod is None:
        return {"available": False, "reason": "pr tools not loaded"}
    create_fn = getattr(pr_mod, "create_pull_request", None) or getattr(
        pr_mod, "create_pr", None
    )
    if create_fn is None:
        return {"available": False, "reason": "no create_pr() entrypoint"}
    try:
        result = await _maybe_await(
            create_fn(
                owner=args["owner"],
                repo=args["repo"],
                head=args["head"],
                base=args.get("base", "main"),
                title=args["title"],
                body=args.get("body", ""),
            )
        )
    except TypeError:
        # Older positional API.
        try:
            result = await _maybe_await(
                create_fn(
                    args["owner"],
                    args["repo"],
                    args["head"],
                    args.get("base", "main"),
                    args["title"],
                    args.get("body", ""),
                )
            )
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}
    return {"available": True, "pull_request": result}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _maybe_await(value):
    """Await ``value`` if it's a coroutine, otherwise return it."""
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value


# ---------------------------------------------------------------------------
# Public binder
# ---------------------------------------------------------------------------
HANDLER_TABLE = {
    "gitpilot.healthz": _healthz,
    "gitpilot.list_repos": _list_repos,
    "gitpilot.list_branches": _list_branches,
    "gitpilot.describe_repo": _describe_repo,
    "gitpilot.list_skills": _list_skills,
    "gitpilot.classify_topology": _classify_topology,
    "gitpilot.plan": _plan,
    "gitpilot.execute": _execute,
    "gitpilot.run_skill": _run_skill,
    "gitpilot.create_pr": _create_pr,
}


def bind(server: GitPilotMCPServer) -> GitPilotMCPServer:
    """Wire all handlers into a :class:`GitPilotMCPServer` instance."""
    catalog_names = {t.name for t in TOOL_CATALOG}
    for name, handler in HANDLER_TABLE.items():
        if name not in catalog_names:
            raise MCPServerError(
                f"Handler {name!r} is not in the public TOOL_CATALOG"
            )
        server.register_handler(name, handler)
    return server
