# gitpilot/yaml_lite.py
"""A tiny YAML reader for GitPilot's own configuration files.

PyYAML is an optional dependency: a stripped install must still be able to read
``modes.yaml``, ``topologies.yaml`` and the built-in topology documents, because
those files decide what the agent is *allowed to do*. Silently ignoring a policy
file because a parser is missing would mean falling back to the permissive
default — exactly the wrong direction to fail in.

So: use ``yaml`` when it is installed, then JSON-masquerading-as-YAML, then an
in-tree parser covering the subset GitPilot's own files use — nested mappings,
lists, block scalars and inline flows.

This module was extracted from :mod:`gitpilot.modes` in Batch V4-G1, when the
topology loader became the second consumer. It has no GitPilot imports, so any
config layer can use it without pulling in a dependency graph.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

__all__ = ["load_yaml_or_json", "tiny_yaml", "scalar", "split_flow"]


def load_yaml_or_json(text: str) -> Dict[str, Any]:
    """Parse YAML or JSON text.  Prefers ``yaml`` when installed.

    Falls back to ``json`` for ``.yaml`` files that happen to be JSON
    and to a tiny in-tree YAML subset otherwise.  The subset supports
    the shape used by ``modes.yaml``: nested mappings, lists, and
    folded/block scalars.
    """
    try:
        import yaml

        loaded = yaml.safe_load(text)
        if isinstance(loaded, dict):
            return loaded
        return {}
    except ImportError:
        pass
    # Fast path: JSON masquerading as YAML.
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            parsed_json = json.loads(stripped)
            if isinstance(parsed_json, dict):
                return parsed_json
        except Exception:
            pass
    return tiny_yaml(text)


# --- in-tree minimal YAML parser ---------------------------------------
# Supports: scalars, lists ("- foo"), nested maps via indentation, block
# scalars ("|" and ">-"), and inline ``{a: 1, b: 2}`` / ``[a, b]`` flows.
# Sufficient for ``modes.yaml`` examples shipped with GitPilot.

_BLOCK_SCALAR_RE = re.compile(r"^(?P<key>[^:#\s][^:]*):\s*(?P<style>[|>][-+]?)\s*$")
_KEY_VAL_RE = re.compile(r"^(?P<key>[^:#\s][^:]*?):\s*(?P<value>.*)$")
_LIST_ITEM_RE = re.compile(r"^- ?(?P<rest>.*)$")


def tiny_yaml(text: str) -> Dict[str, Any]:
    lines = text.splitlines()
    pos = [0]

    def parse_block(indent: int) -> Any:
        # Decide list vs map by first non-blank child.
        while pos[0] < len(lines) and not lines[pos[0]].strip():
            pos[0] += 1
        if pos[0] >= len(lines):
            return None
        first = lines[pos[0]]
        cur_indent = len(first) - len(first.lstrip(" "))
        if cur_indent < indent:
            return None
        stripped = first[cur_indent:]
        if stripped.startswith("- "):
            return parse_list(cur_indent)
        return parse_map(cur_indent)

    def parse_map(indent: int) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        while pos[0] < len(lines):
            raw = lines[pos[0]]
            if not raw.strip() or raw.lstrip().startswith("#"):
                pos[0] += 1
                continue
            cur_indent = len(raw) - len(raw.lstrip(" "))
            if cur_indent < indent:
                break
            if cur_indent > indent:
                break
            stripped = raw[cur_indent:]
            block = _BLOCK_SCALAR_RE.match(stripped)
            if block:
                key = block.group("key").strip()
                pos[0] += 1
                result[key] = _read_blockscalar(cur_indent + 1, block.group("style"))
                continue
            m = _KEY_VAL_RE.match(stripped)
            if not m:
                pos[0] += 1
                continue
            key = m.group("key").strip()
            value = m.group("value").strip()
            pos[0] += 1
            if value == "" or value is None:
                # Nested block (map or list)
                nested = parse_block(cur_indent + 1)
                result[key] = nested if nested is not None else None
            else:
                result[key] = scalar(value)
        return result

    def parse_list(indent: int) -> List[Any]:
        result: List[Any] = []
        while pos[0] < len(lines):
            raw = lines[pos[0]]
            if not raw.strip() or raw.lstrip().startswith("#"):
                pos[0] += 1
                continue
            cur_indent = len(raw) - len(raw.lstrip(" "))
            if cur_indent < indent:
                break
            if cur_indent > indent:
                break
            stripped = raw[cur_indent:]
            lm = _LIST_ITEM_RE.match(stripped)
            if not lm:
                break
            rest = lm.group("rest").rstrip()
            pos[0] += 1
            if not rest:
                # Next line is a nested map or list
                nested = parse_block(cur_indent + 2)
                result.append(nested)
                continue
            # ``- key: value`` form starts an inline map.
            inline = _KEY_VAL_RE.match(rest)
            if inline:
                key = inline.group("key").strip()
                value = inline.group("value").strip()
                item: Dict[str, Any] = {}
                if value:
                    item[key] = scalar(value)
                else:
                    nested = parse_block(cur_indent + 2)
                    item[key] = nested
                # Continue collecting remaining map keys at the same
                # indent as the dash continuation (cur_indent + 2).
                child_indent = cur_indent + 2
                extra = parse_map(child_indent)
                item.update(extra)
                result.append(item)
            else:
                result.append(scalar(rest))
        return result

    def _read_blockscalar(indent: int, style: str) -> str:
        buf: List[str] = []
        while pos[0] < len(lines):
            raw = lines[pos[0]]
            if not raw.strip():
                buf.append("")
                pos[0] += 1
                continue
            cur_indent = len(raw) - len(raw.lstrip(" "))
            if cur_indent < indent:
                break
            buf.append(raw[indent:])
            pos[0] += 1
        joined = "\n".join(buf)
        if style.startswith(">"):
            joined = joined.replace("\n\n", "\f").replace("\n", " ").replace("\f", "\n\n")
        if style.endswith("-"):
            joined = joined.rstrip("\n")
        return joined

    root = parse_map(0)
    if not isinstance(root, dict):
        return {}
    return root


def scalar(raw: str) -> Any:
    s = raw.strip()
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [scalar(x) for x in split_flow(inner)]
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        if not inner:
            return {}
        out: Dict[str, Any] = {}
        for piece in split_flow(inner):
            if ":" in piece:
                k, v = piece.split(":", 1)
                out[k.strip()] = scalar(v)
        return out
    low = s.lower()
    if low in {"true", "yes"}:
        return True
    if low in {"false", "no"}:
        return False
    if low in {"null", "~", ""}:
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def split_flow(text: str) -> List[str]:
    """Split a flow sequence on commas, respecting nested [] and {}."""
    out: List[str] = []
    depth = 0
    buf: List[str] = []
    for ch in text:
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return out
