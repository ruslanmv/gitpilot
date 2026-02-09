# gitpilot/memory.py
"""Project context memory — the GITPILOT.md system.

Loads project-specific conventions, rules, and context from:

1. ``.gitpilot/GITPILOT.md``     — project root (committed to repo)
2. ``.gitpilot/rules/*.md``      — modular rule files
3. ``.gitpilot/memory.json``     — auto-learned patterns (local only)

The combined context is injected into agent system prompts so they
follow project conventions automatically.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

MEMORY_FILE = "GITPILOT.md"
RULES_DIR = "rules"
AUTO_MEMORY_FILE = "memory.json"
MAX_CONVENTIONS_CHARS = 10_000
MAX_RULE_CHARS = 5_000
MAX_PATTERNS = 100


@dataclass
class ProjectContext:
    """Combined project context for agent injection."""

    conventions: str = ""
    rules: List[str] = field(default_factory=list)
    auto_memory: Dict[str, Any] = field(default_factory=dict)

    def to_system_prompt(self) -> str:
        """Format as a system-prompt section to prepend to agent backstory."""
        parts: List[str] = []
        if self.conventions:
            parts.append(f"## Project Conventions\n\n{self.conventions}")
        if self.rules:
            parts.append("## Project Rules\n\n" + "\n\n---\n\n".join(self.rules))
        patterns = self.auto_memory.get("patterns", [])
        if patterns:
            parts.append(
                "## Learned Patterns\n\n"
                + "\n".join(f"- {p}" for p in patterns)
            )
        return "\n\n".join(parts)

    @property
    def is_empty(self) -> bool:
        return not self.conventions and not self.rules and not self.auto_memory


class MemoryManager:
    """Load and manage project-level context and conventions."""

    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path
        self.gitpilot_dir = workspace_path / ".gitpilot"

    def load_context(self) -> ProjectContext:
        ctx = ProjectContext()

        # 1. GITPILOT.md
        md_path = self.gitpilot_dir / MEMORY_FILE
        if md_path.exists():
            ctx.conventions = md_path.read_text(encoding="utf-8")[
                :MAX_CONVENTIONS_CHARS
            ]

        # 2. rules/*.md
        rules_dir = self.gitpilot_dir / RULES_DIR
        if rules_dir.is_dir():
            for rule_file in sorted(rules_dir.glob("*.md")):
                content = rule_file.read_text(encoding="utf-8")[:MAX_RULE_CHARS]
                ctx.rules.append(f"### {rule_file.stem}\n\n{content}")

        # 3. auto-learned memory
        auto_path = self.gitpilot_dir / AUTO_MEMORY_FILE
        if auto_path.exists():
            try:
                ctx.auto_memory = json.loads(auto_path.read_text())
            except Exception:
                pass

        return ctx

    def save_auto_memory(self, memory: Dict[str, Any]):
        self.gitpilot_dir.mkdir(parents=True, exist_ok=True)
        auto_path = self.gitpilot_dir / AUTO_MEMORY_FILE
        auto_path.write_text(json.dumps(memory, indent=2))

    def add_learned_pattern(self, pattern: str):
        auto_path = self.gitpilot_dir / AUTO_MEMORY_FILE
        memory: Dict[str, Any] = {}
        if auto_path.exists():
            try:
                memory = json.loads(auto_path.read_text())
            except Exception:
                pass
        patterns = memory.setdefault("patterns", [])
        if pattern not in patterns:
            patterns.append(pattern)
            memory["patterns"] = patterns[-MAX_PATTERNS:]
            self.save_auto_memory(memory)

    def init_project(self) -> Path:
        """Create .gitpilot/ with template GITPILOT.md.  Returns path."""
        self.gitpilot_dir.mkdir(parents=True, exist_ok=True)
        md_path = self.gitpilot_dir / MEMORY_FILE
        if not md_path.exists():
            md_path.write_text(
                "# GitPilot Project Conventions\n\n"
                "<!-- Add your project conventions here. -->\n"
                "<!-- GitPilot agents will follow these automatically. -->\n\n"
                "## Code Style\n\n\n"
                "## Testing\n\n\n"
                "## Commit Messages\n\n\n"
            )
        (self.gitpilot_dir / RULES_DIR).mkdir(exist_ok=True)
        return md_path

    def get_conventions_text(self) -> str:
        md_path = self.gitpilot_dir / MEMORY_FILE
        if md_path.exists():
            return md_path.read_text(encoding="utf-8")
        return ""

    def set_conventions_text(self, text: str):
        self.gitpilot_dir.mkdir(parents=True, exist_ok=True)
        md_path = self.gitpilot_dir / MEMORY_FILE
        md_path.write_text(text, encoding="utf-8")
