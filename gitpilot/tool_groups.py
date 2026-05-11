# gitpilot/tool_groups.py
"""Tool category model + per-mode tool policy enforcement.

Pure data layer — does not call tools.  Other modules (executor,
permissions, modes) consult :class:`ToolPolicy` to decide whether a tool
invocation is allowed for the active mode.

Categories mirror the surface a developer cares about::

    read     — read files / list directories / search
    edit     — write / patch / delete files
    command  — execute shell commands
    browser  — drive a browser / fetch the web
    mcp      — call a tool exposed by a connected MCP server
    mode     — switch the active mode (meta-tool)

The policy is intentionally additive: an empty policy allows everything
(legacy behaviour); a populated policy restricts.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional, Set


class ToolCategory(str, Enum):
    """High-level category an executor uses to decide whether a tool
    invocation is permitted by the active :class:`ToolPolicy`."""

    READ = "read"
    EDIT = "edit"
    COMMAND = "command"
    BROWSER = "browser"
    MCP = "mcp"
    MODE = "mode"


# Default classification for built-in GitPilot tool names.  Names not
# listed default to READ — the safest unknown bucket.
_BUILTIN_CATEGORIES: Dict[str, ToolCategory] = {
    # read
    "read_local_file": ToolCategory.READ,
    "list_local_files": ToolCategory.READ,
    "search_local_files": ToolCategory.READ,
    "github_search_code": ToolCategory.READ,
    "github_search_issues": ToolCategory.READ,
    "git_diff": ToolCategory.READ,
    "git_log": ToolCategory.READ,
    "read_file": ToolCategory.READ,
    "list_files": ToolCategory.READ,
    # edit
    "write_local_file": ToolCategory.EDIT,
    "edit_local_file": ToolCategory.EDIT,
    "delete_local_file": ToolCategory.EDIT,
    "apply_patch": ToolCategory.EDIT,
    "write_file": ToolCategory.EDIT,
    "apply_diff": ToolCategory.EDIT,
    "insert_content": ToolCategory.EDIT,
    # command
    "run_command": ToolCategory.COMMAND,
    "execute_command": ToolCategory.COMMAND,
    "run_tests": ToolCategory.COMMAND,
    # browser / fetch
    "fetch_url": ToolCategory.BROWSER,
    "web_search": ToolCategory.BROWSER,
    # mode meta
    "switch_mode": ToolCategory.MODE,
}


def classify(tool_name: str) -> ToolCategory:
    """Return the category for a tool name, defaulting to READ."""
    if tool_name in _BUILTIN_CATEGORIES:
        return _BUILTIN_CATEGORIES[tool_name]
    # MCP tools are typically namespaced "server.tool"
    if "." in tool_name or tool_name.startswith("mcp__"):
        return ToolCategory.MCP
    return ToolCategory.READ


def register_category(tool_name: str, category: ToolCategory) -> None:
    """Plugin hook: register the category for a custom tool."""
    _BUILTIN_CATEGORIES[tool_name] = category


# ----------------------------------------------------------------------
# Policy
# ----------------------------------------------------------------------

@dataclass
class EditGuard:
    """File-path constraint applied to EDIT-category tools."""

    file_regex: Optional[str] = None

    def matches(self, path: str) -> bool:
        if not self.file_regex:
            return True
        try:
            return re.search(self.file_regex, path) is not None
        except re.error:
            return False


@dataclass
class MCPGuard:
    """Per-MCP-server tool toggles.

    Tool identifiers use the convention ``"<server>.<tool>"``.  ``allow``
    is an allowlist applied first; ``deny`` is then applied; ``always``
    marks tools that may run without user approval.
    """

    allow: Set[str] = field(default_factory=set)
    deny: Set[str] = field(default_factory=set)
    always: Set[str] = field(default_factory=set)
    disabled_servers: Set[str] = field(default_factory=set)

    def is_enabled(self, qualified_name: str) -> bool:
        server = qualified_name.split(".", 1)[0]
        if server in self.disabled_servers:
            return False
        if qualified_name in self.deny:
            return False
        if not self.allow:
            return True
        return _glob_in_set(qualified_name, self.allow)

    def is_always_allowed(self, qualified_name: str) -> bool:
        return _glob_in_set(qualified_name, self.always)


def _glob_in_set(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


@dataclass
class ToolPolicy:
    """Composable policy applied per session/mode.

    An empty policy (no categories listed) means *anything goes* — keeps
    legacy behaviour for callers that don't opt in.
    """

    enabled_categories: Set[ToolCategory] = field(default_factory=set)
    edit_guard: EditGuard = field(default_factory=EditGuard)
    mcp_guard: MCPGuard = field(default_factory=MCPGuard)
    extra_deny: Set[str] = field(default_factory=set)

    @property
    def restrictive(self) -> bool:
        return bool(self.enabled_categories)

    def allow_tool(self, tool_name: str, *, target_path: Optional[str] = None) -> bool:
        """Return True if the tool may run under this policy."""
        if tool_name in self.extra_deny:
            return False
        category = classify(tool_name)
        if self.restrictive and category not in self.enabled_categories:
            return False
        if category is ToolCategory.EDIT and target_path:
            if not self.edit_guard.matches(target_path):
                return False
        if category is ToolCategory.MCP:
            if not self.mcp_guard.is_enabled(tool_name):
                return False
        return True

    def always_allowed(self, tool_name: str) -> bool:
        if classify(tool_name) is ToolCategory.MCP:
            return self.mcp_guard.is_always_allowed(tool_name)
        return False

    @classmethod
    def permissive(cls) -> "ToolPolicy":
        """A non-restrictive policy — legacy default."""
        return cls()

    @classmethod
    def from_mode_groups(cls, groups: List[object]) -> "ToolPolicy":
        """Build a policy from a YAML-shape ``groups:`` list.

        Each entry may be a plain string (``"read"``) or a single-key
        mapping (``{"edit": {"fileRegex": "^src/.+"}}``).
        """
        policy = cls()
        for entry in groups:
            if isinstance(entry, str):
                try:
                    policy.enabled_categories.add(ToolCategory(entry))
                except ValueError:
                    continue
            elif isinstance(entry, dict) and len(entry) == 1:
                key, conf = next(iter(entry.items()))
                try:
                    category = ToolCategory(key)
                except ValueError:
                    continue
                policy.enabled_categories.add(category)
                if category is ToolCategory.EDIT and isinstance(conf, dict):
                    policy.edit_guard = EditGuard(file_regex=conf.get("fileRegex"))
                elif category is ToolCategory.MCP and isinstance(conf, dict):
                    policy.mcp_guard = MCPGuard(
                        allow=set(conf.get("allow", [])),
                        deny=set(conf.get("deny", [])),
                        always=set(conf.get("alwaysAllow", [])),
                        disabled_servers=set(conf.get("disabledServers", [])),
                    )
        return policy
