"""Hierarchical repository map (Batch B6).

Generates a compact, factual "site map" of a repository — what
languages, what top-level modules, which files are entry points —
and persists it so every planner prompt can be primed with the same
high-level overview without re-discovering it each turn.

Inspired by Aider's repo-map, Cursor's project context, and the
``AGENTS.md`` convention.  This implementation is fully local: no
LLM call needed.  We read the file tree, count extensions, identify
"key" files via well-known names, and emit a markdown blob bounded
by a hard token budget (default 500).

Storage:
  ~/.gitpilot/repo_maps/<sha1(owner/repo/branch)>.json

Invalidation:
  Stored alongside the commit SHA the map was built from.  When the
  branch's HEAD moves, callers can detect the staleness via
  ``RepoMap.commit_sha`` and refresh.

Wiring:
  Phase 6 of the enterprise roadmap.  The next batch will inject
  ``repo_map.agents_md`` into the planner's backstory through the
  existing ``context_pack`` slot in ``generate_plan``.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from . import flags
from .context_budget import estimate_tokens

logger = logging.getLogger(__name__)

FLAG_REPO_MAP = "repo_map"

DEFAULT_MAP_TOKEN_BUDGET = 500
MAP_MAX_KEY_FILES = 10
MAP_MAX_MODULES = 12
MAP_MAX_FILES_PER_MODULE = 6
MAPS_DIR_ENV = "GITPILOT_REPO_MAPS_DIR"

# Files we always lift into "key files" when present.  Ordered by
# importance — first match wins for ranking.
_WELL_KNOWN_KEY_FILES: tuple[str, ...] = (
    "README.md",
    "README.rst",
    "README",
    "AGENTS.md",
    "CLAUDE.md",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Dockerfile",
    "docker-compose.yml",
    "Makefile",
    ".github/workflows/ci.yml",
    "LICENSE",
    "CHANGELOG.md",
)


def _coerce_int_safe(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _coerce_str_list(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if x is not None]


def _coerce_lang_counts(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(k): _coerce_int_safe(v) for k, v in value.items()}


@dataclass
class ModuleSummary:
    path: str           # directory path, e.g. "src/util"
    files: List[str] = field(default_factory=list)
    file_count: int = 0


@dataclass
class RepoMap:
    """In-memory + on-disk representation of a repo's site map."""
    owner: str
    repo: str
    branch: str
    commit_sha: Optional[str] = None
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )
    languages: dict[str, int] = field(default_factory=dict)
    key_files: List[str] = field(default_factory=list)
    modules: List[ModuleSummary] = field(default_factory=list)
    total_files: int = 0
    agents_md: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "RepoMap":
        raw_modules = data.get("modules", []) or []
        modules: List[ModuleSummary] = []
        if isinstance(raw_modules, list):
            for m in raw_modules:
                if isinstance(m, dict):
                    modules.append(ModuleSummary(
                        path=str(m.get("path", "") or ""),
                        files=_coerce_str_list(m.get("files")),
                        file_count=_coerce_int_safe(m.get("file_count")),
                    ))
        out = cls(
            owner=str(data.get("owner", "") or ""),
            repo=str(data.get("repo", "") or ""),
            branch=str(data.get("branch", "") or ""),
            commit_sha=(
                str(data["commit_sha"]) if data.get("commit_sha") is not None else None
            ),
            generated_at=str(data.get("generated_at", "") or ""),
            languages=_coerce_lang_counts(data.get("languages")),
            key_files=_coerce_str_list(data.get("key_files")),
            modules=modules,
            total_files=_coerce_int_safe(data.get("total_files")),
            agents_md=str(data.get("agents_md", "") or ""),
        )
        return out


# ----------------------------------------------------------------------
# Storage helpers
# ----------------------------------------------------------------------

def _maps_root() -> Path:
    import os

    override = os.environ.get(MAPS_DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".gitpilot" / "repo_maps"


def _cache_key(owner: str, repo: str, branch: str) -> str:
    raw = f"{owner}/{repo}@{branch}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:24]


def _cache_path(owner: str, repo: str, branch: str) -> Path:
    return _maps_root() / f"{_cache_key(owner, repo, branch)}.json"


def load_cached(owner: str, repo: str, branch: str) -> Optional[RepoMap]:
    path = _cache_path(owner, repo, branch)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RepoMap.from_dict(data)
    except Exception as exc:
        logger.debug("[repo-map] could not load %s: %s", path, exc)
        return None


def save_cached(repo_map: RepoMap) -> None:
    root = _maps_root()
    root.mkdir(parents=True, exist_ok=True)
    path = _cache_path(repo_map.owner, repo_map.repo, repo_map.branch)
    try:
        path.write_text(json.dumps(repo_map.to_dict(), indent=2), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[repo-map] could not save %s: %s", path, exc)


# ----------------------------------------------------------------------
# Map builder
# ----------------------------------------------------------------------

def _extension_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return "(no-ext)"
    return name.rsplit(".", 1)[-1].lower()


def _top_dir_of(path: str) -> str:
    """Return the first directory segment of a path; '' for root files."""
    if "/" not in path:
        return ""
    return path.split("/", 1)[0]


def _group_into_modules(paths: List[str]) -> List[ModuleSummary]:
    """Group files by top-level directory.  Root-level files form a
    "(root)" pseudo-module so they're still visible."""
    buckets: dict[str, List[str]] = {}
    for p in sorted(paths):
        top = _top_dir_of(p) or "(root)"
        buckets.setdefault(top, []).append(p)

    modules: List[ModuleSummary] = []
    for name, files in buckets.items():
        modules.append(
            ModuleSummary(
                path=name,
                files=files[:MAP_MAX_FILES_PER_MODULE],
                file_count=len(files),
            )
        )
    # Sort by file count descending so the most-populated modules
    # come first — these are the ones the planner most needs to see.
    modules.sort(key=lambda m: (-m.file_count, m.path))
    return modules[:MAP_MAX_MODULES]


def _select_key_files(paths: List[str]) -> List[str]:
    by_name = {p.rsplit("/", 1)[-1]: p for p in paths}
    selected: List[str] = []
    for well_known in _WELL_KNOWN_KEY_FILES:
        # Match either the bare name at any depth or the exact path.
        if well_known in paths:
            selected.append(well_known)
            continue
        if "/" in well_known:
            if well_known in paths:
                selected.append(well_known)
            continue
        if well_known in by_name:
            selected.append(by_name[well_known])
        if len(selected) >= MAP_MAX_KEY_FILES:
            break
    return selected


def _render_agents_md(repo_map: RepoMap, *, token_budget: int) -> str:
    """Render the markdown blob that gets pinned into planner prompts.

    Bounded by ``token_budget``.  If the first-pass output overshoots
    we trim modules from the tail (least-populated) and try again.
    """
    def _build(modules: List[ModuleSummary]) -> str:
        lines: list[str] = []
        lines.append(f"# Repository map — `{repo_map.owner}/{repo_map.repo}` @ `{repo_map.branch}`")
        lines.append("")
        lines.append(f"**Total files:** {repo_map.total_files}")
        if repo_map.languages:
            top = sorted(repo_map.languages.items(), key=lambda kv: -kv[1])[:8]
            lines.append(
                "**Languages:** " + ", ".join(f"`{ext}`={n}" for ext, n in top)
            )
        if repo_map.key_files:
            lines.append("")
            lines.append("## Key files")
            for kf in repo_map.key_files:
                lines.append(f"- `{kf}`")
        if modules:
            lines.append("")
            lines.append("## Modules")
            for mod in modules:
                lines.append(f"- **`{mod.path}/`** — {mod.file_count} file(s)")
                for f in mod.files:
                    lines.append(f"    - `{f}`")
        lines.append("")
        lines.append(
            "_Use the `Find files matching a pattern`, `Search file contents` "
            "and `Read file content` tools to drill into anything above._"
        )
        return "\n".join(lines)

    modules = list(repo_map.modules)
    out = _build(modules)
    while estimate_tokens(out) > token_budget and len(modules) > 3:
        # Drop the least-populated module and try again.
        modules = modules[:-1]
        out = _build(modules)
    return out


def build_repo_map(
    *,
    owner: str,
    repo: str,
    branch: str,
    paths: Iterable[str],
    commit_sha: Optional[str] = None,
    token_budget: int = DEFAULT_MAP_TOKEN_BUDGET,
) -> RepoMap:
    """Deterministically construct a :class:`RepoMap` from a list of
    repository file paths.  Pure function — no I/O, no network, no
    LLM call.  Caller is responsible for fetching the paths (today
    that's ``get_repo_tree`` for GitHub mode or ``Path.rglob`` for
    local mode).
    """
    files = sorted({p.strip() for p in paths if p and isinstance(p, str)})
    languages = dict(Counter(_extension_of(p) for p in files))
    key_files = _select_key_files(files)
    modules = _group_into_modules(files)

    repo_map = RepoMap(
        owner=owner,
        repo=repo,
        branch=branch,
        commit_sha=commit_sha,
        languages=languages,
        key_files=key_files,
        modules=modules,
        total_files=len(files),
    )
    repo_map.agents_md = _render_agents_md(repo_map, token_budget=token_budget)
    return repo_map


def get_or_build_repo_map(
    *,
    owner: str,
    repo: str,
    branch: str,
    paths_provider: Callable[[], Iterable[str]],
    commit_sha: Optional[str] = None,
    token_budget: int = DEFAULT_MAP_TOKEN_BUDGET,
    force: bool = False,
) -> RepoMap:
    """Return a cached map if it's still valid for the current commit,
    otherwise build a fresh one and persist.  ``paths_provider`` is
    a zero-arg callable that returns ``Iterable[str]`` of repo paths —
    keeps this function independent of how paths are fetched.
    """
    if not force:
        cached = load_cached(owner, repo, branch)
        if cached and cached.commit_sha == commit_sha and commit_sha is not None:
            return cached

    paths = list(paths_provider())
    fresh = build_repo_map(
        owner=owner, repo=repo, branch=branch,
        paths=paths, commit_sha=commit_sha,
        token_budget=token_budget,
    )
    save_cached(fresh)
    return fresh


__all__ = [
    "FLAG_REPO_MAP",
    "DEFAULT_MAP_TOKEN_BUDGET",
    "ModuleSummary",
    "RepoMap",
    "build_repo_map",
    "get_or_build_repo_map",
    "load_cached",
    "save_cached",
]
