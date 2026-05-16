"""Explorer-report compression (Batch B5).

The Repository Explorer agent produces a free-form "REPOSITORY
EXPLORATION REPORT" that grows linearly with the repo size.  On a
200-file repo the file-listing section alone can run 4–6 KB — enough
to crowd the planner's prompt on an 8 k-context model like
llama3:8b.

This module compresses that report into a fixed-budget summary the
planner sees instead of the raw transcript.  Strict properties:

* **Deterministic.**  No LLM call needed; the compression is pure
  string manipulation.  Easy to test, reproducible across runs.
* **Lossless for facts.**  Every concrete file path the planner needs
  to validate is preserved (file lists, key files, directory tree).
  Only the prose padding and redundant repetition is trimmed.
* **Hard-capped.**  Default 800 tokens, configurable.  When the raw
  report is already under cap we emit it unchanged (no churn).
* **Format-stable.**  Output is the same "REPOSITORY EXPLORATION
  REPORT" header the planner already knows how to read — no prompt
  template change in agentic.py beyond passing the compressed string.

Wired in at the boundary between explorer.kickoff() and the planner
task description, so neither agent changes shape.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

from . import flags
from .context_budget import estimate_tokens

logger = logging.getLogger(__name__)

FLAG_SUBAGENT_EXPLORER = "subagent_explorer"

# Tunable budgets.  Centralised so a future per-provider override can
# tighten them on small-context models without touching call sites.
DEFAULT_TOKEN_BUDGET = 800        # planner-injection cap
MAX_FILES_LISTED = 60             # absolute hard cap on enumerated paths
MAX_KEY_FILES = 8
MAX_DIRECTORY_LINES = 25
PROSE_PARAGRAPH_CAP = 280         # chars per free-text paragraph


@dataclass
class CompressionReport:
    """Returned alongside the compressed string so the caller can land
    a Task row showing concrete before/after numbers."""
    original_tokens: int = 0
    compressed_tokens: int = 0
    files_in_original: int = 0
    files_kept: int = 0
    truncated: bool = False
    reason: Optional[str] = None


@dataclass
class _ParsedReport:
    """Best-effort split of the explorer's free-form report into the
    sections we actually use.  Missing sections default to empty
    strings; we never crash on a malformed report."""
    files_found: List[str] = field(default_factory=list)
    key_files: List[str] = field(default_factory=list)
    directory_structure: str = ""
    file_types: str = ""
    other_prose: List[str] = field(default_factory=list)


# Regexes for the section headers the explorer's prompt template
# instructs it to emit.  Case-insensitive on purpose — small models
# sometimes wobble the capitalisation.
_SECTION_RE = re.compile(
    r"(?im)^\s*(?P<name>files\s+found|key\s+files|directory\s+structure|file\s+types|repository\s+exploration\s+report)\s*:?\s*$"
)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+\.)\s+(?P<rest>.+?)\s*$")
_PATH_RE = re.compile(r"[\w./\-]+\.(?:md|py|ts|tsx|js|jsx|json|yml|yaml|toml|cfg|ini|txt|rst|sh|bash|go|rs|rb|java|c|h|cpp|hpp|html|css|scss)")


def _split_into_sections(report: str) -> _ParsedReport:
    """Walk the report top-to-bottom, routing lines into buckets based
    on the most-recent section header we've seen.  Tolerant: unknown
    sections fall through to ``other_prose``."""
    parsed = _ParsedReport()
    current = "other"
    for raw_line in report.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        m = _SECTION_RE.match(line)
        if m:
            name = m.group("name").lower().replace(" ", "_")
            if "files_found" in name:
                current = "files_found"
            elif "key_files" in name:
                current = "key_files"
            elif "directory" in name:
                current = "directory"
            elif "file_types" in name:
                current = "file_types"
            else:
                current = "other"
            continue

        if current == "files_found":
            # Pull path-like tokens out of the line (handles "- file.py"
            # and "1. file.py" and bare "file.py").
            for path in _PATH_RE.findall(line):
                if path not in parsed.files_found:
                    parsed.files_found.append(path)
            # Also catch lines that look like bullets but don't have a
            # standard extension — we don't want to drop a "Dockerfile".
            bm = _BULLET_RE.match(line)
            if bm:
                rest = bm.group("rest").strip().strip("`'\"")
                if rest and "/" in rest or _looks_like_filename(rest):
                    if rest not in parsed.files_found:
                        parsed.files_found.append(rest)
        elif current == "key_files":
            bm = _BULLET_RE.match(line)
            if bm:
                rest = bm.group("rest").strip().strip("`'\"")
                if rest and rest not in parsed.key_files:
                    parsed.key_files.append(rest)
            else:
                # Sometimes the explorer just lists key files inline.
                for path in _PATH_RE.findall(line):
                    if path not in parsed.key_files:
                        parsed.key_files.append(path)
        elif current == "directory":
            parsed.directory_structure += line + "\n"
        elif current == "file_types":
            parsed.file_types += line + "\n"
        else:
            parsed.other_prose.append(line)
    return parsed


def _looks_like_filename(s: str) -> bool:
    """Heuristic for tokens like ``Dockerfile``, ``Makefile``, ``LICENSE``
    that don't have an extension but are obviously file names."""
    s = s.strip()
    if not s or "\n" in s or " " in s:
        return False
    if s.startswith(".") and len(s) > 1:
        return True   # .gitignore, .env
    bare = {"Dockerfile", "Makefile", "LICENSE", "CHANGELOG", "NOTICE", "AUTHORS"}
    return s in bare


def _truncate_directory(structure: str) -> str:
    """Keep only the first ``MAX_DIRECTORY_LINES`` lines of the
    directory tree.  Append a marker when trimmed."""
    lines = [ln for ln in structure.splitlines() if ln.strip()]
    if len(lines) <= MAX_DIRECTORY_LINES:
        return "\n".join(lines)
    return "\n".join(lines[:MAX_DIRECTORY_LINES]) + f"\n  …{len(lines) - MAX_DIRECTORY_LINES} more entries"


def _file_extension_histogram(files: List[str]) -> str:
    """When the explorer hasn't produced a 'File Types' section, derive
    one from the file list ourselves.  Cheap and always-on."""
    counter: Counter[str] = Counter()
    for f in files:
        if "." in f.split("/")[-1]:
            ext = f.rsplit(".", 1)[-1].lower()
            counter[ext] += 1
        else:
            counter["(no-ext)"] += 1
    if not counter:
        return ""
    return ", ".join(f"{ext}={n}" for ext, n in counter.most_common(8))


def compress_exploration_report(
    report: str,
    *,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> tuple[str, CompressionReport]:
    """Return a fixed-budget compressed form of the explorer's report.

    Always returns a string the planner can read using its existing
    template; never raises on malformed input.

    Falls back to the raw report (no compression) when:
    * the feature flag is off, OR
    * the raw report already fits under the budget.
    """
    metrics = CompressionReport(
        original_tokens=estimate_tokens(report or ""),
    )
    if not flags.is_on(FLAG_SUBAGENT_EXPLORER, default=True):
        metrics.compressed_tokens = metrics.original_tokens
        metrics.reason = "flag off"
        return report, metrics

    if not report or not report.strip():
        metrics.reason = "empty report"
        return report, metrics

    if metrics.original_tokens <= token_budget:
        metrics.compressed_tokens = metrics.original_tokens
        metrics.reason = "under budget"
        return report, metrics

    parsed = _split_into_sections(report)
    metrics.files_in_original = len(parsed.files_found)

    # File list — cap and annotate if we trimmed.
    files = parsed.files_found[:MAX_FILES_LISTED]
    truncated_files = len(parsed.files_found) > MAX_FILES_LISTED
    metrics.files_kept = len(files)
    metrics.truncated = truncated_files

    # Key files — preserve order, cap.
    key = parsed.key_files[:MAX_KEY_FILES]

    # Directory structure — cap to first N lines.
    directory = _truncate_directory(parsed.directory_structure)

    # File-type histogram — prefer explorer's own; else compute one.
    file_types = parsed.file_types.strip() or _file_extension_histogram(parsed.files_found)

    # Assemble the compressed report using the same header the planner
    # already expects.
    lines: list[str] = ["REPOSITORY EXPLORATION REPORT", "============================="]
    lines.append("")
    lines.append("Files Found:")
    for path in files:
        lines.append(f"  - {path}")
    if truncated_files:
        lines.append(
            f"  …{len(parsed.files_found) - MAX_FILES_LISTED} more files. "
            "Use 'Find files matching a pattern' or 'Search file contents' "
            "to drill down."
        )
    if key:
        lines.append("")
        lines.append("Key Files:")
        for k in key:
            lines.append(f"  - {k}")
    if directory:
        lines.append("")
        lines.append("Directory Structure:")
        lines.append(directory.rstrip())
    if file_types:
        lines.append("")
        lines.append(f"File Types: {file_types}")

    compressed = "\n".join(lines)
    metrics.compressed_tokens = estimate_tokens(compressed)

    # If our compression somehow blew the budget (very pathological
    # input), trim from the file list as the last resort.
    while metrics.compressed_tokens > token_budget and len(files) > 5:
        files = files[: max(5, int(len(files) * 0.75))]
        metrics.files_kept = len(files)
        rebuilt = [
            "REPOSITORY EXPLORATION REPORT", "=============================", "",
            "Files Found:",
            *(f"  - {p}" for p in files),
            f"  …{len(parsed.files_found) - len(files)} more files. "
            "Use 'Find files matching a pattern' or 'Search file contents' to drill down.",
        ]
        if key:
            rebuilt.extend(["", "Key Files:", *(f"  - {k}" for k in key)])
        if directory:
            rebuilt.extend(["", "Directory Structure:", directory.rstrip()])
        if file_types:
            rebuilt.extend(["", f"File Types: {file_types}"])
        compressed = "\n".join(rebuilt)
        metrics.compressed_tokens = estimate_tokens(compressed)

    return compressed, metrics


__all__ = [
    "FLAG_SUBAGENT_EXPLORER",
    "DEFAULT_TOKEN_BUDGET",
    "CompressionReport",
    "compress_exploration_report",
]
