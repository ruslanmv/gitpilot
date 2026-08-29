"""
Agent Tools for GitPilot Multi-Agent System
Provides CrewAI-compatible tools for agents to explore and analyze repositories.
"""
import asyncio
import threading
from typing import Any, Dict, List, Optional, Tuple

from crewai.tools import tool

from .github_api import get_file, get_repo_tree
from .glob_match import GLOB_DEFAULT_MAX_RESULTS as _GLOB_DEFAULT_MAX_RESULTS
from .glob_match import GLOB_HARD_MAX_RESULTS as _GLOB_HARD_MAX_RESULTS
from .glob_match import glob_match, glob_to_regex


def _sanitize_tool_arg(value: Any, fallback_key: str = "description") -> str:
    """Fix CrewAI tool argument format bug.

    Smaller LLMs (deepseek-r1, qwen, phi) sometimes send tool arguments
    as a dict copying the schema definition instead of the actual value:
        {"description": "README.md", "type": "str"}
    instead of:
        "README.md"

    Worst case: the LLM copies the schema verbatim with a literal
    ``"None"`` value (because the tool exposes ``description: None``):
        {"description": "None", "type": "str"}

    This helper unwraps every variant we have seen in production and
    returns a plain string.  Raises ``ValueError`` only when the value
    cannot be recovered (e.g. the LLM passed a list or an empty dict)
    so the caller can surface a clear error instead of querying
    GitHub with a stringified Python dict.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # 1. Try the most likely human-supplied keys.
        for key in (fallback_key, "description", "value", "default", "title", "path"):
            v = value.get(key)
            if isinstance(v, str) and v and v.lower() != "none":
                return v
        # 2. Any other string field on the dict that isn't the schema
        #    ``type`` marker.
        for key, v in value.items():
            if key in {"type", "anyOf", "format"}:
                continue
            if isinstance(v, str) and v and v.lower() != "none":
                return v
        raise ValueError(
            f"tool argument arrived as a schema-shaped dict with no "
            f"usable value (got keys: {sorted(value.keys())!r}). "
            f"Pass the parameter as a plain string."
        )
    if value is None:
        raise ValueError("tool argument is required but received None")
    if isinstance(value, (list, tuple, set)):
        raise ValueError(
            f"tool argument expected a string, got a {type(value).__name__}; "
            f"pass a single value, not a sequence."
        )
    return str(value)

# Global context for current repository
# Now includes 'token' to ensure tools can authenticate even in threads
# AND includes 'branch' to ensure tools operate on the correct ref (not default HEAD/main)
_current_repo_context: Dict[str, Any] = {}
_context_lock = threading.RLock()


def set_repo_context(
    owner: str,
    repo: str,
    token: Optional[str] = None,
    branch: Optional[str] = None,
):
    """Set the current repository context for tools."""
    global _current_repo_context
    with _context_lock:
        _current_repo_context = {
            "owner": owner,
            "repo": repo,
            "token": token,
            "branch": branch or "HEAD",
        }


def get_repo_context() -> Tuple[str, str, Optional[str], str]:
    """Get the current repository context including token and branch."""
    with _context_lock:
        owner = _current_repo_context.get("owner", "")
        repo = _current_repo_context.get("repo", "")
        token = _current_repo_context.get("token")
        branch = _current_repo_context.get("branch", "HEAD")

    if not owner or not repo:
        raise ValueError("Repository context not set. Call set_repo_context first.")
    return owner, repo, token, branch


async def get_repository_context_summary(
    owner: str,
    repo: str,
    token: Optional[str] = None,
    branch: str = "HEAD",
) -> Dict[str, Any]:
    """Programmatically gather repository context."""
    try:
        # Pass token + ref explicitly
        tree = await get_repo_tree(owner, repo, token=token, ref=branch)

        if not tree:
            return {
                "all_files": [],
                "total_files": 0,
                "extensions": {},
                "directories": set(),
                "key_files": [],
            }

        all_files = [item["path"] for item in tree]
        extensions: Dict[str, int] = {}
        directories: set = set()
        key_files: List[str] = []

        for item in tree:
            path = item["path"]
            if "." in path:
                ext = "." + path.rsplit(".", 1)[1]
                extensions[ext] = extensions.get(ext, 0) + 1
            if "/" in path:
                directories.add(path.split("/")[0])

            path_lower = path.lower()
            if any(
                k in path_lower
                for k in ["readme", "package.json", "requirements.txt", "dockerfile", "makefile"]
            ):
                key_files.append(path)

        return {
            "all_files": all_files,
            "total_files": len(all_files),
            "extensions": extensions,
            "directories": directories,
            "key_files": key_files,
        }

    except Exception as e:
        print(f"[Error] Failed to get repository context: {str(e)}")
        return {"error": str(e), "total_files": 0}


@tool("List all files in repository")
def list_repository_files() -> str:
    """Lists all files in the current repository."""
    try:
        owner, repo, token, branch = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Pass token + ref explicitly
            tree = loop.run_until_complete(get_repo_tree(owner, repo, token=token, ref=branch))
        finally:
            loop.close()

        if not tree:
            return f"Repository is empty - no files found. (Branch: {branch})"

        result = f"Repository: {owner}/{repo} (Branch: {branch})\nFiles:\n"
        for item in sorted(tree, key=lambda x: x["path"]):
            result += f"  - {item['path']}\n"
        return result
    except Exception as e:
        return f"Error listing files: {str(e)}"


@tool("Get directory structure")
def get_directory_structure() -> str:
    """Gets the hierarchical directory structure."""
    try:
        owner, repo, token, branch = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Pass token + ref explicitly
            tree = loop.run_until_complete(get_repo_tree(owner, repo, token=token, ref=branch))
        finally:
            loop.close()

        if not tree:
            return f"No files. (Branch: {branch})"

        # Simple structure generation
        paths = [t["path"] for t in tree]
        return f"Structure for {owner}/{repo} (Branch: {branch}):\n" + "\n".join(sorted(paths))
    except Exception as e:
        return f"Error: {str(e)}"


# ----------------------------------------------------------------------
# Windowed-Read defaults — match Claude Code's contract
# ----------------------------------------------------------------------
READ_DEFAULT_LIMIT = 2000        # default line cap when limit is omitted
READ_MAX_LIMIT = 10_000          # hard ceiling — beyond this the caller
                                 # must paginate via offset
# Glob caps live with the matcher (Batch V4-A3); re-exported for callers here.
GLOB_DEFAULT_MAX_RESULTS = _GLOB_DEFAULT_MAX_RESULTS
GLOB_HARD_MAX_RESULTS = _GLOB_HARD_MAX_RESULTS


def _coerce_int(value: Any, default: int) -> int:
    """CrewAI sometimes passes ints as strings or dicts.  Coerce
    safely; anything we can't parse falls back to the default.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except (TypeError, ValueError):
            return default
    if isinstance(value, dict):
        # Common CrewAI schema-leak: {"description": "...", "type": "int"}
        return default
    return default


@tool("Find files matching a pattern")
def list_repository_files_glob(
    pattern: Any,
    max_results: Any = GLOB_DEFAULT_MAX_RESULTS,
) -> str:
    """Search the repository for files whose path matches a glob.

    pattern: a pathlib-style glob.  Examples:
        "**/*.py"            all Python files
        "src/**/*.tsx"       every .tsx under src
        "**/test_*.py"       all pytest files
        "README*"            top-level README files
    max_results: hard cap on the number of paths returned (default 200,
        max 1000).  When the cap is hit the result is annotated so the
        caller can refine.

    Output: one path per line.  Path-only — no contents.  Use
    "Read file content" afterwards if you need bytes.
    """
    pattern = _sanitize_tool_arg(pattern, fallback_key="pattern") or "**/*"
    cap = max(1, min(GLOB_HARD_MAX_RESULTS, _coerce_int(max_results, GLOB_DEFAULT_MAX_RESULTS)))
    try:
        owner, repo, token, branch = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            tree = loop.run_until_complete(get_repo_tree(owner, repo, token=token, ref=branch))
        finally:
            loop.close()

        if not tree:
            return f"Repository is empty - no files. (Branch: {branch})"

        # ``fnmatch`` understands `*`/`?`/`[…]` but treats `**` as a
        # plain star.  Translate `**` → match-any-segments by walking
        # the pattern manually for a tighter match on the common case.
        paths = [item["path"] for item in tree]
        matches = _glob_match(paths, pattern)
        truncated = False
        if len(matches) > cap:
            matches = matches[:cap]
            truncated = True

        if not matches:
            return f"No files matched pattern: {pattern}\n(Branch: {branch}, total files: {len(paths)})"

        header = f"Repository: {owner}/{repo} (Branch: {branch})\nMatching: {pattern}\n"
        body = "\n".join(f"  - {p}" for p in sorted(matches))
        footer = f"\n…{cap}+ matches truncated. Refine the pattern.\n" if truncated else ""
        return f"{header}{body}{footer}"
    except Exception as e:
        return f"Error globbing files: {str(e)}"


# Globbing lives in :mod:`gitpilot.glob_match` (Batch V4-A3) so the canonical
# ``fs.glob``/``fs.grep`` handlers can share it without importing CrewAI through
# this module.  Re-exported under the original private names because callers in
# this file and its tests use them.
_glob_to_regex = glob_to_regex
_glob_match = glob_match


def _fetch_file_content(file_path: str) -> str | None:
    """Fetch a file from the active repository using the current context."""
    owner, repo, token, branch = get_repo_context()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            get_file(owner, repo, file_path, token=token, ref=branch)
        )
    finally:
        loop.close()


@tool("Read file content")
def read_file(file_path: Any) -> str:
    """Read the content of a file from the active repository.

    file_path: the file's path relative to the repository root, e.g.
    "README.md" or "src/main.py". Pass a plain string — do **not** pass
    a dict like {"description": "...", "type": "str"}.
    """
    file_path = _sanitize_tool_arg(file_path)
    try:
        content = _fetch_file_content(file_path)
        return f"Content of {file_path}:\n---\n{content}\n---"
    except Exception as e:
        return f"Error reading file {file_path}: {str(e)}"


@tool("Read file content window")
def read_file_window(
    file_path: Any,
    offset: Any = 0,
    limit: Any = READ_DEFAULT_LIMIT,
) -> str:
    """Read a line window from a file in the active repository.

    This advanced pagination tool is intentionally not included in the
    default repository tool list. Keep the primary "Read file content"
    tool's schema simple for smaller ReAct models.

    file_path: the file's path relative to the repository root.
    offset: 0-indexed line number to start reading from.
    limit: maximum number of lines to return (default 2000, max 10000).
    """
    file_path = _sanitize_tool_arg(file_path)
    start = max(0, _coerce_int(offset, 0))
    span = max(1, min(READ_MAX_LIMIT, _coerce_int(limit, READ_DEFAULT_LIMIT)))
    try:
        content = _fetch_file_content(file_path)
        if content is None:
            return f"Error reading file {file_path}: empty response"

        lines = content.splitlines()
        total = len(lines)
        if total == 0:
            return f"Content of {file_path}:\n---\n(empty file)\n---"

        end = min(total, start + span)
        slice_text = "\n".join(lines[start:end])

        header = f"Content of {file_path}"
        if start > 0 or end < total:
            header += f" (lines {start + 1}-{end} of {total})"

        footer = ""
        if end < total:
            remaining = total - end
            footer = (
                f"\n…{remaining} more lines. Continue with offset={end} "
                f"to read further."
            )
        return f"{header}:\n---\n{slice_text}\n---{footer}"
    except Exception as e:
        return f"Error reading file {file_path}: {str(e)}"


@tool("Get repository summary")
def get_repository_summary() -> str:
    """Provides a comprehensive summary of the repository."""
    try:
        owner, repo, token, branch = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Pass token + ref explicitly
            tree = loop.run_until_complete(get_repo_tree(owner, repo, token=token, ref=branch))
        finally:
            loop.close()

        return f"Summary for {owner}/{repo} (Branch: {branch}): {len(tree)} files found."
    except Exception as e:
        return f"Error: {str(e)}"


# ---------------------------------------------------------------------------
# Write tools — allow agents to create, update, and delete files via GitHub API
# ---------------------------------------------------------------------------

@tool("Edit a section of a file (exact string replacement)")
def edit_file(
    file_path: Any,
    old_string: Any,
    new_string: Any,
    commit_message: Any,
    expected_occurrences: Any = 1,
) -> str:
    """Surgical edit — replace a small section of a file without
    re-emitting the rest.  Use this whenever you want to fix a bug,
    rename a symbol, or insert a few lines into a file that already
    exists.  Never use ``Write or update a file`` to apply a small
    change — that requires re-emitting the whole file and corrupts
    long files on small-context models.

    file_path: path relative to the repo root.  Plain string.
    old_string: the exact text to find — including surrounding
        indentation and (where needed) preceding/trailing context
        so the match is unique.  Plain string.
    new_string: the replacement text.  Plain string.  Pass an empty
        string to delete the matched block.
    commit_message: short imperative commit summary.
    expected_occurrences: how many times old_string is expected to
        appear in the file.  Default 1.  Pass a higher number to
        rename an identifier that appears N times; pass -1 to allow
        any positive number.  When the actual count differs, the
        edit is refused — widen old_string to disambiguate.

    On success returns "File '<path>' edited (N occurrence(s) replaced).
    Commit: <sha>".  On failure returns an actionable error message
    starting with "Error:".
    """
    from .edit_backend import EditError, apply_edit
    from .github_api import get_file, put_file

    file_path = _sanitize_tool_arg(file_path)
    old_string_s = old_string if isinstance(old_string, str) else _sanitize_tool_arg(old_string, fallback_key="value")
    new_string_s = new_string if isinstance(new_string, str) else _sanitize_tool_arg(new_string, fallback_key="value")
    commit_message_s = _sanitize_tool_arg(commit_message, fallback_key="value") or f"Edit {file_path}"
    expected = _coerce_int(expected_occurrences, 1)

    try:
        owner, repo, token, branch = get_repo_context()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            current = loop.run_until_complete(
                get_file(owner, repo, file_path, token=token, ref=branch)
            )
            new_content, report = apply_edit(
                current or "",
                old_string=old_string_s,
                new_string=new_string_s,
                expected_occurrences=expected,
            )
            result = loop.run_until_complete(
                put_file(owner, repo, file_path, new_content, commit_message_s, token=token, branch=branch)
            )
        finally:
            loop.close()

        sha = result.get("commit_sha", "")
        return (
            f"File '{file_path}' edited "
            f"({report.occurrences_replaced} occurrence(s) replaced, "
            f"{report.bytes_before} → {report.bytes_after} bytes). "
            f"Commit: {sha[:8]}"
        )
    except EditError as e:
        # User-facing — keep the original message so the agent can
        # widen the context and retry.
        return f"Error: {e}"
    except Exception as e:
        return f"Error editing file {file_path}: {e}"


@tool("Apply a unified diff to a file")
def apply_patch_to_file(
    file_path: Any,
    diff: Any,
    commit_message: Any,
) -> str:
    """Apply a unified-diff patch to a single file.  Use this when the
    change involves several non-contiguous edits inside one file and
    a single ``Edit a section of a file`` call wouldn't capture all
    of them cleanly.

    file_path: path relative to the repo root.
    diff: a single-file unified diff with one or more @@-hunks.  The
        helper matches each hunk by *context lines* (the leading-space
        lines around the change), so line numbers can be stale.
        Multi-file diffs are not accepted — split them first.
    commit_message: short imperative commit summary.

    Returns the same shape as ``Edit a section of a file``.
    """
    from .edit_backend import EditError, apply_unified_diff
    from .github_api import get_file, put_file

    file_path = _sanitize_tool_arg(file_path)
    diff_s = diff if isinstance(diff, str) else _sanitize_tool_arg(diff, fallback_key="value")
    commit_message_s = _sanitize_tool_arg(commit_message, fallback_key="value") or f"Patch {file_path}"

    try:
        owner, repo, token, branch = get_repo_context()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            current = loop.run_until_complete(
                get_file(owner, repo, file_path, token=token, ref=branch)
            )
            new_content, report = apply_unified_diff(current or "", diff_s)
            result = loop.run_until_complete(
                put_file(owner, repo, file_path, new_content, commit_message_s, token=token, branch=branch)
            )
        finally:
            loop.close()

        sha = result.get("commit_sha", "")
        return (
            f"File '{file_path}' patched "
            f"({report.occurrences_replaced} hunk(s) applied, "
            f"{report.bytes_before} → {report.bytes_after} bytes). "
            f"Commit: {sha[:8]}"
        )
    except EditError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error patching file {file_path}: {e}"


@tool("Write or update a file in the repository")
def write_file(file_path: Any, content: Any, commit_message: Any) -> str:
    """Create or update a file in the repository.

    file_path: path relative to the repo root (plain string, e.g.
    ``"src/main.py"``).  content: the full new file content (plain
    string).  commit_message: a short imperative commit summary.  Do
    **not** wrap any of these in a ``{description, type}`` schema dict.
    """
    file_path = _sanitize_tool_arg(file_path)
    content = _sanitize_tool_arg(content, fallback_key="value")
    commit_message = _sanitize_tool_arg(commit_message, fallback_key="value")
    try:
        owner, repo, token, branch = get_repo_context()
        from .github_api import put_file

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                put_file(owner, repo, file_path, content, commit_message, token=token, branch=branch)
            )
        finally:
            loop.close()

        sha = result.get("commit_sha", "")
        return f"File '{file_path}' written successfully. Commit: {sha[:8]}"
    except Exception as e:
        return f"Error writing file {file_path}: {str(e)}"


@tool("Delete a file from the repository")
def delete_repo_file(file_path: Any, commit_message: Any) -> str:
    """Delete a file from the repository.

    file_path: the path relative to the repo root (plain string, e.g.
    ``"docs/old.md"``).  commit_message: a short imperative commit
    summary.  Both are plain strings — never wrap them in a schema dict.
    """
    file_path = _sanitize_tool_arg(file_path)
    commit_message = _sanitize_tool_arg(commit_message, fallback_key="value")
    try:
        owner, repo, token, branch = get_repo_context()
        from .github_api import delete_file

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                delete_file(owner, repo, file_path, commit_message, token=token, branch=branch)
            )
        finally:
            loop.close()

        sha = result.get("commit_sha", "")
        return f"File '{file_path}' deleted. Commit: {sha[:8]}"
    except Exception as e:
        return f"Error deleting file {file_path}: {str(e)}"


@tool("Create a new branch in the repository")
def create_repo_branch(branch_name: str) -> str:
    """Creates a new branch from the current HEAD."""
    branch_name = _sanitize_tool_arg(branch_name)
    try:
        owner, repo, token, _branch = get_repo_context()
        from .github_api import create_branch

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                create_branch(owner, repo, branch_name, from_ref="HEAD", token=token)
            )
        finally:
            loop.close()

        return f"Branch '{branch_name}' created successfully."
    except Exception as e:
        if "already exists" in str(e).lower() or "422" in str(e):
            return f"Branch '{branch_name}' already exists (OK to use)."
        return f"Error creating branch: {str(e)}"


# Export tools
@tool("Search file contents")
def grep_repository(
    pattern: Any,
    path_pattern: Any = None,
    case_insensitive: Any = False,
    max_results: Any = 100,
) -> str:
    """Search the repository for a regex pattern across file contents.

    pattern: a Python-style regular expression.  Use this when you need
        to find a symbol, string, import, or any other content that
        listing/globbing won't reveal.
    path_pattern: optional glob to scope the search (e.g. "**/*.py",
        "src/**/*.ts").  Same `/`-aware semantics as
        "Find files matching a pattern".
    case_insensitive: pass true to match regardless of case.
    max_results: hard cap (default 100, max 500).  Beyond the cap the
        result is annotated so you can narrow the search.

    Output: one match per line, formatted ``path:line: matched_text``.
    """
    from .grep_backend import (
        GREP_DEFAULT_MAX_RESULTS,
        format_result,
        grep,
    )

    pattern_str = _sanitize_tool_arg(pattern, fallback_key="pattern") or ""
    if not pattern_str:
        return "Error: empty search pattern"
    path_filter_str = path_pattern if isinstance(path_pattern, str) else None
    ci_flag = bool(case_insensitive) if not isinstance(case_insensitive, dict) else False
    cap = _coerce_int(max_results, GREP_DEFAULT_MAX_RESULTS)

    try:
        owner, repo, token, branch = get_repo_context()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            tree = loop.run_until_complete(get_repo_tree(owner, repo, token=token, ref=branch))
        finally:
            loop.close()

        if not tree:
            return f"Repository is empty - no files to search. (Branch: {branch})"

        # Pre-filter file list by path glob BEFORE fetching contents —
        # this is the single biggest cost saving on GitHub-backed repos.
        paths = [item["path"] for item in tree]
        if path_filter_str:
            paths = _glob_match(paths, path_filter_str)
        if not paths:
            return (
                f"No files matched path_pattern: {path_filter_str}\n"
                f"(Branch: {branch}, total files: {len(tree)})"
            )

        # Cap the number of files we fetch — at 200 paths × ~50 KB each
        # that's already 10 MB.  Anything beyond is the caller's job
        # to narrow with a tighter path_pattern.
        FILE_FETCH_CAP = 200
        paths = paths[:FILE_FETCH_CAP]

        # Fetch contents concurrently.  ``get_file`` is async so we batch.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _gather():
                import asyncio as _aio
                async def _fetch(p):
                    try:
                        return p, await get_file(owner, repo, p, token=token, ref=branch)
                    except Exception:
                        return p, None
                return await _aio.gather(*(_fetch(p) for p in paths))
            results = loop.run_until_complete(_gather())
        finally:
            loop.close()

        files = {p: c for p, c in results if isinstance(c, str)}
        if not files:
            return f"Could not fetch any matching files. (Tried {len(paths)} paths.)"

        rx_path_filter = _glob_to_regex(path_filter_str) if path_filter_str else None
        result = grep(
            files,
            pattern_str,
            case_insensitive=ci_flag,
            max_results=cap,
            path_filter=rx_path_filter,
        )
        return format_result(result, pattern=pattern_str)
    except Exception as e:
        return f"Error in grep_repository: {str(e)}"


@tool("Find code by semantic search")
def semantic_search(query: Any, k: Any = 8) -> str:
    """Find the most semantically-similar code chunks for a natural-
    language query.  Powered by a local on-prem RAG index (ChromaDB
    + MiniLM-L6-v2 by default; pure-Python hashing fallback when the
    model isn't available).

    query: what you want to find, in natural language.  Example
        queries: "authentication middleware", "where do we parse the
        plan response", "the function that talks to OpenAI".
    k: how many results to return (default 8, max 20).

    Output: one chunk per result, formatted as ``path:start-end``
    plus a short excerpt.  Returns "No matches" silently when the
    index hasn't been built yet — fall back to grep / glob in that
    case.

    Gated behind the ``rag_retrieval`` flag — when off this tool
    isn't registered with the agent at all.
    """
    from . import flags
    from .rag import FLAG_RAG_RETRIEVAL, retrieve_top_k

    if not flags.is_on(FLAG_RAG_RETRIEVAL, default=False):
        return "Semantic search is disabled. Enable the rag_retrieval flag and build the index first."

    q = _sanitize_tool_arg(query, fallback_key="query") or ""
    if not q:
        return "Error: empty search query"
    kk = max(1, min(20, _coerce_int(k, 8)))
    try:
        owner, repo, token, branch = get_repo_context()
        hits = retrieve_top_k(q, owner=owner, repo=repo, branch=branch or "HEAD", k=kk)
        if not hits:
            return (
                f"No semantic matches for: {q}\n"
                "Either the index hasn't been built yet, or no chunks "
                "matched.  Try the 'Search file contents' tool instead."
            )
        lines = [f"Top {len(hits)} semantic match(es) for: {q}"]
        for h in hits:
            excerpt = h.text.replace("\n", " ").strip()[:200]
            lines.append(f"  {h.path}:{h.start_line}-{h.end_line}  (score={h.score:.2f})")
            lines.append(f"    {excerpt}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error in semantic_search: {str(e)}"


# ---------------------------------------------------------------------------
# Plan-vocabulary aliases
#
# The planner's JSON schema uses an action vocabulary — CREATE, MODIFY, DELETE,
# READ, INDEX, EXECUTE — and those words collide with the ReAct loop's own
# `Action:` keyword. A small model, mid-plan, reliably emits
#
#     Action: READ
#     Action Input: {"file_path": "new_file.py"}
#
# which CrewAI rejects with "Action 'READ' don't exist". The arguments are
# correct; only the name is wrong, and the model then burns two or three turns
# rediscovering the canonical name — sometimes giving up and returning a plan
# whose only step is the read it never managed to do.
#
# Rejecting a semantically correct call over a naming detail is the defect, not
# the model's mistake. These aliases make the collision resolve: the same
# function, reachable by the word the planner was taught to write. Descriptions
# are one terse line each so the prompt cost stays near zero, and the canonical
# tools remain the ones the instructions point at.
#
# (The same class of failure was already worked around for EXECUTE with a
# deterministic short-circuit — see the EXECUTE notes in agentic.py.)


@tool("READ")
def read_file_alias(file_path: Any) -> str:
    """Alias for "Read file content". file_path: path relative to the repo root."""
    return read_file.run(file_path=file_path)


@tool("LIST")
def list_files_alias() -> str:
    """Alias for "List all files in repository"."""
    return list_repository_files.run()


REPOSITORY_TOOLS = [
    list_repository_files,
    get_directory_structure,
    read_file,
    get_repository_summary,
]

#: The planner's tools: the same read-only surface, plus the aliases.
#:
#: Scoped to the planner on purpose. It is the only agent taught the JSON action
#: vocabulary, so it is the only one that confuses those words for tool names —
#: and every other agent keeps the deliberately small explorer surface. Aliases
#: last: the canonical names are what the instructions point at, and what a
#: capable model picks; these only catch the collision.
PLANNER_TOOLS = [
    *REPOSITORY_TOOLS,
    read_file_alias,
    list_files_alias,
]
WRITE_TOOLS = [
    edit_file,              # B8: surgical exact-string replacement
    apply_patch_to_file,    # B8: unified-diff patch
    write_file,
    delete_repo_file,
    create_repo_branch,
]
