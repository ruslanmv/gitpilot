"""Regression tests for the default CrewAI repository tool contract."""
from __future__ import annotations

import ast
from pathlib import Path

AGENT_TOOLS = Path(__file__).resolve().parents[1] / "gitpilot" / "agent_tools.py"


def _module() -> ast.Module:
    return ast.parse(AGENT_TOOLS.read_text())


def _function(name: str) -> ast.FunctionDef:
    for node in _module().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found")


def test_primary_read_tool_keeps_single_argument_schema() -> None:
    """Keep the common read tool simple for smaller ReAct models."""
    read_file = _function("read_file")

    assert [arg.arg for arg in read_file.args.args] == ["file_path"]
    assert read_file.args.defaults == []


def test_default_repository_tools_use_stable_explorer_surface() -> None:
    """The explorer's default tools should match the pre-B1 safe set."""
    module = _module()
    assignments = [
        node
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "REPOSITORY_TOOLS"
            for target in node.targets
        )
    ]
    assert assignments, "REPOSITORY_TOOLS assignment not found"

    value = assignments[-1].value
    assert isinstance(value, ast.List)
    assert [elt.id for elt in value.elts if isinstance(elt, ast.Name)] == [
        "list_repository_files",
        "get_directory_structure",
        "read_file",
        "get_repository_summary",
    ]
