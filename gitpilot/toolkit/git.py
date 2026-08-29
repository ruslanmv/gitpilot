# gitpilot/toolkit/git.py
"""Git tools — Batch V4-A5.

``git.status``, ``git.diff``, ``git.log``, ``git.branch``, ``git.commit``,
``git.push``, ``git.stash`` — all wrapping
:class:`gitpilot.workspace.WorkspaceManager`, which shells out to real git and
parses porcelain output.

These are local-checkout tools.  A GitHub-API session has no working tree to
diff or commit, and inventing an approximation would be worse than a clean
refusal: a model told "no local checkout" can ask for one, while a model handed
a plausible-looking empty diff draws the wrong conclusion and keeps going.

Risk grading follows how hard the action is to undo.  Reads are SAFE; commit,
branch and stash are APPROVAL because they change local state a checkpoint can
restore; ``git.push`` is HIGH_RISK because it leaves the machine and no
checkpoint of ours reaches a remote.
"""
from __future__ import annotations

import json
from typing import Any, List, Tuple

from .registry import (
    Effect,
    Risk,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)

NO_ARGS_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


def _manager():
    from ..workspace import WorkspaceManager

    return WorkspaceManager()


def _info(ctx: ToolExecutionContext):
    from ..workspace import WorkspaceInfo

    ws = ctx.require_workspace()
    repo = ctx.repo
    return WorkspaceInfo(
        owner=repo.owner if repo else "local",
        repo=repo.repo if repo else ws.root.name,
        path=ws.root,
        branch=(repo.branch if repo else None) or "HEAD",
        remote_url="",
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def _git_status(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    try:
        status = await _manager().status(_info(ctx))
    except Exception as exc:  # noqa: BLE001 - matches local_tools.git_status
        return ToolResult.failure(call, f"Error: {exc}", error="git_failed")
    return ToolResult.success(call, json.dumps(status, indent=2), data=status)


DIFF_SCHEMA = {
    "type": "object",
    "properties": {
        "staged": {
            "type": "boolean",
            "description": "Show staged changes instead of unstaged ones.",
        },
    },
    "additionalProperties": False,
}


async def _git_diff(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    staged = bool(call.arguments.get("staged", False))
    try:
        diff = await _manager().diff(_info(ctx), staged=staged)
    except Exception as exc:  # noqa: BLE001 - matches local_tools.git_diff
        return ToolResult.failure(call, f"Error: {exc}", error="git_failed")
    return ToolResult.success(
        call,
        diff or "No changes.",
        data={"staged": staged, "empty": not diff, "bytes": len(diff or "")},
    )


LOG_SCHEMA = {
    "type": "object",
    "properties": {
        "count": {"type": "integer", "description": "How many commits.", "minimum": 1, "maximum": 200},
    },
    "additionalProperties": False,
}


async def _git_log(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    count = int(call.arguments.get("count") or 10)
    try:
        commits = await _manager().log(_info(ctx), count)
    except Exception as exc:  # noqa: BLE001 - matches local_tools.git_log
        return ToolResult.failure(call, f"Error: {exc}", error="git_failed")
    return ToolResult.success(
        call, json.dumps(commits, indent=2), data={"commits": commits, "count": len(commits)},
    )


# ---------------------------------------------------------------------------
# Local mutations
# ---------------------------------------------------------------------------

BRANCH_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "New branch name."},
    },
    "required": ["name"],
    "additionalProperties": False,
}


async def _git_branch(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    name = call.arguments["name"]
    try:
        created = await _manager().create_branch(_info(ctx), name)
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(call, f"Error: {exc}", error="git_failed")
    return ToolResult.success(call, f"Switched to a new branch '{created}'", data={"branch": created})


COMMIT_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string", "description": "Commit message."},
        "files": {
            "type": "array",
            "description": "Paths to stage. Omit to stage every change.",
            "items": {"type": "string"},
        },
    },
    "required": ["message"],
    "additionalProperties": False,
}


async def _git_commit(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    message = call.arguments["message"]
    files = call.arguments.get("files") or None
    try:
        result = await _manager().commit(_info(ctx), message, list(files) if files else None)
    except Exception as exc:  # noqa: BLE001 - matches local_tools.git_commit
        return ToolResult.failure(call, f"Error: {exc}", error="git_failed")
    return ToolResult.success(call, json.dumps(result), data=result)


STASH_SCHEMA = {
    "type": "object",
    "properties": {
        "pop": {"type": "boolean", "description": "Restore the most recent stash instead of creating one."},
    },
    "additionalProperties": False,
}


async def _git_stash(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    pop = bool(call.arguments.get("pop", False))
    try:
        output = await _manager().stash(_info(ctx), pop=pop)
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(call, f"Error: {exc}", error="git_failed")
    return ToolResult.success(call, output or ("Stash popped." if pop else "Stashed."), data={"pop": pop})


# ---------------------------------------------------------------------------
# Remote mutation
# ---------------------------------------------------------------------------

PUSH_SCHEMA = {
    "type": "object",
    "properties": {
        "force": {
            "type": "boolean",
            "description": "Force-push with --force-with-lease. Refuses if the remote moved.",
        },
    },
    "additionalProperties": False,
}


async def _git_push(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    force = bool(call.arguments.get("force", False))
    try:
        result = await _manager().push(_info(ctx), force=force)
    except Exception as exc:  # noqa: BLE001
        return ToolResult.failure(call, f"Error: {exc}", error="git_failed")
    return ToolResult.success(call, json.dumps(result), data={**result, "force": force})


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

# ``git.status``/``git.diff``/``git.log`` declared ``GIT_LOCAL`` until Batch
# V4-G3.  They do not change local git state — they report it — and the effect set
# is what the read-only gate reads, so declaring it made every read-only run
# unable to look at the repository it was reviewing.  ``GIT_LOCAL`` now means what
# its name says: this call changes the local repository.
SPECS: List[Tuple[ToolSpec, Any]] = [
    (
        ToolSpec(
            id="git.status",
            title="Git status",
            description="Report the working tree's state: branch, staged, unstaged and untracked files.",
            params_schema=NO_ARGS_SCHEMA,
            capability="git.read",
            risk=Risk.SAFE,
            effects=frozenset({Effect.READS_FS}),
            priority=30,
        ),
        _git_status,
    ),
    (
        ToolSpec(
            id="git.diff",
            title="Git diff",
            description=(
                "Show the current changes as a unified diff. Read this before committing "
                "to check that the change is the one you intended."
            ),
            params_schema=DIFF_SCHEMA,
            capability="git.read",
            risk=Risk.SAFE,
            effects=frozenset({Effect.READS_FS}),
            priority=30,
        ),
        _git_diff,
    ),
    (
        ToolSpec(
            id="git.log",
            title="Git log",
            description="List recent commits with author, date and subject.",
            params_schema=LOG_SCHEMA,
            capability="git.read",
            risk=Risk.SAFE,
            effects=frozenset({Effect.READS_FS}),
            priority=50,
        ),
        _git_log,
    ),
    (
        ToolSpec(
            id="git.branch",
            title="Create a branch",
            description="Create a branch and switch to it.",
            params_schema=BRANCH_SCHEMA,
            risk=Risk.APPROVAL,
            effects=frozenset({Effect.GIT_LOCAL}),
            priority=50,
        ),
        _git_branch,
    ),
    (
        ToolSpec(
            id="git.commit",
            title="Commit",
            description="Stage changes and commit them. Returns the new commit's sha.",
            params_schema=COMMIT_SCHEMA,
            risk=Risk.APPROVAL,
            effects=frozenset({Effect.GIT_LOCAL, Effect.WRITES_FS}),
            priority=40,
        ),
        _git_commit,
    ),
    (
        ToolSpec(
            id="git.stash",
            title="Stash changes",
            description="Stash the working tree, or restore the most recent stash.",
            params_schema=STASH_SCHEMA,
            risk=Risk.APPROVAL,
            effects=frozenset({Effect.GIT_LOCAL, Effect.WRITES_FS}),
            priority=70,
        ),
        _git_stash,
    ),
    (
        ToolSpec(
            id="git.push",
            title="Push",
            description=(
                "Push the current branch to its remote. This leaves the machine: no "
                "local checkpoint can undo it."
            ),
            params_schema=PUSH_SCHEMA,
            risk=Risk.HIGH_RISK,
            effects=frozenset({Effect.GIT_REMOTE, Effect.NETWORK}),
            priority=80,
        ),
        _git_push,
    ),
]


def register(registry: ToolRegistry) -> None:
    """Add the git tools to *registry*."""
    for spec, handler in SPECS:
        registry.register(spec, handler)
