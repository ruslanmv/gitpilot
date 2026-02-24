# gitpilot/context_pack.py
"""Context Pack — compose a bounded, token-safe context injection for agents.

Non-destructive, additive feature.  If no context assets or use cases exist
the pack is empty and agents behave exactly as before.

Usage in agentic.py / agent builders:
    from .context_pack import build_context_pack
    pack = build_context_pack(workspace_path, query=goal)
    # Prepend ``pack`` to agent backstory or system prompt.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limits (keep total injection well under 8 K chars to avoid token blowups)
# ---------------------------------------------------------------------------
MAX_CONVENTIONS_CHARS = 2_000
MAX_USE_CASE_CHARS = 2_000
MAX_CHUNKS_CHARS = 3_000
MAX_CHUNKS = 8


def build_context_pack(
    workspace_path: Path,
    query: str = "",
    *,
    include_conventions: bool = True,
    include_use_case: bool = True,
    include_assets: bool = True,
    max_total_chars: int = 7_000,
) -> str:
    """Build a markdown context pack for agent prompt injection.

    Returns an empty string when nothing is available (zero overhead).
    """
    parts: list[str] = []
    total = 0

    # 1) Conventions / Rules (existing MemoryManager)
    if include_conventions:
        section = _conventions_section(workspace_path)
        if section and total + len(section) <= max_total_chars:
            parts.append(section)
            total += len(section)

    # 2) Active Use Case
    if include_use_case:
        section = _use_case_section(workspace_path)
        if section and total + len(section) <= max_total_chars:
            parts.append(section)
            total += len(section)

    # 3) Relevant context chunks from uploaded assets
    if include_assets and query:
        remaining = max_total_chars - total
        section = _assets_section(workspace_path, query, max_chars=min(remaining, MAX_CHUNKS_CHARS))
        if section:
            parts.append(section)

    if not parts:
        return ""

    return "## Project Context Pack (auto)\n\n" + "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------
def _conventions_section(workspace_path: Path) -> str:
    try:
        from .memory import MemoryManager

        mgr = MemoryManager(workspace_path)
        ctx = mgr.load_context()
        prompt = ctx.to_system_prompt()
        if not prompt:
            return ""
        return "### Conventions\n\n" + prompt[:MAX_CONVENTIONS_CHARS]
    except Exception:
        logger.debug("Could not load conventions for context pack", exc_info=True)
        return ""


def _use_case_section(workspace_path: Path) -> str:
    try:
        from .use_case import UseCaseManager

        mgr = UseCaseManager(workspace_path)
        uc = mgr.get_active_use_case()
        if not uc:
            return ""

        spec = uc.spec
        lines = ["### Active Use Case"]
        if spec.title:
            lines.append(f"- **Title:** {spec.title}")
        if spec.summary:
            lines.append(f"- **Summary:** {spec.summary}")
        if spec.problem:
            lines.append(f"- **Problem:** {spec.problem}")
        if spec.users:
            lines.append(f"- **Users:** {spec.users}")
        if spec.requirements:
            lines.append("- **Requirements:**")
            for r in spec.requirements[:10]:
                lines.append(f"  - {r}")
        if spec.acceptance_criteria:
            lines.append("- **Acceptance Criteria:**")
            for ac in spec.acceptance_criteria[:10]:
                lines.append(f"  - {ac}")
        if spec.constraints:
            lines.append("- **Constraints:**")
            for c in spec.constraints[:5]:
                lines.append(f"  - {c}")

        result = "\n".join(lines)
        return result[:MAX_USE_CASE_CHARS]
    except Exception:
        logger.debug("Could not load active use case for context pack", exc_info=True)
        return ""


def _assets_section(
    workspace_path: Path,
    query: str,
    max_chars: int = MAX_CHUNKS_CHARS,
) -> str:
    try:
        from .context_vault import ContextVault

        vault = ContextVault(workspace_path)
        chunks = vault.search_chunks(query, max_chunks=MAX_CHUNKS, max_chars=max_chars)
        if not chunks:
            return ""

        lines = [f"### Relevant References (Top {len(chunks)})"]
        for chunk in chunks:
            lines.append(
                f"[Asset: {chunk.filename} | chunk {chunk.chunk_index}]\n{chunk.text}"
            )

        return "\n\n".join(lines)[:max_chars]
    except Exception:
        logger.debug("Could not search context vault for context pack", exc_info=True)
        return ""
