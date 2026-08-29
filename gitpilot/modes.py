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

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .tool_groups import ToolPolicy
from .yaml_lite import load_yaml_or_json, scalar, split_flow, tiny_yaml

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
# YAML loading
# ----------------------------------------------------------------------

def _expand_env(value: Any) -> str:
    if isinstance(value, str):
        return os.path.expandvars(value)
    return str(value)


#: The loader lives in :mod:`gitpilot.yaml_lite` since Batch V4-G1, when the
#: topology documents became its second consumer.  These aliases keep the names
#: this module has always exported.
_load_yaml_or_json = load_yaml_or_json
_tiny_yaml = tiny_yaml
_scalar = scalar
_split_flow = split_flow
