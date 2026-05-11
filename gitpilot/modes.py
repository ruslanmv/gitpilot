# gitpilot/modes.py
"""Custom modes — declarative YAML personas with bound tool policies.

A mode is a YAML record describing GitPilot's behaviour for a session.
Schema is intentionally minimal so a developer can add a new mode (and
attach new MCP servers to it) in a few lines.

Files searched, in this order::

    ~/.gitpilot/modes.yaml       — user-global modes
    .gitpilot/modes.yaml         — project modes (project wins on slug clash)

Example::

    customModes:
      - slug: db-pilot
        name: "DB Pilot"
        description: "Natural-language queries against staging Postgres"
        roleDefinition: |
          You are a senior DBA.  Always EXPLAIN before mutating.
        whenToUse: |
          User asks about schema, queries, or migrations.
        groups:
          - read
          - mcp:
              allow: ["postgres.query", "postgres.explain"]
              alwaysAllow: ["postgres.explain"]
          - edit:
              fileRegex: "^migrations/.*\\.sql$"
        customInstructions: |
          Refuse DROP / TRUNCATE without explicit confirmation.
        mcpServers:
          postgres:
            command: uvx
            args: [mcp-postgres-server]
            env: { PG_URL: "${STAGING_PG_URL}" }
            alwaysAllow: [postgres.explain]

Nothing in :mod:`gitpilot.modes` mutates the legacy code path — callers
opt in by instantiating :class:`ModeRegistry` and asking for the
:class:`Mode` they want to activate.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .tool_groups import ToolPolicy

logger = logging.getLogger(__name__)

USER_MODES_FILE = Path.home() / ".gitpilot" / "modes.yaml"
PROJECT_MODES_REL = Path(".gitpilot") / "modes.yaml"


# ----------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------

@dataclass
class ModeMCPServer:
    """An MCP server declared inline by a mode."""

    name: str
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    url: Optional[str] = None
    http_url: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    always_allow: List[str] = field(default_factory=list)
    enabled_tools: List[str] = field(default_factory=list)

    def to_mcp_client_dict(self) -> Dict[str, Any]:
        """Render as the dict shape :class:`MCPServerConfig` accepts."""
        transport = "stdio"
        if self.http_url:
            transport = "http"
        elif self.url:
            transport = "sse"
        return {
            "name": self.name,
            "transport": transport,
            "command": self.command,
            "args": self.args,
            "env": self.env,
            "url": self.url or self.http_url,
            "headers": self.headers,
        }


@dataclass
class Mode:
    """A declarative GitPilot mode."""

    slug: str
    name: str
    description: str = ""
    role_definition: str = ""
    when_to_use: str = ""
    custom_instructions: str = ""
    groups: List[Any] = field(default_factory=list)
    mcp_servers: Dict[str, ModeMCPServer] = field(default_factory=dict)
    source: str = ""  # "user" | "project"

    def tool_policy(self) -> ToolPolicy:
        return ToolPolicy.from_mode_groups(self.groups)

    def system_prompt_block(self) -> str:
        parts: List[str] = []
        if self.role_definition:
            parts.append(f"## Role\n{self.role_definition.strip()}")
        if self.when_to_use:
            parts.append(f"## When to use this mode\n{self.when_to_use.strip()}")
        if self.custom_instructions:
            parts.append(f"## Mode instructions\n{self.custom_instructions.strip()}")
        return "\n\n".join(parts)


# ----------------------------------------------------------------------
# Registry / loader
# ----------------------------------------------------------------------

class ModeRegistry:
    """Discover modes from user + project YAML files."""

    def __init__(self) -> None:
        self._modes: Dict[str, Mode] = {}

    # ----- public ---------------------------------------------------
    def load(self, workspace_path: Optional[Path] = None) -> int:
        count = 0
        count += self._load_file(USER_MODES_FILE, source="user")
        if workspace_path is not None:
            count += self._load_file(workspace_path / PROJECT_MODES_REL, source="project")
        return count

    def register(self, mode: Mode) -> None:
        self._modes[mode.slug] = mode

    def get(self, slug: str) -> Optional[Mode]:
        return self._modes.get(slug)

    def all(self) -> List[Mode]:
        return list(self._modes.values())

    def listing(self) -> List[Dict[str, str]]:
        return [
            {
                "slug": m.slug,
                "name": m.name,
                "description": m.description,
                "source": m.source,
            }
            for m in self._modes.values()
        ]

    # ----- loading --------------------------------------------------
    def _load_file(self, path: Path, *, source: str) -> int:
        if not path.exists():
            return 0
        try:
            data = _load_yaml_or_json(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("could not parse modes file %s: %s", path, e)
            return 0
        modes = data.get("customModes") if isinstance(data, dict) else None
        if not isinstance(modes, list):
            return 0
        count = 0
        for entry in modes:
            if not isinstance(entry, dict):
                continue
            slug = entry.get("slug")
            if not slug:
                continue
            mode = _build_mode(entry, source=source)
            self._modes[slug] = mode  # project loaded second, wins
            count += 1
        return count


def _build_mode(entry: Dict[str, Any], *, source: str) -> Mode:
    mcp_servers: Dict[str, ModeMCPServer] = {}
    raw_servers = entry.get("mcpServers") or {}
    if isinstance(raw_servers, dict):
        for name, cfg in raw_servers.items():
            if not isinstance(cfg, dict):
                continue
            mcp_servers[name] = ModeMCPServer(
                name=name,
                command=cfg.get("command"),
                args=list(cfg.get("args", [])),
                env={k: _expand_env(v) for k, v in (cfg.get("env") or {}).items()},
                url=cfg.get("url"),
                http_url=cfg.get("httpURL") or cfg.get("http_url"),
                headers={**(cfg.get("headers") or {})},
                always_allow=list(cfg.get("alwaysAllow", [])),
                enabled_tools=list(cfg.get("enabledTools", [])),
            )
    return Mode(
        slug=str(entry["slug"]),
        name=str(entry.get("name", entry["slug"])),
        description=str(entry.get("description", "")),
        role_definition=str(entry.get("roleDefinition", "")),
        when_to_use=str(entry.get("whenToUse", "")),
        custom_instructions=str(entry.get("customInstructions", "")),
        groups=list(entry.get("groups", [])),
        mcp_servers=mcp_servers,
        source=source,
    )


# ----------------------------------------------------------------------
# Session lifecycle helper
# ----------------------------------------------------------------------

@dataclass
class ActiveModeContext:
    """Bundle of artefacts derived from the active mode for a session.

    Returned by :func:`activate_mode` so the caller can:

      * inject ``system_prompt_block`` into the agent system prompt
      * pass ``tool_policy`` to the executor
      * spin up the MCP servers listed in ``mcp_server_configs``
        (each dict is ready for :class:`gitpilot.mcp_client.MCPServerConfig.from_dict`)
    """

    mode: Mode
    system_prompt_block: str
    tool_policy: ToolPolicy
    mcp_server_configs: List[Dict[str, Any]]
    extra_mcp_toggles: List[Tuple[str, List[str], List[str]]]  # (server, allow, alwaysAllow)


def activate_mode(registry: ModeRegistry, slug: str) -> Optional[ActiveModeContext]:
    """Resolve a mode by slug and return the bundle to apply.

    Returns ``None`` for an unknown slug — callers should fall back to
    the legacy unconfigured behaviour.
    """
    mode = registry.get(slug)
    if mode is None:
        return None
    server_configs = [s.to_mcp_client_dict() for s in mode.mcp_servers.values()]
    extras = [
        (s.name, list(s.enabled_tools), list(s.always_allow))
        for s in mode.mcp_servers.values()
    ]
    return ActiveModeContext(
        mode=mode,
        system_prompt_block=mode.system_prompt_block(),
        tool_policy=mode.tool_policy(),
        mcp_server_configs=server_configs,
        extra_mcp_toggles=extras,
    )


# ----------------------------------------------------------------------
# Minimal YAML loader (no PyYAML dependency)
# ----------------------------------------------------------------------

def _expand_env(value: Any) -> str:
    if isinstance(value, str):
        return os.path.expandvars(value)
    return str(value)


def _load_yaml_or_json(text: str) -> Dict[str, Any]:
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
    return _tiny_yaml(text)


# --- in-tree minimal YAML parser ---------------------------------------
# Supports: scalars, lists ("- foo"), nested maps via indentation, block
# scalars ("|" and ">-"), and inline ``{a: 1, b: 2}`` / ``[a, b]`` flows.
# Sufficient for ``modes.yaml`` examples shipped with GitPilot.

_BLOCK_SCALAR_RE = re.compile(r"^(?P<key>[^:#\s][^:]*):\s*(?P<style>[|>][-+]?)\s*$")
_KEY_VAL_RE = re.compile(r"^(?P<key>[^:#\s][^:]*?):\s*(?P<value>.*)$")
_LIST_ITEM_RE = re.compile(r"^- ?(?P<rest>.*)$")


def _tiny_yaml(text: str) -> Dict[str, Any]:
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
                result[key] = _read_block_scalar(cur_indent + 1, block.group("style"))
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
                result[key] = _scalar(value)
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
                    item[key] = _scalar(value)
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
                result.append(_scalar(rest))
        return result

    def _read_block_scalar(indent: int, style: str) -> str:
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


def _scalar(raw: str) -> Any:
    s = raw.strip()
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_scalar(x) for x in _split_flow(inner)]
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        if not inner:
            return {}
        out: Dict[str, Any] = {}
        for piece in _split_flow(inner):
            if ":" in piece:
                k, v = piece.split(":", 1)
                out[k.strip()] = _scalar(v)
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


def _split_flow(text: str) -> List[str]:
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
