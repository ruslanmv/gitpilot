# gitpilot/skills.py
"""Skill system for GitPilot — reusable, invocable workflows.

Skills are markdown files that define prompt templates.  Users invoke
them via ``/skill-name`` in chat.  They live in:

- ``.gitpilot/skills/*.md``     — project-level skills
- ``~/.gitpilot/skills/*.md``   — global user skills
- Plugin skills (discovered via PluginManager)

Each markdown file has YAML front-matter followed by a prompt template::

    ---
    name: review
    description: Review code quality for the current branch
    auto_trigger: false
    required_tools:
      - git_diff
      - read_local_file
    ---

    Review the code changes on the current branch.
    Focus on: security issues, performance, and maintainability.
    Use git_diff to see what changed, then read relevant files.
    Provide a structured review with severity ratings.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SKILLS_DIR_NAME = "skills"
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Skill:
    """A reusable, invocable workflow template."""

    name: str
    description: str = ""
    prompt_template: str = ""
    auto_trigger: bool = False
    required_tools: List[str] = field(default_factory=list)
    source_file: Optional[Path] = None

    @classmethod
    def from_file(cls, path: Path) -> "Skill":
        """Parse a skill from a markdown file with YAML front-matter."""
        text = path.read_text(encoding="utf-8")
        meta: Dict[str, Any] = {}
        prompt = text

        m = _FRONT_MATTER_RE.match(text)
        if m:
            meta = _parse_yaml_simple(m.group(1))
            prompt = text[m.end():]

        return cls(
            name=meta.get("name", path.stem),
            description=meta.get("description", ""),
            prompt_template=prompt.strip(),
            auto_trigger=meta.get("auto_trigger", False),
            required_tools=meta.get("required_tools", []),
            source_file=path,
        )

    def render(self, context: Optional[Dict[str, str]] = None) -> str:
        """Render the prompt template with optional variable substitution.

        Variables use ``{{var_name}}`` syntax.
        """
        result = self.prompt_template
        if context:
            for key, value in context.items():
                result = result.replace("{{" + key + "}}", str(value))
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "auto_trigger": self.auto_trigger,
            "required_tools": self.required_tools,
            "source": str(self.source_file) if self.source_file else None,
        }


class SkillManager:
    """Discover and manage skills from project, user, and plugin sources."""

    def __init__(
        self,
        workspace_path: Optional[Path] = None,
        user_dir: Optional[Path] = None,
    ) -> None:
        self.workspace_path = workspace_path
        self.user_dir = user_dir or (Path.home() / ".gitpilot")
        self._skills: Dict[str, Skill] = {}

    def load_all(self) -> int:
        """Load skills from all sources.  Returns count of skills loaded."""
        count = 0

        # 1. Project-level skills
        if self.workspace_path:
            project_skills = self.workspace_path / ".gitpilot" / SKILLS_DIR_NAME
            count += self._load_from_dir(project_skills, prefix="project")

        # 2. Global user skills
        user_skills = self.user_dir / SKILLS_DIR_NAME
        count += self._load_from_dir(user_skills, prefix="user")

        logger.info("Loaded %d skills total", count)
        return count

    def register(self, skill: Skill) -> None:
        """Register a skill (e.g. from a plugin)."""
        self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[Skill]:
        """Get a skill by name (used for /command invocation)."""
        return self._skills.get(name)

    def list_skills(self) -> List[Dict[str, Any]]:
        """List all loaded skills."""
        return [s.to_dict() for s in self._skills.values()]

    def find_auto_triggers(self, context: str) -> List[Skill]:
        """Find skills that should auto-trigger based on context.

        Auto-trigger skills are checked against the context string
        (e.g. user message or session state) and returned if their
        name or description is relevant.
        """
        matches = []
        ctx_lower = context.lower()
        for skill in self._skills.values():
            if not skill.auto_trigger:
                continue
            # Simple keyword match for now
            if skill.name.lower() in ctx_lower:
                matches.append(skill)
            elif any(word in ctx_lower for word in skill.description.lower().split()):
                matches.append(skill)
        return matches

    def invoke(
        self,
        name: str,
        context: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """Invoke a skill by name and return the rendered prompt.

        Returns None if the skill is not found.
        """
        skill = self.get(name)
        if not skill:
            return None
        return skill.render(context)

    def _load_from_dir(self, skills_dir: Path, prefix: str = "") -> int:
        if not skills_dir.is_dir():
            return 0
        count = 0
        for md_file in sorted(skills_dir.glob("*.md")):
            try:
                skill = Skill.from_file(md_file)
                self._skills[skill.name] = skill
                count += 1
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", md_file, e)
        return count


def _parse_yaml_simple(text: str) -> Dict[str, Any]:
    """Minimal YAML front-matter parser (no external dependency).

    Handles:
      key: value
      key: true/false
      key:
        - item1
        - item2
    """
    result: Dict[str, Any] = {}
    lines = text.strip().split("\n")
    current_key: Optional[str] = None
    current_list: Optional[List[str]] = None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # List item
        if stripped.startswith("- ") and current_key:
            if current_list is None:
                current_list = []
            current_list.append(stripped[2:].strip())
            result[current_key] = current_list
            continue

        # Key-value
        if ":" in stripped:
            if current_list is not None:
                current_list = None

            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            current_key = key

            if not value:
                # Next lines might be a list
                current_list = []
                result[key] = current_list
                continue

            # Parse value types
            if value.lower() == "true":
                result[key] = True
            elif value.lower() == "false":
                result[key] = False
            elif value.isdigit():
                result[key] = int(value)
            else:
                result[key] = value

            current_list = None

    return result
