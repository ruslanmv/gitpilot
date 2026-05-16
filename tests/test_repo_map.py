"""Tests for the auto-generated repo map (Batch B6).

Pin the contract the planner relies on:

* Pure function: ``build_repo_map`` is deterministic and side-effect
  free.  Same input → identical output, byte-for-byte.
* Key-files heuristic surfaces the well-known anchors (README.md,
  pyproject.toml, etc.) when they exist.
* Modules are sorted by file count descending so the planner sees
  the most-populated dirs first.
* The rendered AGENTS.md blob honours a token budget — even on a
  10 000-file synthetic repo the output stays bounded.
* Round-trip through the on-disk cache reconstructs the same object.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gitpilot import flags
from gitpilot.context_budget import estimate_tokens
from gitpilot.repo_map import (
    DEFAULT_MAP_TOKEN_BUDGET,
    FLAG_REPO_MAP,
    MAP_MAX_MODULES,
    MAPS_DIR_ENV,
    RepoMap,
    build_repo_map,
    get_or_build_repo_map,
    load_cached,
    save_cached,
)


# ----------------------------------------------------------------------
# Sample inputs
# ----------------------------------------------------------------------

SMALL = [
    "README.md",
    "pyproject.toml",
    "src/main.py",
    "src/util/io.py",
    "src/util/__init__.py",
    "tests/test_main.py",
    "docs/intro.md",
    "Dockerfile",
    "LICENSE",
]


def _large_paths(n: int) -> list[str]:
    paths = ["README.md", "pyproject.toml", "LICENSE"]
    for i in range(n):
        mod = f"src/mod_{i % 20:02d}"
        paths.append(f"{mod}/file_{i:04d}.py")
    return paths


# ----------------------------------------------------------------------
# Determinism / facts
# ----------------------------------------------------------------------

def test_build_is_deterministic() -> None:
    a = build_repo_map(owner="o", repo="r", branch="main", paths=SMALL)
    b = build_repo_map(owner="o", repo="r", branch="main", paths=SMALL)
    # generated_at differs (it's the timestamp); every other field
    # must match byte-for-byte.
    a_dict, b_dict = a.to_dict(), b.to_dict()
    a_dict.pop("generated_at", None)
    b_dict.pop("generated_at", None)
    assert a_dict == b_dict


def test_well_known_files_promoted_to_key() -> None:
    m = build_repo_map(owner="o", repo="r", branch="main", paths=SMALL)
    assert "README.md" in m.key_files
    assert "pyproject.toml" in m.key_files
    assert "Dockerfile" in m.key_files
    assert "LICENSE" in m.key_files


def test_modules_sorted_by_file_count_desc() -> None:
    m = build_repo_map(owner="o", repo="r", branch="main", paths=SMALL)
    counts = [mod.file_count for mod in m.modules]
    assert counts == sorted(counts, reverse=True)


def test_root_files_appear_as_root_pseudo_module() -> None:
    m = build_repo_map(owner="o", repo="r", branch="main", paths=SMALL)
    root_modules = [mod for mod in m.modules if mod.path == "(root)"]
    assert len(root_modules) == 1
    # README, pyproject, Dockerfile, LICENSE are all root-level.
    assert root_modules[0].file_count >= 4


def test_languages_histogram_counts_extensions() -> None:
    m = build_repo_map(owner="o", repo="r", branch="main", paths=SMALL)
    assert m.languages.get("py", 0) >= 3
    assert m.languages.get("md", 0) >= 1
    assert m.languages.get("toml", 0) == 1


def test_total_files_dedupes() -> None:
    """Duplicate paths must not double-count."""
    m = build_repo_map(
        owner="o", repo="r", branch="main",
        paths=SMALL + SMALL,
    )
    assert m.total_files == len(set(SMALL))


# ----------------------------------------------------------------------
# Budget / scaling
# ----------------------------------------------------------------------

def test_agents_md_under_budget_on_large_repo() -> None:
    m = build_repo_map(
        owner="o", repo="r", branch="main",
        paths=_large_paths(2000),
        token_budget=500,
    )
    assert estimate_tokens(m.agents_md) <= 700  # generous slack
    assert "Total files" in m.agents_md
    assert "Modules" in m.agents_md


def test_modules_capped_at_hard_max() -> None:
    """We never want the map to grow unbounded with module count."""
    paths = [f"mod_{i}/file.py" for i in range(MAP_MAX_MODULES * 3)]
    m = build_repo_map(owner="o", repo="r", branch="main", paths=paths)
    assert len(m.modules) <= MAP_MAX_MODULES


def test_agents_md_mentions_tools_for_drill_down() -> None:
    """The map's footer must point the agent to the B1/B2 tools so it
    knows what to do when it needs more detail than the map shows."""
    m = build_repo_map(owner="o", repo="r", branch="main", paths=SMALL)
    assert "Find files matching a pattern" in m.agents_md
    assert "Search file contents" in m.agents_md
    assert "Read file content" in m.agents_md


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------

def test_round_trip_through_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MAPS_DIR_ENV, str(tmp_path))
    a = build_repo_map(
        owner="o", repo="r", branch="main",
        paths=SMALL, commit_sha="deadbeef",
    )
    save_cached(a)
    b = load_cached("o", "r", "main")
    assert b is not None
    assert b.owner == a.owner
    assert b.repo == a.repo
    assert b.branch == a.branch
    assert b.commit_sha == a.commit_sha
    assert b.total_files == a.total_files
    assert b.key_files == a.key_files
    assert [m.path for m in b.modules] == [m.path for m in a.modules]


def test_load_cached_returns_none_for_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MAPS_DIR_ENV, str(tmp_path))
    assert load_cached("nope", "nope", "main") is None


# ----------------------------------------------------------------------
# get_or_build (cache-aware)
# ----------------------------------------------------------------------

def test_get_or_build_uses_cache_when_sha_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MAPS_DIR_ENV, str(tmp_path))

    calls = {"n": 0}
    def _provider():
        calls["n"] += 1
        return SMALL

    a = get_or_build_repo_map(
        owner="o", repo="r", branch="main",
        paths_provider=_provider, commit_sha="abc123",
    )
    b = get_or_build_repo_map(
        owner="o", repo="r", branch="main",
        paths_provider=_provider, commit_sha="abc123",
    )
    assert a.commit_sha == b.commit_sha == "abc123"
    # Provider should have been called only on the first build.
    assert calls["n"] == 1


def test_get_or_build_refreshes_on_new_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MAPS_DIR_ENV, str(tmp_path))

    calls = {"n": 0}
    def _provider():
        calls["n"] += 1
        return SMALL

    get_or_build_repo_map(
        owner="o", repo="r", branch="main",
        paths_provider=_provider, commit_sha="aaa",
    )
    get_or_build_repo_map(
        owner="o", repo="r", branch="main",
        paths_provider=_provider, commit_sha="bbb",
    )
    assert calls["n"] == 2


def test_force_rebuilds_even_with_same_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MAPS_DIR_ENV, str(tmp_path))

    calls = {"n": 0}
    def _provider():
        calls["n"] += 1
        return SMALL

    get_or_build_repo_map(
        owner="o", repo="r", branch="main",
        paths_provider=_provider, commit_sha="aaa",
    )
    get_or_build_repo_map(
        owner="o", repo="r", branch="main",
        paths_provider=_provider, commit_sha="aaa",
        force=True,
    )
    assert calls["n"] == 2


# ----------------------------------------------------------------------
# Sanity
# ----------------------------------------------------------------------

def test_default_budget_is_documented_value() -> None:
    assert DEFAULT_MAP_TOKEN_BUDGET == 500
