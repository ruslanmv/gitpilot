# gitpilot/toolkit/web.py
"""Web tools — Batch V4-A6.

**This module intentionally registers nothing today.**

GitPilot has no web search or fetch implementation.  ``WebSearch`` and
``WebFetch`` appear in four topology flow graphs
(:mod:`gitpilot.topology_registry`) and in ``tool_groups``' category table, but
grep the package and there is no provider behind either name — the "Search
Agent" and "Research Subagent" nodes describe a capability that has never
existed.

The registry's rule is that an absent provider means an absent tool, never a
tool that is present and always fails.  A model shown ``web.search`` will use
it: on a small model an error observation frequently produces a retry rather
than a change of plan, and every retry is a whole turn.  Worse, a tool that
exists in the schema list but cannot work makes the *topology* look
configurable when it is not.

:func:`register` is the seam.  When a provider lands, implement the handlers,
gate them on the provider being configured, and the topologies that already
name web tools start working with no change to their definitions.  The unit
test asserting the current emptiness is the reminder — it fails the day this
module starts registering, which is when the flow-graph nodes must be revisited.
"""
from __future__ import annotations

from typing import Any, List, Tuple

from .registry import ToolRegistry, ToolSpec

#: Web tools are declared as a namespace so a topology can grant ``web.*``
#: before any provider exists; the grant is simply inert until one does.
WEB_CAPABILITY_NAMESPACE = "web"

#: Empty until a provider is implemented.  Kept as the same ``(spec, handler)``
#: shape as the other tool modules so wiring one up is a list append.
SPECS: List[Tuple[ToolSpec, Any]] = []


def provider_available() -> bool:
    """Whether a web search/fetch provider is configured.

    Always ``False`` today.  Kept as a function rather than a constant because
    the answer will become configuration-dependent, and callers should already
    be asking the question rather than assuming.
    """
    return False


def register(registry: ToolRegistry) -> None:
    """Add the web tools to *registry* — a no-op while no provider exists."""
    if not provider_available():
        return
    for spec, handler in SPECS:   # pragma: no cover - unreachable until a provider lands
        registry.register(spec, handler)
