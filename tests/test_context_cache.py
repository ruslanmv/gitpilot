"""Tests for the context-pack memoisation layer — Batch P2-C."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

from gitpilot import flags
from gitpilot.context_cache import (
    FLAG_CONTEXT_CACHE,
    build_cached,
    clear_cache,
    get_cache_stats,
    set_capacity,
)


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    flags.clear_all_overrides()
    clear_cache()
    set_capacity(32)
    yield
    flags.clear_all_overrides()
    clear_cache()


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    gitpilot_dir = tmp_path / ".gitpilot"
    gitpilot_dir.mkdir()
    # Populate the conventions block so the pack isn't empty.
    (gitpilot_dir / "GITPILOT.md").write_text("# Conventions\nUse 4 spaces.\n")
    return tmp_path


# ----------------------------------------------------------------------
# Passthrough (flag off)
# ----------------------------------------------------------------------

def test_flag_off_does_not_cache(workspace: Path) -> None:
    flags.set_override(FLAG_CONTEXT_CACHE, False)
    a = build_cached(workspace, "query A")
    b = build_cached(workspace, "query A")
    assert a == b
    stats = get_cache_stats()
    assert stats.size == 0


# ----------------------------------------------------------------------
# Hits and misses
# ----------------------------------------------------------------------

def test_identical_args_hit_cache(workspace: Path) -> None:
    flags.set_override(FLAG_CONTEXT_CACHE, True)
    build_cached(workspace, "query A")
    build_cached(workspace, "query A")
    stats = get_cache_stats()
    assert stats.misses == 1
    assert stats.hits == 1
    assert stats.size == 1


def test_different_query_misses(workspace: Path) -> None:
    flags.set_override(FLAG_CONTEXT_CACHE, True)
    build_cached(workspace, "query A")
    build_cached(workspace, "query B")
    stats = get_cache_stats()
    assert stats.misses == 2
    assert stats.size == 2


def test_different_mode_misses(workspace: Path) -> None:
    flags.set_override(FLAG_CONTEXT_CACHE, True)
    build_cached(workspace, "q", mode_slug="coder")
    build_cached(workspace, "q", mode_slug="reviewer")
    stats = get_cache_stats()
    assert stats.misses == 2


def test_different_workspace_misses(workspace: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    flags.set_override(FLAG_CONTEXT_CACHE, True)
    other = tmp_path_factory.mktemp("other")
    (other / ".gitpilot").mkdir()
    (other / ".gitpilot" / "GITPILOT.md").write_text("# Other\n")
    build_cached(workspace, "q")
    build_cached(other, "q")
    stats = get_cache_stats()
    assert stats.misses == 2


# ----------------------------------------------------------------------
# Mtime invalidation
# ----------------------------------------------------------------------

def test_touch_invalidates_cache(workspace: Path) -> None:
    flags.set_override(FLAG_CONTEXT_CACHE, True)
    build_cached(workspace, "q")
    target = workspace / ".gitpilot" / "GITPILOT.md"
    # Bump mtime by a second to guarantee a different stat value.
    new_mtime = target.stat().st_mtime + 5
    os.utime(target, (new_mtime, new_mtime))
    build_cached(workspace, "q")
    stats = get_cache_stats()
    assert stats.misses == 2
    assert stats.hits == 0


def test_rule_file_change_invalidates_cache(workspace: Path) -> None:
    flags.set_override(FLAG_CONTEXT_CACHE, True)
    rules = workspace / ".gitpilot" / "rules"
    rules.mkdir()
    rule_file = rules / "style.md"
    rule_file.write_text("Use 4-space indent.")
    build_cached(workspace, "q")
    new_mtime = rule_file.stat().st_mtime + 5
    os.utime(rule_file, (new_mtime, new_mtime))
    build_cached(workspace, "q")
    stats = get_cache_stats()
    assert stats.misses >= 2


# ----------------------------------------------------------------------
# Capacity
# ----------------------------------------------------------------------

def test_capacity_evicts_oldest_entry(workspace: Path) -> None:
    flags.set_override(FLAG_CONTEXT_CACHE, True)
    set_capacity(2)
    build_cached(workspace, "q1")
    build_cached(workspace, "q2")
    build_cached(workspace, "q3")  # evicts q1
    stats = get_cache_stats()
    assert stats.size == 2
    # Re-hitting q1 now misses again.
    misses_before = stats.misses
    build_cached(workspace, "q1")
    assert get_cache_stats().misses == misses_before + 1


# ----------------------------------------------------------------------
# Stats serialisation
# ----------------------------------------------------------------------

def test_stats_serialise(workspace: Path) -> None:
    flags.set_override(FLAG_CONTEXT_CACHE, True)
    build_cached(workspace, "q")
    build_cached(workspace, "q")
    import json
    payload = json.dumps(get_cache_stats().to_dict())
    assert "hit_ratio" in payload


def test_clear_resets_counters(workspace: Path) -> None:
    flags.set_override(FLAG_CONTEXT_CACHE, True)
    build_cached(workspace, "q")
    clear_cache()
    stats = get_cache_stats()
    assert stats.size == 0
    assert stats.hits == 0
    assert stats.misses == 0
