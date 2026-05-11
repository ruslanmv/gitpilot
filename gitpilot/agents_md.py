# gitpilot/agents_md.py
"""Persistent project context file — ``AGENTS.md`` + ``/init``.

Industry-convention `AGENTS.md` lives at the workspace root and is loaded
into every session as a high-priority context block.  This module is
purely additive — when no ``AGENTS.md`` exists the rest of GitPilot
behaves exactly as before.

Three responsibilities:

1.  Render a starter ``AGENTS.md`` from a workspace scan (``/init``).
2.  Load the active ``AGENTS.md`` and its mode-specific siblings under
    ``.gitpilot/AGENTS.<mode>.md`` for prompt injection.
3.  Expand inline ``@./other.md`` includes with circular-import detection.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, TypedDict


class _IncludeInfo(TypedDict):
    remaining_budget: int
    include_count: int
    truncated: bool

logger = logging.getLogger(__name__)

AGENTS_MD = "AGENTS.md"
GITPILOT_DIR = ".gitpilot"

MAX_AGENTS_MD_BYTES = 32_000
MAX_INCLUDE_DEPTH = 5
MAX_INCLUDES_TOTAL = 32

_INCLUDE_RE = re.compile(r"^@(\./|\.\./|/)([^\s]+)\s*$", re.MULTILINE)


@dataclass
class AgentsDoc:
    """Loaded AGENTS.md with includes resolved."""

    path: Path
    content: str
    includes: List[Path] = field(default_factory=list)
    truncated: bool = False
    circular: List[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.content.strip()


class AgentsLoader:
    """Locate and load AGENTS.md (root + optional mode-specific)."""

    def __init__(self, workspace_path: Path) -> None:
        self.workspace_path = workspace_path.resolve()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def root_path(self) -> Path:
        return self.workspace_path / AGENTS_MD

    def mode_path(self, mode_slug: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "", mode_slug)
        return self.workspace_path / GITPILOT_DIR / f"AGENTS.{safe}.md"

    # ------------------------------------------------------------------
    # Loading + include expansion
    # ------------------------------------------------------------------
    def load(self, mode_slug: Optional[str] = None) -> AgentsDoc:
        candidates: List[Path] = []
        if mode_slug:
            mp = self.mode_path(mode_slug)
            if mp.exists():
                candidates.append(mp)
        root = self.root_path()
        if root.exists():
            candidates.append(root)

        if not candidates:
            return AgentsDoc(path=root, content="")

        # If both exist, mode-specific is appended after the root so the
        # mode overrides apply last in the system prompt.
        rendered_parts: List[str] = []
        seen: Set[Path] = set()
        circular: List[str] = []
        includes: List[Path] = []
        truncated = False
        budget = MAX_AGENTS_MD_BYTES
        include_count = 0

        for cand in reversed(candidates):  # root first, mode last
            text, info = self._expand_includes(
                cand, depth=0, seen=seen, circular=circular, includes=includes,
                remaining_budget=budget, include_count=include_count,
            )
            rendered_parts.append(text)
            budget = info["remaining_budget"]
            include_count = info["include_count"]
            truncated = truncated or info["truncated"]
            if budget <= 0:
                truncated = True
                break

        return AgentsDoc(
            path=candidates[0],
            content="\n\n".join(p for p in rendered_parts if p),
            includes=includes,
            truncated=truncated,
            circular=circular,
        )

    def _expand_includes(
        self,
        path: Path,
        *,
        depth: int,
        seen: Set[Path],
        circular: List[str],
        includes: List[Path],
        remaining_budget: int,
        include_count: int,
    ) -> Tuple[str, _IncludeInfo]:
        resolved = path.resolve()
        if resolved in seen:
            circular.append(str(resolved))
            return "", {"remaining_budget": remaining_budget, "include_count": include_count, "truncated": False}
        if depth > MAX_INCLUDE_DEPTH or include_count >= MAX_INCLUDES_TOTAL:
            return "", {"remaining_budget": remaining_budget, "include_count": include_count, "truncated": True}

        if not str(resolved).startswith(str(self.workspace_path)):
            return "", {"remaining_budget": remaining_budget, "include_count": include_count, "truncated": False}

        seen.add(resolved)
        try:
            raw = resolved.read_text(encoding="utf-8")
        except Exception as e:
            logger.debug("could not read %s: %s", resolved, e)
            return "", {"remaining_budget": remaining_budget, "include_count": include_count, "truncated": False}

        out_parts: List[str] = []
        truncated = False
        cursor = 0
        for m in _INCLUDE_RE.finditer(raw):
            out_parts.append(raw[cursor : m.start()])
            cursor = m.end()
            include_token = m.group(1) + m.group(2)
            target = (resolved.parent / include_token).resolve() if not include_token.startswith("/") else Path(include_token).resolve()
            includes.append(target)
            include_count += 1
            child_text, child_info = self._expand_includes(
                target,
                depth=depth + 1,
                seen=seen,
                circular=circular,
                includes=includes,
                remaining_budget=remaining_budget,
                include_count=include_count,
            )
            out_parts.append(child_text)
            remaining_budget = child_info["remaining_budget"]
            include_count = child_info["include_count"]
            truncated = truncated or child_info["truncated"]
        out_parts.append(raw[cursor:])

        body = "".join(out_parts)
        if len(body) > remaining_budget:
            body = body[:remaining_budget]
            truncated = True
        remaining_budget -= len(body)

        return body, {"remaining_budget": remaining_budget, "include_count": include_count, "truncated": truncated}


# ----------------------------------------------------------------------
# /init implementation
# ----------------------------------------------------------------------

@dataclass
class InitReport:
    """Summary returned by ``/init``."""

    created: bool
    path: Path
    sections: List[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None


def _scan_workspace(workspace_path: Path) -> Dict[str, Any]:
    """Extract a low-cost fingerprint of the project for the starter doc."""
    info: Dict[str, Any] = {}
    info["python"] = (workspace_path / "pyproject.toml").exists() or any(workspace_path.glob("*.py"))
    info["node"] = (workspace_path / "package.json").exists()
    info["docker"] = (workspace_path / "Dockerfile").exists() or any(workspace_path.glob("Dockerfile*"))
    info["compose"] = any(workspace_path.glob("docker-compose*.y*ml"))
    info["has_tests"] = (workspace_path / "tests").exists() or (workspace_path / "test").exists()
    info["has_makefile"] = (workspace_path / "Makefile").exists()
    info["readme"] = next((p.name for p in workspace_path.glob("README*")), None)
    # Cheap top-level layout
    top: List[str] = []
    for child in sorted(workspace_path.iterdir()):
        if child.name.startswith("."):
            continue
        top.append(child.name + ("/" if child.is_dir() else ""))
        if len(top) >= 30:
            break
    info["top_level"] = top
    return info


_STARTER_TEMPLATE = """# AGENTS.md

> Persistent project context loaded into every GitPilot session.
> Edit freely — agents will follow these notes.

## Project Overview
{overview}

## Directory Layout
{layout}

## Stack
{stack}

## Workflows
{workflows}

## Conventions
- Keep changes small and reversible.
- Run the test suite before committing.
- Write docstrings for any new public function.

## Mode-Specific Notes
Place per-mode overrides in `.gitpilot/AGENTS.<mode>.md` (for example
`.gitpilot/AGENTS.coder.md`).  Use `@./relative/path.md` on its own line to
include another markdown file.
"""


def run_init(
    workspace_path: Path,
    *,
    overwrite: bool = False,
) -> InitReport:
    """Generate a starter ``AGENTS.md`` for the workspace.  Idempotent."""
    workspace_path = workspace_path.resolve()
    target = workspace_path / AGENTS_MD
    if target.exists() and not overwrite:
        return InitReport(created=False, path=target, skipped_reason="exists")

    info = _scan_workspace(workspace_path)

    stack_bits: List[str] = []
    if info.get("python"):
        stack_bits.append("Python")
    if info.get("node"):
        stack_bits.append("Node.js")
    if info.get("docker"):
        stack_bits.append("Docker")
    if info.get("compose"):
        stack_bits.append("docker-compose")
    stack = ", ".join(stack_bits) or "_unknown — describe here_"

    workflows: List[str] = []
    if info.get("has_makefile"):
        workflows.append("- `make install`, `make test`, `make run`")
    if info.get("node"):
        workflows.append("- `npm install`, `npm test`")
    if info.get("python"):
        workflows.append("- `pip install -e .` and `pytest`")
    workflows_md = "\n".join(workflows) or "_describe build/test/run commands here_"

    layout = "\n".join(f"- `{e}`" for e in info.get("top_level", [])) or "_workspace empty_"
    overview = (
        f"This project has a `{info.get('readme')}` at its root — refer to it for "
        "purpose and high-level usage."
        if info.get("readme") else "_describe the project here_"
    )

    doc = _STARTER_TEMPLATE.format(
        overview=overview,
        layout=layout,
        stack=stack,
        workflows=workflows_md,
    )

    target.write_text(doc, encoding="utf-8")
    return InitReport(
        created=True,
        path=target,
        sections=["Project Overview", "Directory Layout", "Stack", "Workflows", "Conventions"],
    )


def load_for_session(
    workspace_path: Path,
    mode_slug: Optional[str] = None,
) -> str:
    """Convenience: return the AGENTS.md content (with includes) or ''."""
    doc = AgentsLoader(workspace_path).load(mode_slug=mode_slug)
    if doc.is_empty:
        return ""
    suffix = ""
    if doc.truncated:
        suffix = "\n\n_…AGENTS.md truncated to fit context budget._"
    return doc.content + suffix
