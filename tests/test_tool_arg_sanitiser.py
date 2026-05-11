"""Tests for the CrewAI tool-argument sanitiser.

Regression test for the production failure where small/cheap LLMs pass
the tool's *schema* as the parameter value:

    {"file_path": {"description": "None", "type": "str"}}

instead of the expected plain string.  The sanitiser must unwrap the
common variants and surface a loud error when the value is truly
unrecoverable, so we never silently query GitHub with a stringified
Python dict.
"""
from __future__ import annotations

import pytest

from gitpilot.agent_tools import _sanitize_tool_arg


# ----------------------------------------------------------------------
# Happy paths
# ----------------------------------------------------------------------

def test_plain_string_passes_through() -> None:
    assert _sanitize_tool_arg("README.md") == "README.md"


def test_unwraps_description_key() -> None:
    payload = {"description": "README.md", "type": "str"}
    assert _sanitize_tool_arg(payload) == "README.md"


def test_unwraps_value_key() -> None:
    payload = {"value": "src/main.py"}
    assert _sanitize_tool_arg(payload) == "src/main.py"


def test_unwraps_path_key() -> None:
    payload = {"path": "docs/index.md"}
    assert _sanitize_tool_arg(payload) == "docs/index.md"


def test_prefers_fallback_key_first() -> None:
    payload = {
        "description": "README.md",
        "value": "WRONG.md",
    }
    # Default fallback_key is "description", which should win.
    assert _sanitize_tool_arg(payload) == "README.md"


def test_fallback_key_override() -> None:
    payload = {"description": "ignored", "value": "ok.txt"}
    assert _sanitize_tool_arg(payload, fallback_key="value") == "ok.txt"


def test_falls_back_to_any_non_schema_string_field() -> None:
    payload = {"type": "str", "filename": "deep/file.md"}
    assert _sanitize_tool_arg(payload) == "deep/file.md"


# ----------------------------------------------------------------------
# The exact production failure mode
# ----------------------------------------------------------------------

def test_rejects_literal_none_schema_payload() -> None:
    """The actual payload observed in the failing trace.  Every string
    value is the literal ``"None"`` — there is no usable content, so the
    sanitiser must raise loudly rather than return ``"None"`` and silently
    query GitHub for a file named ``None``."""
    payload = {"description": "None", "type": "str"}
    with pytest.raises(ValueError) as exc:
        _sanitize_tool_arg(payload)
    assert "schema-shaped dict" in str(exc.value)


def test_rejects_empty_dict() -> None:
    with pytest.raises(ValueError):
        _sanitize_tool_arg({})


def test_rejects_none() -> None:
    with pytest.raises(ValueError):
        _sanitize_tool_arg(None)


def test_rejects_list_arg() -> None:
    with pytest.raises(ValueError):
        _sanitize_tool_arg(["README.md"])


# ----------------------------------------------------------------------
# Tolerated weird inputs
# ----------------------------------------------------------------------

def test_int_is_stringified() -> None:
    assert _sanitize_tool_arg(42) == "42"


def test_bool_is_stringified() -> None:
    assert _sanitize_tool_arg(True) == "True"
