# gitpilot/toolkit/__init__.py
"""The canonical tool surface — Batch V4-A1.

One registry, namespaced ids (``fs.read``, ``terminal.run``, ``git.commit``),
JSON-Schema parameter contracts, and a risk class per tool.  Every capability
an agent can invoke — built-in, MCP, delegation, TODO — is meant to arrive
through here rather than through a framework's decorator, so that the agentic
loop (Batch V4-C2) has exactly one place to look up what a model may call and
exactly one place that executes it.

Nothing in this package imports CrewAI.  The legacy ``@tool``-decorated
functions in :mod:`gitpilot.agent_tools` and :mod:`gitpilot.local_tools`
continue to serve the existing execution paths untouched; the handlers here
wrap the same *implementation* modules (``workspace``, ``github_api``,
``edit_backend``, ``grep_backend``, ``sandbox``, ``test_detection``) so both
surfaces cannot drift.

Gated by the ``tool_registry`` feature flag at the call sites that consume it;
importing this package is always safe.
"""
from __future__ import annotations

from .registry import (
    ALL_CAPABILITIES,
    LEGACY_ALIASES,
    CapabilitySet,
    Effect,
    LocalWorkspace,
    PrunedTool,
    RepoBinding,
    Risk,
    SchemaProfile,
    SchemaRender,
    ToolCall,
    ToolError,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    validate_arguments,
)

__all__ = [
    "ALL_CAPABILITIES",
    "LEGACY_ALIASES",
    "CapabilitySet",
    "Effect",
    "LocalWorkspace",
    "PrunedTool",
    "RepoBinding",
    "Risk",
    "SchemaProfile",
    "SchemaRender",
    "ToolCall",
    "ToolError",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "validate_arguments",
]
