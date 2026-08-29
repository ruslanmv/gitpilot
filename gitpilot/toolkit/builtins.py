# gitpilot/toolkit/builtins.py
"""Assembling the standard tool set — Batch V4-A6.

:func:`build_default_registry` is the one place that decides which tools exist.
It is deliberately a *function returning a fresh registry* rather than a module
level singleton: a registry is built per run from the session's context, so two
sessions with different permissions or different targets cannot see each other's
tools, and a test never inherits another test's registrations.

The ``tool_registry`` flag gates *consumption* of this registry by execution
paths rather than its construction: building one is always safe and has no side
effects.
"""
from __future__ import annotations

from typing import Any

from . import forge, fs, git, terminal, testing, todo, web
from .registry import ToolRegistry

#: Feature flag gating the *use* of this registry by execution paths.  Building
#: one is always safe and has no side effects.
FLAG_TOOL_REGISTRY = "tool_registry"


def build_default_registry(
    *,
    include_filesystem: bool = True,
    include_terminal: bool = True,
    include_git: bool = True,
    include_forge: bool = True,
    include_testing: bool = True,
    include_todo: bool = True,
    include_web: bool = True,
) -> ToolRegistry:
    """Return a registry holding GitPilot's built-in tools.

    The include flags exist for tests and for callers that know a namespace
    cannot work in their context — there is no point offering ``git.*`` to a
    session with no checkout.  They are *not* the permission mechanism:
    capability masks decide what a model may call (Batch V4-D1), and a tool
    withheld here is invisible to policy too.
    """
    registry = ToolRegistry()
    if include_filesystem:
        fs.register(registry)
    if include_terminal:
        terminal.register(registry)
    if include_git:
        git.register(registry)
    if include_forge:
        forge.register(registry)
    if include_testing:
        testing.register(registry)
    if include_todo:
        # Not offered to LITE — the adapter's schema render drops it via
        # `lite_compatible`, and the engine maintains the list instead.
        todo.register(registry)
    if include_web:
        # A no-op while no provider exists; see the module docstring.
        web.register(registry)
    return registry


def describe_default_registry() -> list[dict[str, Any]]:
    """A flat summary of the built-in tools, for docs and diagnostics."""
    registry = build_default_registry()
    return [
        {
            "id": spec.id,
            "title": spec.title,
            "capability": spec.capability,
            "risk": spec.risk.value,
            "effects": sorted(effect.value for effect in spec.effects),
            "signature": spec.signature(),
            "lite_compatible": spec.lite_compatible,
        }
        for spec in registry.specs()
    ]
