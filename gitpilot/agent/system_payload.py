# gitpilot/agent/system_payload.py
"""The live system prefix — Batch V4-H2.

Three pieces of GitPilot have been assembling system prompts for a while, and only
one of them was ever sent to a model.

:func:`gitpilot.prompt_cache.build_system_blocks` composes the whole structured
payload — base instructions, ``AGENTS.md``, rules, tool descriptors with a digest,
and a cheap uncached tail — and marks the stable part cacheable so Anthropic's
prompt cache can hit it. It has been tested since Phase 2 and used by nothing that
talks to a model.

:func:`gitpilot.modes.activate_mode` resolves a ``modes.yaml`` persona into three
things a session should apply: a system-prompt block, a ``ToolPolicy``, and MCP
servers to start. It has had **no caller at all**. A user could write a mode, see it
listed, select it, and have it change nothing.

:mod:`gitpilot.agent.prompts` writes the loop's actual system text, and it is the
one that ships. It is deliberately short because everything else was supposed to
join it here.

This module is the join. What it does not do is decide how tools are described: the
dialects already differ exactly there — REACT_TEXT and LITE append their rendered
manual to the system text, NATIVE passes schemas out of band — so the payload
carries the *project*, and each dialect carries the tool vocabulary it speaks.

The ordering is the prompt cache's, not a preference: base, AGENTS.md, rules, tool
digest, then the volatile tail. Anything that changes between turns after something
stable would invalidate the stable part on every request, which is the whole failure
mode a cacheable prefix exists to avoid.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

FLAG_AGENT_SYSTEM_PAYLOAD = "agent_system_payload"

#: Characters of session tail to carry.  The tail is re-sent every turn and never
#: cached, so it is the most expensive block per token in a long run.
MAX_TAIL_CHARS = 2_000


@dataclass
class ActiveMode:
    """A resolved ``modes.yaml`` persona, with what a session should do about it."""

    slug: str
    prompt_block: str = ""
    tool_policy: Any = None
    mcp_server_configs: List[Dict[str, Any]] = field(default_factory=list)
    #: ``(server, enabled_tools, always_allow)`` triples the mode declares.
    mcp_toggles: List[Any] = field(default_factory=list)
    name: str = ""

    @property
    def restricts_tools(self) -> bool:
        return getattr(self.tool_policy, "restrictive", False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "has_prompt_block": bool(self.prompt_block),
            "restricts_tools": self.restricts_tools,
            "mcp_servers": [config.get("name", "") for config in self.mcp_server_configs],
        }


def resolve_mode(slug: str, workspace: Optional[Path] = None) -> Optional[ActiveMode]:
    """Load ``modes.yaml`` and resolve *slug* — :func:`activate_mode`'s first caller.

    Returns ``None`` for an unknown slug, which is what lets a session name a mode
    it does not have without failing: the run proceeds unconfigured, exactly as it
    did before modes existed.
    """
    if not slug:
        return None
    try:
        from ..modes import ModeRegistry, activate_mode

        registry = ModeRegistry()
        registry.load(Path(workspace) if workspace else None)
        context = activate_mode(registry, slug)
    except Exception as exc:  # noqa: BLE001 - a bad modes file must not fail a run
        logger.warning("could not activate mode %r: %s", slug, exc)
        return None

    if context is None:
        logger.info("mode %r is not defined in this workspace; running unconfigured", slug)
        return None

    return ActiveMode(
        slug=slug,
        name=getattr(context.mode, "name", slug),
        prompt_block=context.system_prompt_block or "",
        tool_policy=context.tool_policy,
        mcp_server_configs=list(context.mcp_server_configs or []),
        mcp_toggles=list(context.extra_mcp_toggles or []),
    )


def register_mode_servers(mode: Optional[ActiveMode], client: Any) -> List[str]:
    """Add a mode's inline MCP servers to *client*, returning their names.

    "Started and stopped with the mode" in the design (§?) means exactly this at the
    client level: a mode's servers become configured servers for the duration, and a
    different mode's do not. Connections are made lazily by
    :func:`gitpilot.toolkit.mcp.register_from_store`, so adding a config that turns
    out to be unreachable costs nothing until something asks for it.
    """
    if mode is None or client is None:
        return []
    added: List[str] = []
    for config in mode.mcp_server_configs:
        name = str(config.get("name") or "")
        if not name:
            continue
        try:
            from ..mcp_client import MCPServerConfig

            client.add_server(MCPServerConfig.from_dict(config))
            added.append(name)
        except Exception as exc:  # noqa: BLE001 - one bad server config is not the run
            logger.warning("mode %r declares an unusable MCP server %r: %s", mode.slug, name, exc)
    return added


def mask_for_mode(mode: Optional[ActiveMode], mask: Any) -> Any:
    """``mask ∩ mode`` — the mode's tool policy, intersected in.

    Intersected rather than replaced, and that direction is the point: a mode is
    configuration a user wrote, and configuration may narrow what a topology granted
    but never widen it.
    """
    if mode is None or mode.tool_policy is None:
        return mask
    try:
        from .policy import mask_from_tool_policy

        return mask.intersect(mask_from_tool_policy(mode.tool_policy))
    except Exception as exc:  # noqa: BLE001 - a mode that cannot narrow does not widen
        logger.warning("could not apply mode %r's tool policy: %s", mode.slug, exc)
        return mask


def build_payload(
    *,
    base_system: str,
    workspace: Optional[Path] = None,
    mode: Optional[ActiveMode] = None,
    tool_defs: Optional[Sequence[Mapping[str, Any]]] = None,
    session_tail: str = "",
    provider: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> Any:
    """The structured payload for one run, via ``prompt_cache``.

    A mode's persona goes into ``base_system`` rather than into the tail, because a
    persona is stable for the whole session and the tail is re-sent uncached every
    turn. Putting it in the tail would be paying for it on every request.
    """
    from ..prompt_cache import build_system_blocks

    base = base_system.strip()
    if mode is not None and mode.prompt_block:
        base = f"{base}\n\n{mode.prompt_block.strip()}".strip()

    return build_system_blocks(
        base_system=base,
        workspace=workspace,
        mode_slug=mode.slug if mode else None,
        tool_defs=tool_defs,
        session_conventions=_trim_tail(session_tail),
        provider=provider,
        enabled=enabled,
    )


def render(payload: Any) -> str:
    """The payload as the single system string every dialect currently takes."""
    from ..prompt_cache import to_legacy_system_string

    return to_legacy_system_string(payload)


def build_system_text(
    *,
    base_system: str,
    workspace: Optional[Path] = None,
    mode: Optional[ActiveMode] = None,
    tool_defs: Optional[Sequence[Mapping[str, Any]]] = None,
    session_tail: str = "",
    provider: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> str:
    """:func:`build_payload` rendered — what the loop puts in ``ctx.system_payload``."""
    return render(build_payload(
        base_system=base_system,
        workspace=workspace,
        mode=mode,
        tool_defs=tool_defs,
        session_tail=session_tail,
        provider=provider,
        enabled=enabled,
    ))


def _trim_tail(text: str) -> str:
    """Keep the end of the tail, not the beginning.

    What changed most recently is what the next turn needs; the start of a long
    session tail is the part the transcript already carries.
    """
    trimmed = (text or "").strip()
    if len(trimmed) <= MAX_TAIL_CHARS:
        return trimmed
    return "…\n" + trimmed[-MAX_TAIL_CHARS:]


def payload_enabled(flag_enabled: Optional[bool] = None) -> bool:
    """Whether the composed payload replaces the plain prompt.

    Off by default. With it off, ``ctx.system_payload`` is exactly the string
    ``build_system_prompt`` has been producing since Phase C — the byte-identical
    off-branch batch rule 2 requires.
    """
    if flag_enabled is not None:
        return bool(flag_enabled)
    from ..flags import is_on

    return is_on(FLAG_AGENT_SYSTEM_PAYLOAD, default=False)
