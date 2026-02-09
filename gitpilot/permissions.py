# gitpilot/permissions.py
"""Fine-grained permission system for tool execution.

Controls what agents can do based on configurable policies.
Three modes:

- **NORMAL** – ask before risky operations (default)
- **PLAN**   – read-only; all writes and shell commands blocked
- **AUTO**   – allow everything without confirmation

Permissions live in ``.gitpilot/permissions.json`` or are set via API.
"""
from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class PermissionMode(str, Enum):
    NORMAL = "normal"
    PLAN = "plan"
    AUTO = "auto"


class Action(str, Enum):
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    DELETE_FILE = "delete_file"
    RUN_COMMAND = "run_command"
    GIT_COMMIT = "git_commit"
    GIT_PUSH = "git_push"
    CREATE_ISSUE = "create_issue"
    CREATE_PR = "create_pr"
    MERGE_PR = "merge_pr"


RISKY_ACTIONS: frozenset[Action] = frozenset([
    Action.DELETE_FILE,
    Action.GIT_PUSH,
    Action.MERGE_PR,
    Action.RUN_COMMAND,
])

READ_ONLY_ACTIONS: frozenset[Action] = frozenset([
    Action.READ_FILE,
])


@dataclass
class PermissionPolicy:
    mode: PermissionMode = PermissionMode.NORMAL
    allowed_actions: Optional[Set[Action]] = None
    blocked_paths: List[str] = field(default_factory=lambda: [
        ".env", ".env.*", "*.pem", "*.key", "credentials*", "secrets*",
    ])
    allowed_commands: Optional[List[str]] = None
    require_confirmation: Set[Action] = field(
        default_factory=lambda: set(RISKY_ACTIONS),
    )


class PermissionManager:
    """Check and enforce permissions for agent actions."""

    def __init__(self, policy: Optional[PermissionPolicy] = None):
        self.policy = policy or PermissionPolicy()

    def check(
        self, action: Action, context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Return ``True`` if allowed, raise ``PermissionError`` if blocked."""
        if self.policy.mode == PermissionMode.PLAN:
            if action not in READ_ONLY_ACTIONS:
                raise PermissionError(
                    f"Action '{action.value}' blocked in plan mode (read-only)"
                )
            return True

        if context and "path" in context:
            self._check_path(context["path"])

        if self.policy.allowed_actions is not None:
            if action not in self.policy.allowed_actions:
                raise PermissionError(
                    f"Action '{action.value}' not in allowed actions"
                )

        return True

    def needs_confirmation(self, action: Action) -> bool:
        if self.policy.mode == PermissionMode.AUTO:
            return False
        if self.policy.mode == PermissionMode.PLAN:
            return False
        return action in self.policy.require_confirmation

    def _check_path(self, path: str):
        basename = path.split("/")[-1] if "/" in path else path
        for pattern in self.policy.blocked_paths:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(basename, pattern):
                raise PermissionError(
                    f"Access to '{path}' blocked by policy (pattern '{pattern}')"
                )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.policy.mode.value,
            "blocked_paths": self.policy.blocked_paths,
            "allowed_commands": self.policy.allowed_commands,
        }

    def load_from_file(self, path: Path):
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            if "mode" in data:
                self.policy.mode = PermissionMode(data["mode"])
            if "blocked_paths" in data:
                self.policy.blocked_paths = data["blocked_paths"]
            if "allowed_commands" in data:
                self.policy.allowed_commands = data["allowed_commands"]
        except Exception as e:
            logger.warning("Failed to load permissions: %s", e)
