# gitpilot/prompt_cache.py
"""Provider-aware system-prompt block builder with Anthropic prompt caching.

Batch P2-A — additive, flag-gated.  The legacy code path keeps emitting
a flat ``system`` string; this module produces a *structured* sequence
of blocks that carries the same content plus ``cache_control: ephemeral``
markers on the stable prefix (``AGENTS.md`` + rules + tool defs).

Why
---
Anthropic's prompt-cache API rebates ~90 % of the input-token cost on
cache hits (re-using the same system prefix across turns) and trims
time-to-first-byte significantly.  By segmenting the system payload
into ``[stable prefix → mode notes → session conventions]`` we cache
the part that almost never changes, while the cheap-to-rebuild tail
stays uncached.

What this module *does not* do
------------------------------
* It does not call any provider SDK — it returns plain dictionaries
  that the caller can hand to ``anthropic.messages.create(system=…)``,
  CrewAI's ``LLM(system=…)``, or any other transport.
* It does not depend on FastAPI, CrewAI, or Anthropic at import time.
* It does not change behaviour unless the ``prompt_cache`` feature
  flag is on; the rendered shape includes a flat ``text`` rendering
  for legacy consumers.

Cache busting
-------------
The stable prefix is keyed by:

* the SHA-256 of ``AGENTS.md`` (with includes resolved)
* the SHA-256 of rule files (sorted by path)
* a canonicalised JSON dump of the tool descriptors

When any of those inputs change the cache key changes, so providers
that key on prefix content (Anthropic) will treat the next call as a
miss — exactly what we want.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import flags

logger = logging.getLogger(__name__)

FLAG_PROMPT_CACHE = "prompt_cache"


class Provider(str, Enum):
    """Providers we recognise for cache-marker emission."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    WATSONX = "watsonx"
    OLLAMA = "ollama"
    OTHER = "other"

    @classmethod
    def from_string(cls, value: Optional[str]) -> "Provider":
        if not value:
            return cls.OTHER
        s = value.lower()
        if "anthropic" in s or s.startswith("claude"):
            return cls.ANTHROPIC
        if "openai" in s or s.startswith("gpt"):
            return cls.OPENAI
        if "watsonx" in s or "ibm" in s:
            return cls.WATSONX
        if "ollama" in s:
            return cls.OLLAMA
        return cls.OTHER


# ----------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class SystemBlock:
    """One segment of the system prompt.

    ``cacheable=True`` segments are emitted with provider-specific cache
    markers when the flag is on; cacheable segments are always rendered
    first so the cache prefix matches across turns.
    """

    text: str
    label: str = ""
    cacheable: bool = False
    kind: str = "text"  # "text" | "tool_defs"


@dataclass
class SystemPayload:
    """Result of building system blocks for a turn."""

    provider: Provider
    blocks: List[SystemBlock]
    cache_prefix_digest: str
    cache_hits_expected: bool

    # ----- helpers --------------------------------------------------
    def to_flat_text(self) -> str:
        """Render as a single string — legacy callers stay happy."""
        return "\n\n".join(b.text for b in self.blocks if b.text)

    def to_anthropic_system(self) -> List[Dict[str, Any]]:
        """Render as Anthropic's structured ``system`` list."""
        out: List[Dict[str, Any]] = []
        for block in self.blocks:
            if not block.text:
                continue
            entry: Dict[str, Any] = {"type": "text", "text": block.text}
            if block.cacheable and self.cache_hits_expected:
                entry["cache_control"] = {"type": "ephemeral"}
            out.append(entry)
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider.value,
            "cache_prefix_digest": self.cache_prefix_digest,
            "cache_hits_expected": self.cache_hits_expected,
            "blocks": [
                {
                    "label": b.label,
                    "kind": b.kind,
                    "cacheable": b.cacheable,
                    "preview": b.text[:120],
                }
                for b in self.blocks
            ],
        }


# ----------------------------------------------------------------------
# Source loaders
# ----------------------------------------------------------------------

def _load_agents_md(workspace: Optional[Path], mode_slug: Optional[str]) -> str:
    if workspace is None:
        return ""
    try:
        from .agents_md import load_for_session  # local import
        return load_for_session(workspace, mode_slug=mode_slug)
    except Exception:
        logger.debug("could not load AGENTS.md for prompt cache", exc_info=True)
        return ""


def _load_rules(workspace: Optional[Path], mode_slug: Optional[str]) -> str:
    if workspace is None:
        return ""
    try:
        from .rules import compose_rules  # local import
        markdown, _ = compose_rules(workspace_path=workspace, mode_slug=mode_slug)
        return markdown
    except Exception:
        logger.debug("could not load rules for prompt cache", exc_info=True)
        return ""


def _render_tool_defs(tool_defs: Optional[Sequence[Mapping[str, Any]]]) -> str:
    if not tool_defs:
        return ""
    # Sort by tool name so the rendered block — and therefore the cache
    # prefix — is stable regardless of how callers happened to assemble
    # the list.  The model only sees the names + descriptions; order
    # carries no semantic meaning here.
    sorted_defs = sorted(tool_defs, key=lambda d: str(d.get("name", "")))
    canonical = json.dumps(sorted_defs, sort_keys=True, separators=(",", ":"))
    lines = ["## Available tools"]
    for entry in sorted_defs:
        name = entry.get("name", "?")
        desc = (entry.get("description") or "").strip().splitlines()[0:1]
        lines.append(f"- `{name}` — {desc[0] if desc else ''}")
    lines.append(f"\n<!-- tool-def-digest:{hashlib.sha256(canonical.encode()).hexdigest()[:16]} -->")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Builder
# ----------------------------------------------------------------------

def build_system_blocks(
    *,
    base_system: str = "",
    workspace: Optional[Path] = None,
    mode_slug: Optional[str] = None,
    tool_defs: Optional[Sequence[Mapping[str, Any]]] = None,
    session_conventions: str = "",
    provider: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> SystemPayload:
    """Compose the structured system payload for the next call.

    Stable prefix (cacheable when ``prompt_cache`` is on, in this order):
      1. ``base_system`` — caller-supplied core instructions
      2. ``AGENTS.md`` — persistent project context
      3. ``rules`` — workspace + global rules
      4. tool descriptors — rendered + digested

    Cheap tail (never cached, always re-rendered):
      5. ``session_conventions`` — anything that changes between turns

    ``enabled`` forces the flag value (used by tests); when ``None`` the
    global ``prompt_cache`` flag decides.
    """
    prov = Provider.from_string(provider)
    flag_on = enabled if enabled is not None else flags.is_on(FLAG_PROMPT_CACHE)
    cache_hits_expected = bool(flag_on and prov is Provider.ANTHROPIC)

    agents_md = _load_agents_md(workspace, mode_slug)
    rules_md = _load_rules(workspace, mode_slug)
    tool_defs_text = _render_tool_defs(tool_defs)

    blocks: List[SystemBlock] = []
    if base_system:
        blocks.append(SystemBlock(text=base_system.strip(), label="base", cacheable=True))
    if agents_md:
        blocks.append(SystemBlock(text=agents_md.strip(), label="agents_md", cacheable=True))
    if rules_md:
        blocks.append(SystemBlock(text=rules_md.strip(), label="rules", cacheable=True))
    if tool_defs_text:
        blocks.append(
            SystemBlock(text=tool_defs_text.strip(), label="tool_defs",
                        cacheable=True, kind="tool_defs")
        )
    if session_conventions:
        blocks.append(
            SystemBlock(text=session_conventions.strip(), label="session",
                        cacheable=False)
        )

    digest = _digest_cache_prefix(blocks)
    return SystemPayload(
        provider=prov,
        blocks=blocks,
        cache_prefix_digest=digest,
        cache_hits_expected=cache_hits_expected,
    )


def _digest_cache_prefix(blocks: Sequence[SystemBlock]) -> str:
    h = hashlib.sha256()
    for block in blocks:
        if not block.cacheable:
            continue
        h.update(block.label.encode("utf-8"))
        h.update(b"\0")
        h.update(block.text.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:32]


# ----------------------------------------------------------------------
# Adapter helpers — keep providers decoupled from the rest of GitPilot
# ----------------------------------------------------------------------

def to_legacy_system_string(payload: SystemPayload) -> str:
    """Flatten back to a single string for callers that can't yet handle
    structured system payloads (legacy code path)."""
    return payload.to_flat_text()


def to_anthropic_kwargs(payload: SystemPayload) -> Dict[str, Any]:
    """Render the kwargs Anthropic's ``messages.create`` expects.

    When the flag is off (or the provider isn't Anthropic) we fall back
    to the plain-string form so the kwargs stay valid for every caller.
    """
    if payload.provider is Provider.ANTHROPIC and payload.cache_hits_expected:
        return {"system": payload.to_anthropic_system()}
    return {"system": payload.to_flat_text()}
