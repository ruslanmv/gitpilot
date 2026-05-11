# gitpilot/tool_def_pruner.py
"""Prune MCP tool descriptors to only those allowed by the active policy.

Batch P2-B — additive.  Every MCP tool description in the prompt costs
input tokens on every turn.  When the active mode only needs a subset
of tools (most modes do), shipping the full catalogue is pure waste.

This module is a stateless filter.  Callers that already build a list
of :class:`MCPToolDescriptor` objects can wrap them in
:func:`prune_descriptors` (or rely on the optional ``policy=`` argument
:mod:`gitpilot.mcp_tools_bridge` exposes after this batch).  When no
policy is supplied (``policy=None``) the function returns the input
unchanged — legacy parity guaranteed.

When the ``lazy_tool_defs`` flag is off the bridge wrapper short-circuits
to the legacy path, so toggling the flag is a single env-var change.
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence

from . import flags
from .tool_groups import MCPGuard, ToolCategory, ToolPolicy

logger = logging.getLogger(__name__)

FLAG_LAZY_TOOL_DEFS = "lazy_tool_defs"


class _NamedDescriptor(Protocol):
    """Minimal shape for any descriptor the pruner accepts."""

    name: str
    server_id: str


@dataclass(frozen=True)
class PruneReport:
    """Summary returned alongside the pruned descriptor list."""

    kept: int
    dropped: int
    reason_counts: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kept": self.kept,
            "dropped": self.dropped,
            "reasons": dict(self.reason_counts),
        }


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def prune_descriptors(
    descriptors: Sequence[_NamedDescriptor],
    *,
    policy: Optional[ToolPolicy] = None,
    enabled: Optional[bool] = None,
) -> tuple[List[_NamedDescriptor], PruneReport]:
    """Return only descriptors permitted by *policy*.

    Behaviour matrix:

    * ``policy=None`` → input is returned unchanged (legacy parity).
    * ``enabled=False`` or the global flag is off → input is returned
      unchanged.  Use this to ship the feature dark.
    * Otherwise: descriptors are kept only when the MCP category is
      enabled by the policy and the per-server / per-tool toggles
      allow the qualified name ``"<server>.<tool>"``.

    Returns a list of descriptors plus a small :class:`PruneReport`
    suitable for logging or surfacing in the doctor command.
    """
    if policy is None:
        return list(descriptors), PruneReport(len(descriptors), 0, {})

    flag_on = enabled if enabled is not None else flags.is_on(FLAG_LAZY_TOOL_DEFS)
    if not flag_on:
        return list(descriptors), PruneReport(len(descriptors), 0, {})

    if policy.restrictive and ToolCategory.MCP not in policy.enabled_categories:
        # Mode explicitly excludes MCP — drop everything.
        category_reasons: Dict[str, int] = {"mcp-category-disabled": len(descriptors)}
        return [], PruneReport(0, len(descriptors), category_reasons)

    guard: MCPGuard = policy.mcp_guard
    kept: List[_NamedDescriptor] = []
    dropped = 0
    reasons: Dict[str, int] = {}
    for desc in descriptors:
        qualified = f"{desc.server_id}.{desc.name}"
        reason = _why_dropped(qualified, guard)
        if reason is None:
            kept.append(desc)
        else:
            dropped += 1
            reasons[reason] = reasons.get(reason, 0) + 1
    if dropped:
        logger.debug(
            "tool-def-pruner: kept=%d dropped=%d reasons=%s",
            len(kept), dropped, reasons,
        )
    return kept, PruneReport(len(kept), dropped, reasons)


def _why_dropped(qualified: str, guard: MCPGuard) -> Optional[str]:
    server = qualified.split(".", 1)[0]
    if server in guard.disabled_servers:
        return "server-disabled"
    if qualified in guard.deny or _glob_in(qualified, guard.deny):
        return "tool-denied"
    if guard.allow:
        if not _glob_in(qualified, guard.allow):
            return "not-in-allowlist"
    return None


def _glob_in(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)
