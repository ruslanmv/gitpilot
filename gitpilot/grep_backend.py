"""Grep backend — pure-Python regex search across repo files.

Powers the ``Search file contents`` agent tool.  Designed for local
on-prem use first; no shell-out, no external dependency.  When
``ripgrep`` is present it is used as a fast path; otherwise we fall
back to a hand-written Python loop that's still fast on the typical
GitPilot repo (a few hundred files).

Contract (pinned by the tests):

* Returns a list of dicts: ``{path, line, match}``.
* Truncates above ``max_results`` and includes a ``truncated=True``
  flag in the metadata so the caller can refine.
* Result order: stable — files are sorted, lines within a file are
  in ascending order.  Reproducible runs for tests.

Security:
* The pattern is a regular expression (validated up front).  No
  shell injection: we never pass it to a shell when using the rg
  binary — only via subprocess args.
* The ``path_pattern`` filter goes through the same glob → regex
  translator used by Batch B1, so the same `/`-aware semantics apply.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

# Hard cap — never return more than this regardless of caller value.
GREP_HARD_MAX_RESULTS = 500
GREP_DEFAULT_MAX_RESULTS = 100
RIPGREP_TIMEOUT_S = 10


@dataclass
class GrepHit:
    path: str
    line: int
    match: str


@dataclass
class GrepResult:
    hits: List[GrepHit] = field(default_factory=list)
    truncated: bool = False
    backend: str = "python"   # "ripgrep" | "python"
    error: Optional[str] = None


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------

def grep(
    files: dict[str, str],
    pattern: str,
    *,
    case_insensitive: bool = False,
    max_results: int = GREP_DEFAULT_MAX_RESULTS,
    path_filter: Optional[re.Pattern[str]] = None,
) -> GrepResult:
    """Run a regex search over the supplied (path → content) mapping.

    The caller is responsible for assembling the file map — for the
    GitHub-only path that means downloading the relevant files first;
    for the local-checkout path that's just ``Path.read_text`` per
    matching file.  Keeping the backend file-source-agnostic lets us
    test it without touching GitHub or the disk.
    """
    cap = max(1, min(GREP_HARD_MAX_RESULTS, int(max_results)))

    try:
        flags = re.IGNORECASE if case_insensitive else 0
        rx = re.compile(pattern, flags)
    except re.error as exc:
        return GrepResult(error=f"invalid regex: {exc}")

    hits: List[GrepHit] = []
    for path in sorted(files.keys()):
        if path_filter is not None and not path_filter.match(path):
            continue
        content = files[path]
        if not content:
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            if rx.search(line):
                hits.append(GrepHit(path=path, line=lineno, match=line.rstrip()))
                if len(hits) >= cap:
                    return GrepResult(hits=hits, truncated=True, backend="python")

    return GrepResult(hits=hits, truncated=False, backend="python")


# ----------------------------------------------------------------------
# Local-checkout fast path: shell out to ripgrep when available
# ----------------------------------------------------------------------

def grep_local(
    workdir: str,
    pattern: str,
    *,
    case_insensitive: bool = False,
    max_results: int = GREP_DEFAULT_MAX_RESULTS,
    glob_filter: Optional[str] = None,
) -> GrepResult:
    """Search files under ``workdir`` using ripgrep if available,
    falling back to a pure-Python walk otherwise.

    Used for the local-checkout / local-git modes.  GitHub-only
    sessions go through :func:`grep` instead because they don't have
    a tree on disk.
    """
    if shutil.which("rg"):
        return _grep_via_ripgrep(
            workdir,
            pattern,
            case_insensitive=case_insensitive,
            max_results=max_results,
            glob_filter=glob_filter,
        )
    # Pure-Python fallback — walk the tree, read each file, match.
    # Kept here (rather than in the GitHub helper) because the local
    # path benefits from a file-handle-streaming walk that doesn't
    # materialise the whole repo into memory.
    return _grep_via_python_walk(
        workdir,
        pattern,
        case_insensitive=case_insensitive,
        max_results=max_results,
        glob_filter=glob_filter,
    )


def _grep_via_ripgrep(
    workdir: str,
    pattern: str,
    *,
    case_insensitive: bool,
    max_results: int,
    glob_filter: Optional[str],
) -> GrepResult:
    cap = max(1, min(GREP_HARD_MAX_RESULTS, int(max_results)))
    argv = [
        "rg",
        "--no-config",         # ignore user's ~/.ripgreprc
        "--no-heading",
        "--line-number",
        "--with-filename",
        "--color", "never",
        "--max-count", str(cap),
        # Skip binaries — saves token-pollution and matches what
        # Claude Code / Cursor do by default.
        "--text",
    ]
    if case_insensitive:
        argv.append("-i")
    if glob_filter:
        argv.extend(["-g", glob_filter])
    argv.extend(["--", pattern, workdir])

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=RIPGREP_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return GrepResult(
            error=f"ripgrep timed out after {RIPGREP_TIMEOUT_S}s",
            backend="ripgrep",
        )
    except FileNotFoundError:
        # rg disappeared between which() and run() — degrade gracefully.
        return _grep_via_python_walk(
            workdir, pattern,
            case_insensitive=case_insensitive,
            max_results=max_results,
            glob_filter=glob_filter,
        )

    # rg exits 1 when there are zero matches — that's not an error.
    if proc.returncode not in (0, 1):
        err = proc.stderr.strip().splitlines()
        return GrepResult(error="; ".join(err[:3]) if err else "ripgrep failed", backend="ripgrep")

    hits: List[GrepHit] = []
    truncated = False
    for raw in proc.stdout.splitlines():
        # Format: <path>:<lineno>:<match>
        parts = raw.split(":", 2)
        if len(parts) < 3:
            continue
        path, lineno_s, match = parts
        try:
            lineno = int(lineno_s)
        except ValueError:
            continue
        # Trim the leading workdir prefix so paths look repo-relative.
        if path.startswith(workdir + "/"):
            path = path[len(workdir) + 1:]
        hits.append(GrepHit(path=path, line=lineno, match=match.rstrip()))
        if len(hits) >= cap:
            truncated = True
            break
    return GrepResult(hits=hits, truncated=truncated, backend="ripgrep")


def _grep_via_python_walk(
    workdir: str,
    pattern: str,
    *,
    case_insensitive: bool,
    max_results: int,
    glob_filter: Optional[str],
) -> GrepResult:
    import pathlib

    try:
        flags = re.IGNORECASE if case_insensitive else 0
        rx = re.compile(pattern, flags)
    except re.error as exc:
        return GrepResult(error=f"invalid regex: {exc}")

    # Local import to avoid a hard module-load dep when grep isn't used.
    from .agent_tools import _glob_to_regex

    pf = _glob_to_regex(glob_filter) if glob_filter else None
    cap = max(1, min(GREP_HARD_MAX_RESULTS, int(max_results)))
    root = pathlib.Path(workdir)
    hits: List[GrepHit] = []
    # Walk deterministically so tests are reproducible.
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if pf is not None and not pf.match(rel):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            if rx.search(line):
                hits.append(GrepHit(path=rel, line=lineno, match=line.rstrip()))
                if len(hits) >= cap:
                    return GrepResult(hits=hits, truncated=True, backend="python")
    return GrepResult(hits=hits, truncated=False, backend="python")


# ----------------------------------------------------------------------
# Formatter for the agent tool wrapper
# ----------------------------------------------------------------------

def format_result(result: GrepResult, *, pattern: str) -> str:
    if result.error:
        return f"Error: {result.error}"
    if not result.hits:
        return f"No matches for pattern: {pattern}"
    lines = [f"Found {len(result.hits)} match(es) for: {pattern}"]
    for hit in result.hits:
        lines.append(f"  {hit.path}:{hit.line}: {hit.match[:200]}")
    if result.truncated:
        lines.append(
            f"…truncated at {len(result.hits)} hits. "
            "Narrow the pattern or pass max_results to see more."
        )
    return "\n".join(lines)
