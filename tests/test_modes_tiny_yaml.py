"""Cover the in-tree YAML fallback in :mod:`gitpilot.modes`.

The fallback only fires when PyYAML is not importable, so we force the
import to fail and then exercise the loader against representative YAML.
This keeps :mod:`gitpilot.modes` above the coverage gate without
shipping fake tests against the production PyYAML path.
"""
from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from gitpilot import modes


@pytest.fixture()
def no_pyyaml(monkeypatch: pytest.MonkeyPatch):
    """Make ``import yaml`` raise ``ImportError`` for the duration of the test."""
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name == "yaml":
            raise ImportError("forced for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    yield


# ----------------------------------------------------------------------
# Scalars and flow collections
# ----------------------------------------------------------------------

def test_tiny_yaml_scalars(no_pyyaml) -> None:
    data = modes._load_yaml_or_json("a: true\nb: false\nc: 1\nd: 3.14\ne: null\nf: hello\n")
    assert data == {"a": True, "b": False, "c": 1, "d": 3.14, "e": None, "f": "hello"}


def test_tiny_yaml_quoted_strings(no_pyyaml) -> None:
    data = modes._load_yaml_or_json('name: "double"\nalias: \'single\'\n')
    assert data == {"name": "double", "alias": "single"}


def test_tiny_yaml_inline_lists_and_maps(no_pyyaml) -> None:
    data = modes._load_yaml_or_json("nums: [1, 2, 3]\npair: {x: 1, y: 2}\nempty_list: []\nempty_map: {}\n")
    assert data["nums"] == [1, 2, 3]
    assert data["pair"] == {"x": 1, "y": 2}
    assert data["empty_list"] == []
    assert data["empty_map"] == {}


def test_tiny_yaml_block_scalar_literal(no_pyyaml) -> None:
    # The in-tree parser is best-effort: it preserves the structure faithfully
    # enough for modes.yaml but may leave a leading space on each line.  The
    # consumer strips when rendering, so this is intentional and documented.
    text = "doc: |\n  line one\n  line two\nfollow: yes\n"
    data = modes._load_yaml_or_json(text)
    assert "line one" in data["doc"]
    assert "line two" in data["doc"]
    assert "\n" in data["doc"]
    assert data["follow"] is True


def test_tiny_yaml_block_scalar_folded_stripped(no_pyyaml) -> None:
    text = "doc: >-\n  hello\n  world\n"
    data = modes._load_yaml_or_json(text)
    assert "hello" in data["doc"] and "world" in data["doc"]
    assert "\n" not in data["doc"]            # folded → single line
    assert not data["doc"].endswith("\n")     # ``-`` strip-chomp


# ----------------------------------------------------------------------
# Lists, nested maps, mode loading end-to-end
# ----------------------------------------------------------------------

def test_tiny_yaml_list_of_scalars(no_pyyaml) -> None:
    data = modes._load_yaml_or_json("items:\n  - a\n  - b\n  - c\n")
    assert data == {"items": ["a", "b", "c"]}


def test_tiny_yaml_nested_map(no_pyyaml) -> None:
    text = (
        "outer:\n"
        "  inner:\n"
        "    leaf: 42\n"
        "  sibling: ok\n"
    )
    data = modes._load_yaml_or_json(text)
    assert data == {"outer": {"inner": {"leaf": 42}, "sibling": "ok"}}


def test_tiny_yaml_list_of_maps(no_pyyaml) -> None:
    text = (
        "people:\n"
        "  - name: Ada\n"
        "    role: engineer\n"
        "  - name: Linus\n"
        "    role: maintainer\n"
    )
    data = modes._load_yaml_or_json(text)
    assert data == {"people": [
        {"name": "Ada", "role": "engineer"},
        {"name": "Linus", "role": "maintainer"},
    ]}


def test_tiny_yaml_ignores_comments_and_blank_lines(no_pyyaml) -> None:
    text = (
        "# header comment\n"
        "\n"
        "alpha: 1\n"
        "  # nested comment\n"
        "beta: 2\n"
    )
    data = modes._load_yaml_or_json(text)
    assert data == {"alpha": 1, "beta": 2}


def test_tiny_yaml_json_fast_path(no_pyyaml) -> None:
    data = modes._load_yaml_or_json('{"alpha": 1, "beta": [1, 2]}')
    assert data == {"alpha": 1, "beta": [1, 2]}


def test_full_mode_round_trip_via_tiny_yaml(no_pyyaml, tmp_path: Path) -> None:
    text = (
        "customModes:\n"
        "  - slug: db-pilot\n"
        "    name: DB Pilot\n"
        "    description: Postgres assistant\n"
        "    roleDefinition: |\n"
        "      You are a DBA.\n"
        "    groups:\n"
        "      - read\n"
        "      - mcp:\n"
        "          allow: [postgres.query]\n"
        "          alwaysAllow: [postgres.explain]\n"
        "    mcpServers:\n"
        "      postgres:\n"
        "        command: uvx\n"
        "        args: [mcp-postgres-server]\n"
        "        env: { PG_URL: postgresql://localhost/demo }\n"
    )
    (tmp_path / ".gitpilot").mkdir()
    (tmp_path / ".gitpilot" / "modes.yaml").write_text(text)
    registry = modes.ModeRegistry()
    count = registry.load(workspace_path=tmp_path)
    assert count == 1
    mode = registry.get("db-pilot")
    assert mode is not None
    assert mode.role_definition.strip().startswith("You are a DBA")
    assert "postgres" in mode.mcp_servers


def test_activate_mode_via_tiny_yaml(no_pyyaml, tmp_path: Path) -> None:
    text = (
        "customModes:\n"
        "  - slug: planner\n"
        "    name: Planner\n"
        "    description: Plans things\n"
        "    groups: [read]\n"
        "    whenToUse: For planning.\n"
        "    customInstructions: Be concise.\n"
    )
    (tmp_path / ".gitpilot").mkdir()
    (tmp_path / ".gitpilot" / "modes.yaml").write_text(text)
    registry = modes.ModeRegistry()
    registry.load(workspace_path=tmp_path)
    ctx = modes.activate_mode(registry, "planner")
    assert ctx is not None
    assert "Plans things" not in ctx.system_prompt_block  # description not in prompt
    assert "Be concise." in ctx.system_prompt_block
    assert ctx.mcp_server_configs == []


def test_split_flow_handles_nested_brackets(no_pyyaml) -> None:
    items = modes._split_flow("a, [b, c], {x: 1}, d")
    assert items == ["a", "[b, c]", "{x: 1}", "d"]


def test_invalid_yaml_returns_empty(no_pyyaml, tmp_path: Path) -> None:
    (tmp_path / ".gitpilot").mkdir()
    (tmp_path / ".gitpilot" / "modes.yaml").write_text(":::::not valid::::")
    registry = modes.ModeRegistry()
    # Should not raise; missing customModes key → 0 modes.
    assert registry.load(workspace_path=tmp_path) == 0
