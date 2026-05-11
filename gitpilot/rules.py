# gitpilot/rules.py
"""Custom rule loading — global + workspace, with mode awareness.

Discovery (highest-priority last so workspace wins on conflict)::

    ~/.gitpilot/rules/*.md             — global rules
    ~/.gitpilot/rules-<mode>/*.md      — global, mode-specific
    <ws>/.gitpilotrules                — single workspace rules file
    <ws>/.gitpilotrules-<mode>         — single, mode-specific
    <ws>/.gitpilot/rules/*.md          — workspace rules directory
    <ws>/.gitpilot/rules-<mode>/*.md   — workspace, mode-specific

The :func:`compose_rules` helper returns a markdown block ready for
prompt injection.  Files exceeding the per-file cap are tail-trimmed to
keep newer instructions visible.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

USER_RULES_ROOT = Path.home() / ".gitpilot"
PROJECT_RULES_REL = Path(".gitpilot")

MAX_RULE_BYTES = 8_000
MAX_RULES_TOTAL_BYTES = 24_000

_SAFE_SLUG_RE = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")


@dataclass
class Rule:
    """A single rule fragment."""

    name: str
    body: str
    source: str  # "global" | "workspace"
    scope: str   # "all" | "<mode>"
    source_file: Optional[Path] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "scope": self.scope,
            "source_file": str(self.source_file) if self.source_file else None,
        }


@dataclass
class RuleSet:
    """Composable result returned by the loader."""

    rules: List[Rule] = field(default_factory=list)

    def to_markdown(self) -> str:
        if not self.rules:
            return ""
        parts: List[str] = ["## Custom rules"]
        total = 0
        for rule in self.rules:
            body = rule.body.strip()
            if not body:
                continue
            head = f"### {rule.name} ({rule.source}/{rule.scope})"
            block = f"{head}\n\n{body}"
            if total + len(block) > MAX_RULES_TOTAL_BYTES:
                parts.append("_…remaining rules truncated to fit context budget._")
                break
            parts.append(block)
            total += len(block)
        return "\n\n".join(parts)


def _safe_mode(mode_slug: Optional[str]) -> Optional[str]:
    if not mode_slug:
        return None
    return mode_slug if _SAFE_SLUG_RE.match(mode_slug) else None


def _read_capped(path: Path) -> str:
    try:
        data = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.debug("rules read failed %s: %s", path, e)
        return ""
    if len(data) > MAX_RULE_BYTES:
        return data[-MAX_RULE_BYTES:]
    return data


def _collect_dir(directory: Path, source: str, scope: str) -> List[Rule]:
    out: List[Rule] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.md")):
        body = _read_capped(path)
        if not body:
            continue
        out.append(Rule(name=path.stem, body=body, source=source, scope=scope, source_file=path))
    return out


def _collect_single(path: Path, source: str, scope: str) -> List[Rule]:
    if not path.exists():
        return []
    body = _read_capped(path)
    if not body:
        return []
    return [Rule(name=path.name, body=body, source=source, scope=scope, source_file=path)]


def load_rules(
    workspace_path: Optional[Path] = None,
    mode_slug: Optional[str] = None,
) -> RuleSet:
    """Discover rules from all sources, workspace overriding global."""
    safe_mode = _safe_mode(mode_slug)
    rules: List[Rule] = []
    # Global directories
    rules += _collect_dir(USER_RULES_ROOT / "rules", "global", "all")
    if safe_mode:
        rules += _collect_dir(USER_RULES_ROOT / f"rules-{safe_mode}", "global", safe_mode)
    if workspace_path is not None:
        # Single-file workspace rules (legacy-friendly)
        rules += _collect_single(workspace_path / ".gitpilotrules", "workspace", "all")
        if safe_mode:
            rules += _collect_single(workspace_path / f".gitpilotrules-{safe_mode}", "workspace", safe_mode)
        # Directory workspace rules
        rules += _collect_dir(workspace_path / PROJECT_RULES_REL / "rules", "workspace", "all")
        if safe_mode:
            rules += _collect_dir(workspace_path / PROJECT_RULES_REL / f"rules-{safe_mode}", "workspace", safe_mode)
    return RuleSet(rules=rules)


def compose_rules(
    workspace_path: Optional[Path] = None,
    mode_slug: Optional[str] = None,
) -> Tuple[str, RuleSet]:
    """Convenience: ``(markdown, ruleset)`` ready for injection."""
    ruleset = load_rules(workspace_path=workspace_path, mode_slug=mode_slug)
    return ruleset.to_markdown(), ruleset
