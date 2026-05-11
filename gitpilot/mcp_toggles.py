# gitpilot/mcp_toggles.py
"""Per-server MCP tool toggles and ``alwaysAllow`` semantics.

Additive overlay on :mod:`gitpilot.mcp_client`.  The existing client is
left untouched; callers that want fine-grained control wrap their server
configs with :class:`MCPServerToggles` and ask :meth:`filter_tools` /
:meth:`is_always_allowed` before exposing a tool to the model.

Project file::

    .gitpilot/mcp.json

    {
      "servers": [
        {
          "name": "github",
          "transport": "stdio",
          "command": "uvx", "args": ["mcp-github"],
          "enabledTools": ["search_code", "list_issues"],
          "disabledTools": [],
          "alwaysAllow":  ["search_code"],
          "disabled":     false
        }
      ]
    }

User overrides at ``~/.gitpilot/mcp.json`` are merged underneath the
project file, with the project taking precedence on name conflicts.
"""
from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)

GLOBAL_MCP_PATH = Path.home() / ".gitpilot" / "mcp.json"
PROJECT_MCP_REL = Path(".gitpilot") / "mcp.json"


@dataclass
class MCPServerToggles:
    """Configurable visibility for an MCP server's tools."""

    name: str
    enabled_tools: Set[str] = field(default_factory=set)   # empty == all
    disabled_tools: Set[str] = field(default_factory=set)
    always_allow: Set[str] = field(default_factory=set)
    disabled: bool = False

    def is_tool_enabled(self, tool_name: str) -> bool:
        if self.disabled:
            return False
        if _glob_in_set(tool_name, self.disabled_tools):
            return False
        if not self.enabled_tools:
            return True
        return _glob_in_set(tool_name, self.enabled_tools)

    def is_always_allowed(self, tool_name: str) -> bool:
        return _glob_in_set(tool_name, self.always_allow)

    def filter_tools(self, tools: Iterable[Any]) -> List[Any]:
        """Filter a list of tool descriptors by name.

        Each ``tool`` must expose a ``.name`` attribute (the
        :class:`gitpilot.mcp_client.MCPTool` dataclass already does).
        """
        return [t for t in tools if self.is_tool_enabled(getattr(t, "name", ""))]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "enabledTools": sorted(self.enabled_tools),
            "disabledTools": sorted(self.disabled_tools),
            "alwaysAllow": sorted(self.always_allow),
            "disabled": self.disabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MCPServerToggles":
        return cls(
            name=data.get("name", ""),
            enabled_tools=set(data.get("enabledTools", [])),
            disabled_tools=set(data.get("disabledTools", [])),
            always_allow=set(data.get("alwaysAllow", [])),
            disabled=bool(data.get("disabled", False)),
        )


@dataclass
class MCPToggleRegistry:
    """Aggregate toggles loaded from global + project config files."""

    by_server: Dict[str, MCPServerToggles] = field(default_factory=dict)

    def get(self, server: str) -> MCPServerToggles:
        return self.by_server.get(server) or MCPServerToggles(name=server)

    def is_tool_enabled(self, server: str, tool: str) -> bool:
        return self.get(server).is_tool_enabled(tool)

    def is_always_allowed(self, server: str, tool: str) -> bool:
        return self.get(server).is_always_allowed(tool)

    def register(self, toggles: MCPServerToggles) -> None:
        self.by_server[toggles.name] = toggles

    @classmethod
    def load(cls, workspace_path: Optional[Path] = None) -> "MCPToggleRegistry":
        reg = cls()
        # Global first…
        reg._merge_from(GLOBAL_MCP_PATH)
        # …then project (overrides on name conflicts).
        if workspace_path is not None:
            reg._merge_from(workspace_path / PROJECT_MCP_REL)
        return reg

    def _merge_from(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("could not read %s: %s", path, e)
            return
        servers = data.get("servers", []) if isinstance(data, dict) else data
        if not isinstance(servers, list):
            return
        for entry in servers:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            toggles = MCPServerToggles.from_dict(entry)
            self.by_server[toggles.name] = toggles


# ----------------------------------------------------------------------
# Output validator (defends against context poisoning via tool replies)
# ----------------------------------------------------------------------

@dataclass
class ToolOutputCheck:
    """Result of a tool-output sanity check."""

    ok: bool
    reason: Optional[str] = None
    sanitised: Optional[str] = None


def validate_tool_output(
    raw: str,
    *,
    max_bytes: int = 256_000,
    forbid_control_chars: bool = True,
) -> ToolOutputCheck:
    """Validate the text a tool wants to inject into context history.

    The check is conservative: oversize outputs are truncated rather
    than rejected (truncation is recorded via ``sanitised``), but
    obviously contaminated payloads (NUL bytes, bell, etc.) are flagged
    so the caller can ask the user instead of poisoning the prompt.
    """
    if raw is None:
        return ToolOutputCheck(ok=True, sanitised="")
    text = str(raw)
    if forbid_control_chars:
        bad = [c for c in text if ord(c) < 0x09 or (0x0B <= ord(c) <= 0x1F and c not in "\r")]
        if bad:
            return ToolOutputCheck(ok=False, reason=f"control characters ({len(bad)})")
    if len(text.encode("utf-8", errors="replace")) > max_bytes:
        return ToolOutputCheck(
            ok=True,
            reason="truncated",
            sanitised=text[: max_bytes // 2] + "\n…\n[truncated]\n",
        )
    return ToolOutputCheck(ok=True)


def _glob_in_set(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)
