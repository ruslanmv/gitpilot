"""Tests for the lazy tool-def pruner — Batch P2-B."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List

import pytest

from gitpilot import flags
from gitpilot.tool_def_pruner import (
    FLAG_LAZY_TOOL_DEFS,
    prune_descriptors,
)
from gitpilot.tool_groups import ToolPolicy


@dataclass(frozen=True)
class FakeDescriptor:
    """Stand-in for ``MCPToolDescriptor`` with the two fields the pruner reads."""

    name: str
    server_id: str


@pytest.fixture(autouse=True)
def _isolate_flags() -> Iterator[None]:
    flags.clear_all_overrides()
    yield
    flags.clear_all_overrides()


@pytest.fixture()
def descriptors() -> List[FakeDescriptor]:
    return [
        FakeDescriptor("query",       "postgres"),
        FakeDescriptor("explain",     "postgres"),
        FakeDescriptor("create_pr",   "github"),
        FakeDescriptor("search_code", "github"),
    ]


# ----------------------------------------------------------------------
# Legacy parity
# ----------------------------------------------------------------------

def test_no_policy_returns_input_unchanged(descriptors: List[FakeDescriptor]) -> None:
    kept, report = prune_descriptors(descriptors, policy=None)
    assert kept == descriptors
    assert report.dropped == 0
    assert report.kept == len(descriptors)


def test_flag_off_returns_input_unchanged(descriptors: List[FakeDescriptor]) -> None:
    flags.set_override(FLAG_LAZY_TOOL_DEFS, False)
    # Construct a restrictive policy that *would* drop everything if active.
    policy = ToolPolicy.from_mode_groups(["read"])  # no MCP category enabled
    kept, report = prune_descriptors(descriptors, policy=policy)
    assert kept == descriptors
    assert report.dropped == 0


# ----------------------------------------------------------------------
# Restrictive policy paths
# ----------------------------------------------------------------------

def test_mcp_disabled_drops_everything(descriptors: List[FakeDescriptor]) -> None:
    flags.set_override(FLAG_LAZY_TOOL_DEFS, True)
    policy = ToolPolicy.from_mode_groups(["read"])  # MCP category absent
    kept, report = prune_descriptors(descriptors, policy=policy)
    assert kept == []
    assert report.dropped == len(descriptors)
    assert report.reason_counts == {"mcp-category-disabled": len(descriptors)}


def test_allowlist_keeps_matching_tools(descriptors: List[FakeDescriptor]) -> None:
    flags.set_override(FLAG_LAZY_TOOL_DEFS, True)
    policy = ToolPolicy.from_mode_groups([
        {"mcp": {"allow": ["postgres.*"]}},
    ])
    kept, report = prune_descriptors(descriptors, policy=policy)
    names = sorted(d.name for d in kept)
    assert names == ["explain", "query"]
    assert report.kept == 2
    assert report.dropped == 2
    assert "not-in-allowlist" in report.reason_counts


def test_explicit_deny_overrides_allow(descriptors: List[FakeDescriptor]) -> None:
    flags.set_override(FLAG_LAZY_TOOL_DEFS, True)
    policy = ToolPolicy.from_mode_groups([
        {"mcp": {
            "allow": ["postgres.*", "github.*"],
            "deny":  ["github.create_pr"],
        }},
    ])
    kept, report = prune_descriptors(descriptors, policy=policy)
    names = sorted(d.name for d in kept)
    assert "create_pr" not in names
    assert "search_code" in names
    assert report.reason_counts.get("tool-denied") == 1


def test_disabled_server_drops_only_that_server(
    descriptors: List[FakeDescriptor],
) -> None:
    flags.set_override(FLAG_LAZY_TOOL_DEFS, True)
    policy = ToolPolicy.from_mode_groups([
        {"mcp": {
            "allow": ["*"],
            "disabledServers": ["github"],
        }},
    ])
    kept, report = prune_descriptors(descriptors, policy=policy)
    assert {d.server_id for d in kept} == {"postgres"}
    assert report.reason_counts.get("server-disabled") == 2


def test_empty_allowlist_keeps_everything_in_mcp_category(
    descriptors: List[FakeDescriptor],
) -> None:
    flags.set_override(FLAG_LAZY_TOOL_DEFS, True)
    policy = ToolPolicy.from_mode_groups([
        {"mcp": {"allow": []}},  # empty allow → permissive within the category
    ])
    kept, _ = prune_descriptors(descriptors, policy=policy)
    assert kept == descriptors


# ----------------------------------------------------------------------
# Glob behaviour
# ----------------------------------------------------------------------

def test_allowlist_supports_globs(descriptors: List[FakeDescriptor]) -> None:
    flags.set_override(FLAG_LAZY_TOOL_DEFS, True)
    policy = ToolPolicy.from_mode_groups([
        {"mcp": {"allow": ["*.query", "*.search_*"]}},
    ])
    kept, _ = prune_descriptors(descriptors, policy=policy)
    names = sorted(d.name for d in kept)
    assert names == ["query", "search_code"]


# ----------------------------------------------------------------------
# Reporting + serialisation
# ----------------------------------------------------------------------

def test_report_to_dict_is_serialisable(descriptors: List[FakeDescriptor]) -> None:
    flags.set_override(FLAG_LAZY_TOOL_DEFS, True)
    policy = ToolPolicy.from_mode_groups([
        {"mcp": {"allow": ["postgres.*"]}},
    ])
    _, report = prune_descriptors(descriptors, policy=policy)
    import json
    payload = json.dumps(report.to_dict())
    assert "kept" in payload and "dropped" in payload
