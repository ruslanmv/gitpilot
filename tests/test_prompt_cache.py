"""Tests for the prompt-cache builder — Batch P2-A."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, List

import pytest

from gitpilot import flags
from gitpilot.prompt_cache import (
    FLAG_PROMPT_CACHE,
    Provider,
    SystemBlock,
    SystemPayload,
    build_system_blocks,
    to_anthropic_kwargs,
    to_legacy_system_string,
)


@pytest.fixture(autouse=True)
def _isolate_flags() -> Iterator[None]:
    flags.clear_all_overrides()
    yield
    flags.clear_all_overrides()


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """A workspace with the three stable-prefix sources populated."""
    (tmp_path / "AGENTS.md").write_text(
        "# AGENTS.md\nThis project uses 4-space indentation.\n"
    )
    rules_dir = tmp_path / ".gitpilot" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "style.md").write_text("Always type-annotate public functions.")
    return tmp_path


# ----------------------------------------------------------------------
# Provider classification
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("anthropic", Provider.ANTHROPIC),
        ("claude-3-5-sonnet", Provider.ANTHROPIC),
        ("openai", Provider.OPENAI),
        ("gpt-4o", Provider.OPENAI),
        ("watsonx", Provider.WATSONX),
        ("ibm/granite", Provider.WATSONX),
        ("ollama", Provider.OLLAMA),
        ("something-else", Provider.OTHER),
        (None, Provider.OTHER),
        ("", Provider.OTHER),
    ],
)
def test_provider_from_string(value: object, expected: Provider) -> None:
    assert Provider.from_string(value) is expected  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Ordering & determinism
# ----------------------------------------------------------------------

def test_ordering_is_deterministic(workspace: Path) -> None:
    tools = [
        {"name": "read_file", "description": "Read a file"},
        {"name": "write_file", "description": "Write a file"},
    ]
    a = build_system_blocks(
        base_system="You are GitPilot.",
        workspace=workspace,
        tool_defs=tools,
        session_conventions="Be concise.",
        provider="anthropic",
        enabled=True,
    )
    b = build_system_blocks(
        base_system="You are GitPilot.",
        workspace=workspace,
        tool_defs=tools,
        session_conventions="Be concise.",
        provider="anthropic",
        enabled=True,
    )
    labels_a = [block.label for block in a.blocks]
    labels_b = [block.label for block in b.blocks]
    assert labels_a == labels_b
    assert labels_a == ["base", "agents_md", "rules", "tool_defs", "session"]
    assert a.cache_prefix_digest == b.cache_prefix_digest


def test_idempotent_when_inputs_unchanged(workspace: Path) -> None:
    args = dict(
        base_system="core",
        workspace=workspace,
        tool_defs=[{"name": "a", "description": "first"}],
        provider="anthropic",
        enabled=True,
    )
    digest_first = build_system_blocks(**args).cache_prefix_digest  # type: ignore[arg-type]
    digest_second = build_system_blocks(**args).cache_prefix_digest  # type: ignore[arg-type]
    assert digest_first == digest_second


def test_tool_def_order_does_not_affect_digest(workspace: Path) -> None:
    tools_forward = [{"name": "a"}, {"name": "b"}]
    tools_reversed = [{"name": "b"}, {"name": "a"}]
    a = build_system_blocks(workspace=workspace, tool_defs=tools_forward,
                            provider="anthropic", enabled=True)
    b = build_system_blocks(workspace=workspace, tool_defs=tools_reversed,
                            provider="anthropic", enabled=True)
    # The digest derives from a sorted-keys JSON dump, so ordering does
    # not affect the cache key — that's the design intent.
    assert a.cache_prefix_digest == b.cache_prefix_digest


# ----------------------------------------------------------------------
# Cache busting on AGENTS.md change
# ----------------------------------------------------------------------

def test_agents_md_change_busts_cache(workspace: Path) -> None:
    before = build_system_blocks(workspace=workspace, provider="anthropic", enabled=True)
    (workspace / "AGENTS.md").write_text("# AGENTS.md\nFully new content.\n")
    after = build_system_blocks(workspace=workspace, provider="anthropic", enabled=True)
    assert before.cache_prefix_digest != after.cache_prefix_digest


def test_rule_file_change_busts_cache(workspace: Path) -> None:
    before = build_system_blocks(workspace=workspace, provider="anthropic", enabled=True)
    (workspace / ".gitpilot" / "rules" / "style.md").write_text("Use 2-space indent.")
    after = build_system_blocks(workspace=workspace, provider="anthropic", enabled=True)
    assert before.cache_prefix_digest != after.cache_prefix_digest


def test_session_conventions_do_not_change_cache_digest(workspace: Path) -> None:
    a = build_system_blocks(workspace=workspace, session_conventions="turn one",
                            provider="anthropic", enabled=True)
    b = build_system_blocks(workspace=workspace, session_conventions="turn TWO",
                            provider="anthropic", enabled=True)
    # The session block is non-cacheable; the cache key must not move.
    assert a.cache_prefix_digest == b.cache_prefix_digest


# ----------------------------------------------------------------------
# Anthropic-only cache markers
# ----------------------------------------------------------------------

def test_anthropic_emits_cache_control_when_flag_on(workspace: Path) -> None:
    payload = build_system_blocks(
        workspace=workspace, provider="anthropic", enabled=True,
        base_system="core",
    )
    assert payload.cache_hits_expected is True
    rendered = payload.to_anthropic_system()
    # At least one cacheable block must carry the ephemeral marker.
    markers = [b.get("cache_control") for b in rendered]
    assert {"type": "ephemeral"} in markers


def test_anthropic_no_cache_control_when_flag_off(workspace: Path) -> None:
    payload = build_system_blocks(
        workspace=workspace, provider="anthropic", enabled=False,
        base_system="core",
    )
    assert payload.cache_hits_expected is False
    for block in payload.to_anthropic_system():
        assert "cache_control" not in block


def test_non_anthropic_provider_never_marks_cache(workspace: Path) -> None:
    for prov in ("openai", "watsonx", "ollama"):
        payload = build_system_blocks(workspace=workspace, provider=prov, enabled=True)
        assert payload.cache_hits_expected is False
        for block in payload.to_anthropic_system():
            assert "cache_control" not in block


def test_anthropic_kwargs_emits_structured_only_when_caching(workspace: Path) -> None:
    on = to_anthropic_kwargs(
        build_system_blocks(workspace=workspace, provider="anthropic", enabled=True)
    )
    off = to_anthropic_kwargs(
        build_system_blocks(workspace=workspace, provider="anthropic", enabled=False)
    )
    assert isinstance(on["system"], list)
    assert isinstance(off["system"], str)


# ----------------------------------------------------------------------
# Legacy compatibility
# ----------------------------------------------------------------------

def test_flat_text_renders_in_order(workspace: Path) -> None:
    payload = build_system_blocks(
        workspace=workspace, base_system="core",
        session_conventions="session-tail",
        provider="anthropic", enabled=True,
    )
    text = to_legacy_system_string(payload)
    # Order matches the documented stable prefix.
    assert text.index("core") < text.index("AGENTS.md")
    assert text.index("AGENTS.md") < text.index("Custom rules")
    assert text.index("Custom rules") < text.index("session-tail")


def test_empty_inputs_produce_empty_payload(tmp_path: Path) -> None:
    payload = build_system_blocks(workspace=tmp_path, provider="anthropic", enabled=True)
    assert payload.blocks == []
    assert payload.to_flat_text() == ""
    assert to_anthropic_kwargs(payload)["system"] in ([], "")


def test_to_dict_is_serialisable(workspace: Path) -> None:
    import json
    payload = build_system_blocks(workspace=workspace, provider="anthropic", enabled=True)
    snapshot = payload.to_dict()
    json.dumps(snapshot)  # must not raise


# ----------------------------------------------------------------------
# Flag wiring
# ----------------------------------------------------------------------

def test_global_flag_drives_default(workspace: Path) -> None:
    flags.set_override(FLAG_PROMPT_CACHE, True)
    payload = build_system_blocks(workspace=workspace, provider="anthropic")
    assert payload.cache_hits_expected is True
    flags.set_override(FLAG_PROMPT_CACHE, False)
    payload = build_system_blocks(workspace=workspace, provider="anthropic")
    assert payload.cache_hits_expected is False
