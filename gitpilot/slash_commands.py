# gitpilot/slash_commands.py
"""Custom slash commands as markdown files.

Discovery::

    .gitpilot/commands/<name>.md   — project commands
    ~/.gitpilot/commands/<name>.md — user-global commands

Each file may carry simple YAML front-matter::

    ---
    description: Create a new API endpoint
    argument-hint: <endpoint-name> <http-method>
    ---

    Create a new API endpoint called $1 that handles $2 requests.

Args are positional ``$1 .. $9``; ``$ARGS`` expands to the full
argument string.  Unknown placeholders are left intact so commands stay
predictable.

This module is pure-Python and has no dependency on chat plumbing — the
session layer feeds it the user message and renders the result back
into the chat.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .skills import _parse_yaml_simple  # reuse front-matter parser

logger = logging.getLogger(__name__)

COMMANDS_DIR = "commands"
USER_COMMANDS = Path.home() / ".gitpilot" / COMMANDS_DIR
PROJECT_COMMANDS_REL = Path(".gitpilot") / COMMANDS_DIR

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_INVOCATION_RE = re.compile(r"^\s*/([a-z0-9][a-z0-9_-]*)(?:\s+(.*))?$", re.IGNORECASE)
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class SlashCommand:
    """A loaded slash command."""

    name: str
    description: str = ""
    argument_hint: str = ""
    template: str = ""
    source: str = ""  # "project" | "user"
    source_file: Optional[Path] = None

    @classmethod
    def from_file(cls, path: Path, *, source: str) -> "SlashCommand":
        text = path.read_text(encoding="utf-8")
        meta: Dict[str, object] = {}
        body = text
        m = _FRONT_MATTER_RE.match(text)
        if m:
            meta = _parse_yaml_simple(m.group(1))
            body = text[m.end():]
        return cls(
            name=_normalise_name(path.stem),
            description=str(meta.get("description", "")),
            argument_hint=str(meta.get("argument-hint", "") or meta.get("argument_hint", "")),
            template=body.strip(),
            source=source,
            source_file=path,
        )

    def render(self, args: List[str]) -> str:
        rendered = self.template
        for i in range(1, 10):
            token = f"${i}"
            value = args[i - 1] if i - 1 < len(args) else ""
            rendered = rendered.replace(token, value)
        rendered = rendered.replace("$ARGS", " ".join(args))
        return rendered

    def to_dict(self) -> Dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "argument_hint": self.argument_hint,
            "source": self.source,
        }


class SlashCommandRegistry:
    """Discover and resolve slash commands."""

    def __init__(self) -> None:
        self._commands: Dict[str, SlashCommand] = {}

    def load(self, workspace_path: Optional[Path] = None) -> int:
        count = 0
        count += self._load_dir(USER_COMMANDS, source="user")
        if workspace_path is not None:
            count += self._load_dir(workspace_path / PROJECT_COMMANDS_REL, source="project")
        return count

    def _load_dir(self, directory: Path, *, source: str) -> int:
        if not directory.is_dir():
            return 0
        count = 0
        for path in sorted(directory.glob("*.md")):
            try:
                cmd = SlashCommand.from_file(path, source=source)
            except Exception as e:
                logger.warning("failed to load slash command %s: %s", path, e)
                continue
            self._commands[cmd.name] = cmd
            count += 1
        return count

    def register(self, cmd: SlashCommand) -> None:
        self._commands[cmd.name] = cmd

    def get(self, name: str) -> Optional[SlashCommand]:
        return self._commands.get(_normalise_name(name))

    def listing(self) -> List[Dict[str, str]]:
        return [c.to_dict() for c in self._commands.values()]

    # ------------------------------------------------------------------
    # Invocation helper
    # ------------------------------------------------------------------
    def parse_invocation(self, message: str) -> Optional[Tuple[SlashCommand, List[str]]]:
        """Return ``(cmd, args)`` if ``message`` starts with a known slash."""
        if not message or not message.lstrip().startswith("/"):
            return None
        m = _INVOCATION_RE.match(message.strip())
        if not m:
            return None
        name = _normalise_name(m.group(1))
        cmd = self._commands.get(name)
        if not cmd:
            return None
        args = _shlex(m.group(2) or "")
        return cmd, args


def _normalise_name(raw: str) -> str:
    name = raw.lower().strip()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-z0-9_-]", "", name)
    name = name.strip("-")
    return name


def _shlex(text: str) -> List[str]:
    """Tiny shlex — splits on whitespace, respects double quotes."""
    out: List[str] = []
    buf: List[str] = []
    in_quote = False
    for ch in text:
        if ch == '"':
            in_quote = not in_quote
            continue
        if ch.isspace() and not in_quote:
            if buf:
                out.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out
