# gitpilot/toolkit/fs.py
"""Filesystem tools — Batch V4-A3.

Seven canonical tools (``fs.read``, ``fs.list``, ``fs.glob``, ``fs.grep``,
``fs.write``, ``fs.edit``, ``fs.delete``), each with two backends chosen by the
run's context: an on-disk checkout, or a GitHub repository reached over the API.
One id either way — the model should not have to know which kind of session it
is in, and a topology that grants ``fs.write`` means the same thing in both.

Every handler wraps the implementation the CrewAI tools already use
(:class:`gitpilot.workspace.WorkspaceManager`, :mod:`gitpilot.github_api`,
:mod:`gitpilot.edit_backend`, :mod:`gitpilot.grep_backend`,
:mod:`gitpilot.glob_match`) and reproduces its output text exactly, so the
parity gate in ``tests/toolkit/parity`` can prove the migration changes nothing
a model reads.  Where the two legacy surfaces worded an error differently, both
wordings are kept rather than unified: an invented third format would be a
behaviour change smuggled in under a refactor.

What is new here is the ``data`` payload on every result — the diff, the byte
counts, the paths written — so events, the journal and the UI stop re-parsing
prose.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from ..glob_match import GLOB_DEFAULT_MAX_RESULTS, GLOB_HARD_MAX_RESULTS, glob_match, glob_to_regex
from .registry import (
    Effect,
    Risk,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)

#: Mirrors ``agent_tools.grep_repository``: 200 paths at ~50 KB each is already
#: 10 MB of fetches, and beyond that the caller should narrow ``path_pattern``.
FILE_FETCH_CAP = 200


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _workspace_manager():
    from ..workspace import WorkspaceManager

    return WorkspaceManager()


def _workspace_info(ctx: ToolExecutionContext):
    """Adapt a :class:`LocalWorkspace` to the ``WorkspaceInfo`` the manager takes.

    ``WorkspaceManager`` only reads ``.path`` for file operations; owner/repo
    carry provenance for git remotes, which the fs tools never touch.
    """
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


async def _repo_tree(ctx: ToolExecutionContext) -> List[Dict[str, Any]]:
    from ..github_api import get_repo_tree

    repo = ctx.require_repo()
    return await get_repo_tree(repo.owner, repo.repo, token=repo.token, ref=repo.branch)


def _unified_diff(path: str, before: str, after: str, *, limit: int = 20_000) -> str:
    """A unified diff for the ``data`` payload, so the UI need not re-derive it.

    Not shown to the model — it already knows what it asked for, and the diff
    would double the observation's token cost.  Truncated because a generated
    file can be enormous and this rides on every event.
    """
    import difflib

    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    if len(diff) > limit:
        return diff[:limit] + f"\n… diff truncated at {limit} bytes\n"
    return diff


# ---------------------------------------------------------------------------
# fs.read
# ---------------------------------------------------------------------------

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path relative to the repository root, e.g. 'src/main.py'.",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


async def _fs_read(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    path = call.arguments["path"]

    if ctx.is_local:
        try:
            content = await _workspace_manager().read_file(_workspace_info(ctx), path)
        except Exception as exc:  # noqa: BLE001 - matches read_local_file
            return ToolResult.failure(
                call, f"Error reading {path}: {exc}", error="read_failed",
            )
    else:
        from ..github_api import get_file

        repo = ctx.require_repo()
        try:
            content = await get_file(repo.owner, repo.repo, path, token=repo.token, ref=repo.branch)
        except Exception as exc:  # noqa: BLE001 - matches agent_tools.read_file
            return ToolResult.failure(
                call, f"Error reading file {path}: {exc}", error="read_failed",
            )

    return ToolResult.success(
        call,
        f"Content of {path}:\n---\n{content}\n---",
        data={"path": path, "bytes": len(content or ""), "lines": len((content or "").splitlines())},
    )


# ---------------------------------------------------------------------------
# fs.list
# ---------------------------------------------------------------------------

LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "directory": {
            "type": "string",
            "description": "Directory to list, relative to the root. Defaults to the whole repository.",
        },
    },
    "additionalProperties": False,
}


async def _fs_list(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    directory = call.arguments.get("directory") or "."

    if ctx.is_local:
        try:
            files = await _workspace_manager().list_files(_workspace_info(ctx), directory)
        except Exception as exc:  # noqa: BLE001 - matches list_local_files
            return ToolResult.failure(call, f"Error listing files: {exc}", error="list_failed")
        content = "\n".join(files) if files else "No files found."
        return ToolResult.success(call, content, data={"paths": files, "count": len(files)})

    repo = ctx.require_repo()
    try:
        tree = await _repo_tree(ctx)
    except Exception as exc:  # noqa: BLE001 - matches list_repository_files
        return ToolResult.failure(call, f"Error listing files: {exc}", error="list_failed")

    if not tree:
        return ToolResult.success(
            call,
            f"Repository is empty - no files found. (Branch: {repo.branch})",
            data={"paths": [], "count": 0},
        )

    paths = sorted(item["path"] for item in tree)
    body = "".join(f"  - {p}\n" for p in paths)
    return ToolResult.success(
        call,
        f"Repository: {repo.full_name} (Branch: {repo.branch})\nFiles:\n{body}",
        data={"paths": paths, "count": len(paths)},
    )


# ---------------------------------------------------------------------------
# fs.glob
# ---------------------------------------------------------------------------

GLOB_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Glob such as '**/*.py' or 'src/**/*.tsx'. '**' spans directories.",
        },
        "max_results": {
            "type": "integer",
            "description": f"Cap on returned paths (default {GLOB_DEFAULT_MAX_RESULTS}).",
            "minimum": 1,
            "maximum": GLOB_HARD_MAX_RESULTS,
        },
    },
    "required": ["pattern"],
    "additionalProperties": False,
}


async def _fs_glob(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    pattern = call.arguments["pattern"] or "**/*"
    cap = max(1, min(GLOB_HARD_MAX_RESULTS, int(call.arguments.get("max_results") or GLOB_DEFAULT_MAX_RESULTS)))

    if ctx.is_local:
        try:
            paths = await _workspace_manager().list_files(_workspace_info(ctx), ".")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(call, f"Error globbing files: {exc}", error="glob_failed")
        matches = sorted(glob_match(paths, pattern))
        truncated = len(matches) > cap
        matches = matches[:cap]
        if not matches:
            return ToolResult.success(
                call,
                f"No files matched pattern: {pattern}\n(total files: {len(paths)})",
                data={"paths": [], "pattern": pattern, "truncated": False},
            )
        body = "\n".join(f"  - {p}" for p in matches)
        footer = f"\n…{cap}+ matches truncated. Refine the pattern.\n" if truncated else ""
        return ToolResult.success(
            call,
            f"Matching: {pattern}\n{body}{footer}",
            data={"paths": matches, "pattern": pattern, "truncated": truncated},
        )

    repo = ctx.require_repo()
    try:
        tree = await _repo_tree(ctx)
    except Exception as exc:  # noqa: BLE001 - matches list_repository_files_glob
        return ToolResult.failure(call, f"Error globbing files: {exc}", error="glob_failed")

    if not tree:
        return ToolResult.success(
            call,
            f"Repository is empty - no files. (Branch: {repo.branch})",
            data={"paths": [], "pattern": pattern, "truncated": False},
        )

    paths = [item["path"] for item in tree]
    matches = glob_match(paths, pattern)
    truncated = len(matches) > cap
    if truncated:
        matches = matches[:cap]

    if not matches:
        return ToolResult.success(
            call,
            f"No files matched pattern: {pattern}\n(Branch: {repo.branch}, total files: {len(paths)})",
            data={"paths": [], "pattern": pattern, "truncated": False},
        )

    header = f"Repository: {repo.full_name} (Branch: {repo.branch})\nMatching: {pattern}\n"
    body = "\n".join(f"  - {p}" for p in sorted(matches))
    footer = f"\n…{cap}+ matches truncated. Refine the pattern.\n" if truncated else ""
    return ToolResult.success(
        call,
        f"{header}{body}{footer}",
        data={"paths": sorted(matches), "pattern": pattern, "truncated": truncated},
    )


# ---------------------------------------------------------------------------
# fs.grep
# ---------------------------------------------------------------------------

GREP_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Regular expression to search for."},
        "path_pattern": {
            "type": "string",
            "description": "Optional glob scoping the search, e.g. '**/*.py'.",
        },
        "case_insensitive": {"type": "boolean"},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
    },
    "required": ["pattern"],
    "additionalProperties": False,
}


async def _fs_grep(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    from ..grep_backend import GREP_DEFAULT_MAX_RESULTS, format_result, grep

    pattern = call.arguments["pattern"]
    if not pattern:
        return ToolResult.failure(call, "Error: empty search pattern", error="empty_pattern")

    path_pattern: Optional[str] = call.arguments.get("path_pattern")
    case_insensitive = bool(call.arguments.get("case_insensitive", False))
    cap = int(call.arguments.get("max_results") or GREP_DEFAULT_MAX_RESULTS)

    if ctx.is_local:
        # ``search_files`` is git grep, which is what the local CrewAI tool
        # uses; its 50-match display cap is part of the output contract.
        try:
            matches = await _workspace_manager().search_files(
                _workspace_info(ctx), pattern, path_pattern or ".",
            )
        except Exception as exc:  # noqa: BLE001 - matches search_in_files
            return ToolResult.failure(call, f"Error searching: {exc}", error="grep_failed")
        if not matches:
            return ToolResult.success(call, "No matches found.", data={"matches": [], "count": 0})
        lines = [f"{m['file']}:{m['line']}: {m['content']}" for m in matches[:50]]
        return ToolResult.success(
            call,
            "\n".join(lines),
            data={"matches": matches[:50], "count": len(matches)},
        )

    from ..github_api import get_file

    repo = ctx.require_repo()
    try:
        tree = await _repo_tree(ctx)
        if not tree:
            return ToolResult.success(
                call,
                f"Repository is empty - no files to search. (Branch: {repo.branch})",
                data={"matches": [], "count": 0},
            )

        paths = [item["path"] for item in tree]
        if path_pattern:
            paths = glob_match(paths, path_pattern)
        if not paths:
            return ToolResult.success(
                call,
                f"No files matched path_pattern: {path_pattern}\n"
                f"(Branch: {repo.branch}, total files: {len(tree)})",
                data={"matches": [], "count": 0},
            )
        paths = paths[:FILE_FETCH_CAP]

        async def _fetch(path: str) -> Tuple[str, Optional[str]]:
            try:
                return path, await get_file(repo.owner, repo.repo, path, token=repo.token, ref=repo.branch)
            except Exception:  # noqa: BLE001 - a missing file is not a search failure
                return path, None

        fetched = await asyncio.gather(*(_fetch(p) for p in paths))
        files = {path: content for path, content in fetched if isinstance(content, str)}
        if not files:
            return ToolResult.success(
                call,
                f"Could not fetch any matching files. (Tried {len(paths)} paths.)",
                data={"matches": [], "count": 0},
            )

        result = grep(
            files,
            pattern,
            case_insensitive=case_insensitive,
            max_results=cap,
            path_filter=glob_to_regex(path_pattern) if path_pattern else None,
        )
    except Exception as exc:  # noqa: BLE001 - matches grep_repository
        return ToolResult.failure(call, f"Error in grep_repository: {exc}", error="grep_failed")

    return ToolResult.success(
        call,
        format_result(result, pattern=pattern),
        data={
            "count": len(result.hits),
            "pattern": pattern,
            "truncated": result.truncated,
            "backend": result.backend,
        },
    )


# ---------------------------------------------------------------------------
# fs.write
# ---------------------------------------------------------------------------

WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Path relative to the repository root."},
        "content": {"type": "string", "description": "The full new file content."},
        "commit_message": {
            "type": "string",
            "description": "Commit summary. Required for GitHub-backed repositories, ignored on a local checkout.",
        },
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}


async def _fs_write(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    path = call.arguments["path"]
    content = call.arguments["content"]

    if ctx.is_local:
        manager = _workspace_manager()
        info = _workspace_info(ctx)
        try:
            try:
                before = await manager.read_file(info, path)
            except Exception:  # noqa: BLE001 - a new file has no "before"
                before = ""
            written = await manager.write_file(info, path, content)
        except Exception as exc:  # noqa: BLE001 - matches write_local_file
            return ToolResult.failure(call, f"Error writing {path}: {exc}", error="write_failed")
        return ToolResult.success(
            call,
            f"Written {written['size']} bytes to {written['path']}",
            data={
                "path": path,
                "bytes": written["size"],
                "paths_written": [path],
                "created": not before,
                "diff": _unified_diff(path, before, content),
            },
        )

    from ..github_api import get_file, put_file

    repo = ctx.require_repo()
    message = call.arguments.get("commit_message") or f"Update {path}"
    try:
        try:
            before = await get_file(repo.owner, repo.repo, path, token=repo.token, ref=repo.branch) or ""
        except Exception:  # noqa: BLE001 - a new file has no "before"
            before = ""
        result = await put_file(
            repo.owner, repo.repo, path, content, message, token=repo.token, branch=repo.branch,
        )
    except Exception as exc:  # noqa: BLE001 - matches agent_tools.write_file
        return ToolResult.failure(call, f"Error writing file {path}: {exc}", error="write_failed")

    sha = result.get("commit_sha", "")
    return ToolResult.success(
        call,
        f"File '{path}' written successfully. Commit: {sha[:8]}",
        data={
            "path": path,
            "commit_sha": sha,
            "paths_written": [path],
            "created": not before,
            "diff": _unified_diff(path, before, content),
        },
    )


# ---------------------------------------------------------------------------
# fs.edit
# ---------------------------------------------------------------------------

EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Path relative to the repository root."},
        "old_string": {
            "type": "string",
            "description": (
                "Exact text to replace, including indentation and enough surrounding "
                "context to be unique in the file."
            ),
        },
        "new_string": {
            "type": "string",
            "description": "Replacement text. Pass an empty string to delete the matched block.",
        },
        "expected_occurrences": {
            "type": "integer",
            "description": (
                "How many times old_string should appear. Default 1; -1 allows any "
                "positive number. A mismatch refuses the edit rather than guessing."
            ),
        },
        "commit_message": {
            "type": "string",
            "description": "Commit summary. Required for GitHub-backed repositories.",
        },
    },
    "required": ["path", "old_string", "new_string"],
    "additionalProperties": False,
}


async def _fs_edit(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    from ..edit_backend import EditError, apply_edit

    path = call.arguments["path"]
    old_string = call.arguments["old_string"]
    new_string = call.arguments["new_string"]
    expected = int(call.arguments.get("expected_occurrences", 1))

    if ctx.is_local:
        manager = _workspace_manager()
        info = _workspace_info(ctx)
        try:
            current = await manager.read_file(info, path)
            new_content, report = apply_edit(
                current or "",
                old_string=old_string,
                new_string=new_string,
                expected_occurrences=expected,
            )
            await manager.write_file(info, path, new_content)
        except EditError as exc:
            # Preserved verbatim: the message tells the model how to widen the
            # match, which is the whole value of the strict-occurrence contract.
            return ToolResult.failure(call, f"Error: {exc}", error="edit_conflict")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.failure(call, f"Error editing file {path}: {exc}", error="edit_failed")

        return ToolResult.success(
            call,
            f"File '{path}' edited "
            f"({report.occurrences_replaced} occurrence(s) replaced, "
            f"{report.bytes_before} → {report.bytes_after} bytes).",
            data={
                "path": path,
                "replacements": report.occurrences_replaced,
                "bytes_before": report.bytes_before,
                "bytes_after": report.bytes_after,
                "paths_written": [path],
                "diff": _unified_diff(path, current or "", new_content),
            },
        )

    from ..github_api import get_file, put_file

    repo = ctx.require_repo()
    message = call.arguments.get("commit_message") or f"Edit {path}"
    try:
        current = await get_file(repo.owner, repo.repo, path, token=repo.token, ref=repo.branch)
        new_content, report = apply_edit(
            current or "",
            old_string=old_string,
            new_string=new_string,
            expected_occurrences=expected,
        )
        result = await put_file(
            repo.owner, repo.repo, path, new_content, message, token=repo.token, branch=repo.branch,
        )
    except EditError as exc:
        return ToolResult.failure(call, f"Error: {exc}", error="edit_conflict")
    except Exception as exc:  # noqa: BLE001 - matches agent_tools.edit_file
        return ToolResult.failure(call, f"Error editing file {path}: {exc}", error="edit_failed")

    sha = result.get("commit_sha", "")
    return ToolResult.success(
        call,
        f"File '{path}' edited "
        f"({report.occurrences_replaced} occurrence(s) replaced, "
        f"{report.bytes_before} → {report.bytes_after} bytes). "
        f"Commit: {sha[:8]}",
        data={
            "path": path,
            "replacements": report.occurrences_replaced,
            "bytes_before": report.bytes_before,
            "bytes_after": report.bytes_after,
            "commit_sha": sha,
            "paths_written": [path],
            "diff": _unified_diff(path, current or "", new_content),
        },
    )


# ---------------------------------------------------------------------------
# fs.delete
# ---------------------------------------------------------------------------

DELETE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Path relative to the repository root."},
        "commit_message": {
            "type": "string",
            "description": "Commit summary. Required for GitHub-backed repositories.",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


async def _fs_delete(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    path = call.arguments["path"]

    if ctx.is_local:
        try:
            deleted = await _workspace_manager().delete_file(_workspace_info(ctx), path)
        except Exception as exc:  # noqa: BLE001 - matches delete_local_file
            return ToolResult.failure(call, f"Error deleting {path}: {exc}", error="delete_failed")
        return ToolResult.success(
            call, f"Deleted: {deleted}", data={"path": path, "deleted": bool(deleted)},
        )

    from ..github_api import delete_file

    repo = ctx.require_repo()
    message = call.arguments.get("commit_message") or f"Delete {path}"
    try:
        result = await delete_file(
            repo.owner, repo.repo, path, message, token=repo.token, branch=repo.branch,
        )
    except Exception as exc:  # noqa: BLE001 - matches delete_repo_file
        return ToolResult.failure(call, f"Error deleting file {path}: {exc}", error="delete_failed")

    sha = result.get("commit_sha", "")
    return ToolResult.success(
        call,
        f"File '{path}' deleted. Commit: {sha[:8]}",
        data={"path": path, "deleted": True, "commit_sha": sha},
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

SPECS: List[Tuple[ToolSpec, Any]] = [
    (
        ToolSpec(
            id="fs.read",
            title="Read file",
            description=(
                "Read a file from the repository. Returns its full text content. "
                "Prefer this over guessing what a file contains."
            ),
            params_schema=READ_SCHEMA,
            risk=Risk.SAFE,
            effects=frozenset({Effect.READS_FS}),
            priority=10,
        ),
        _fs_read,
    ),
    (
        ToolSpec(
            id="fs.list",
            title="List files",
            description="List the files tracked in the repository, optionally under one directory.",
            params_schema=LIST_SCHEMA,
            risk=Risk.SAFE,
            effects=frozenset({Effect.READS_FS}),
            priority=20,
        ),
        _fs_list,
    ),
    (
        ToolSpec(
            id="fs.glob",
            title="Find files by pattern",
            description=(
                "Find files whose path matches a glob such as '**/*.py'. Returns paths "
                "only; read the ones you need afterwards."
            ),
            params_schema=GLOB_SCHEMA,
            risk=Risk.SAFE,
            effects=frozenset({Effect.READS_FS}),
            priority=15,
        ),
        _fs_glob,
    ),
    (
        ToolSpec(
            id="fs.grep",
            title="Search file contents",
            description=(
                "Search file contents for a regular expression. Use this to find a "
                "symbol, import or string that listing paths will not reveal."
            ),
            params_schema=GREP_SCHEMA,
            risk=Risk.SAFE,
            effects=frozenset({Effect.READS_FS}),
            priority=10,
        ),
        _fs_grep,
    ),
    (
        ToolSpec(
            id="fs.write",
            title="Write file",
            description=(
                "Create a file or replace its entire contents. For a small change to an "
                "existing file use fs.edit instead — rewriting a whole file to alter a "
                "few lines corrupts long files on small-context models."
            ),
            params_schema=WRITE_SCHEMA,
            risk=Risk.APPROVAL,
            effects=frozenset({Effect.WRITES_FS}),
            priority=10,
        ),
        _fs_write,
    ),
    (
        ToolSpec(
            id="fs.edit",
            title="Edit file",
            description=(
                "Replace an exact string in a file, leaving the rest untouched. The edit "
                "is refused when old_string does not appear exactly the expected number "
                "of times, so include enough context to be unique."
            ),
            params_schema=EDIT_SCHEMA,
            risk=Risk.APPROVAL,
            effects=frozenset({Effect.WRITES_FS}),
            priority=10,
        ),
        _fs_edit,
    ),
    (
        ToolSpec(
            id="fs.delete",
            title="Delete file",
            description="Delete a file from the repository.",
            params_schema=DELETE_SCHEMA,
            risk=Risk.APPROVAL,
            effects=frozenset({Effect.WRITES_FS}),
            priority=45,
        ),
        _fs_delete,
    ),
]


def register(registry: ToolRegistry) -> None:
    """Add the filesystem tools to *registry*."""
    for spec, handler in SPECS:
        registry.register(spec, handler)
