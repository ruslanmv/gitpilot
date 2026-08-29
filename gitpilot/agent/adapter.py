# gitpilot/agent/adapter.py
"""The dialect interface — Batch V4-B3.

An adapter is the only thing in the runtime that knows how a particular kind of
model is spoken to.  It turns (system payload, messages, tool schemas) into a
request, and a response back into an :class:`~.contracts.AgentTurn`.  The loop
above it does not branch on provider or dialect at all — that is what makes the
same engine drive Claude and a 1.5B model on Ollama.

Four responsibilities, and the third and fourth are easy to overlook:

1. ``generate`` — one turn.
2. ``result_messages`` — how a tool result re-enters the conversation.  Native
   tool APIs, a text grammar and the lite protocol disagree completely here.
3. ``conclude`` — resolve :attr:`~.contracts.Finality.FINAL_AFTER_TOOLS` once the
   loop has dispatched the turn's calls (LITE needs this; the others do not).
4. ``assistant_message`` — how the model's own turn is recorded, so the next
   request contains a faithful history.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from ..toolkit.registry import (
    CapabilitySet,
    ToolCall,
    ToolRegistry,
    ToolResult,
)
from .contracts import AgentTurn, Dialect, Finality
from .model_profile import ModelProfile
from .providers.base import ChatProvider, ToolDef

logger = logging.getLogger(__name__)


class ModelAdapter:
    """Base class.  Subclasses implement one dialect."""

    dialect: Dialect

    def __init__(
        self,
        provider: ChatProvider,
        profile: ModelProfile,
        registry: Optional[ToolRegistry] = None,
    ) -> None:
        self.provider = provider
        self.profile = profile
        self.registry = registry

    # -- tool surface -----------------------------------------------------

    def tool_defs(self, capabilities: Optional[CapabilitySet] = None) -> List[ToolDef]:
        """The tools this turn may use, rendered at the profile's verbosity.

        Built from the registry's own render so the schema the model sees and the
        schema its arguments are validated against are the same object.
        """
        if self.registry is None:
            return []
        render = self.registry.render(
            capabilities=capabilities, profile=self.profile,
            lite_only=self.dialect is Dialect.LITE,
        )
        if render.truncated:
            logger.debug(
                "%s: %d tool(s) withheld from the model (%s)",
                self.dialect.value,
                len(render.dropped),
                ", ".join(f"{d.id}:{d.reason}" for d in render.dropped[:5]),
            )
        return [ToolDef.from_render(schema) for schema in render.schemas]

    def dropped_tools(self, capabilities: Optional[CapabilitySet] = None) -> List[Any]:
        """What ``tool_defs`` left out, for the loop's ``tools_pruned`` event."""
        if self.registry is None:
            return []
        return self.registry.render(capabilities=capabilities, profile=self.profile).dropped

    # -- the turn ---------------------------------------------------------

    async def generate(
        self,
        *,
        system: Any = None,
        messages: Sequence[Dict[str, Any]],
        capabilities: Optional[CapabilitySet] = None,
        **opts: Any,
    ) -> AgentTurn:
        raise NotImplementedError

    def assistant_message(self, turn: AgentTurn) -> Optional[Dict[str, Any]]:
        """How to record the model's own turn in the message history."""
        raise NotImplementedError

    def result_messages(
        self, call: ToolCall, result: ToolResult,
    ) -> List[Dict[str, Any]]:
        """How a tool result re-enters the conversation."""
        raise NotImplementedError

    def conclude(self, turn: AgentTurn, results: Sequence[ToolResult]) -> bool:
        """Whether a ``FINAL_AFTER_TOOLS`` turn actually finished.

        Only the LITE dialect produces such turns; the default says no, which
        keeps the loop iterating rather than stopping on a maybe.
        """
        return False


def build_adapter(
    provider: ChatProvider,
    profile: ModelProfile,
    registry: Optional[ToolRegistry] = None,
) -> ModelAdapter:
    """The adapter for *profile*'s dialect.

    A NATIVE profile on a provider that cannot take tool definitions is
    corrected here rather than failing at request time — watsonx does not claim
    native tools, and a mis-set ``models.yaml`` should not produce a run where
    every tool call comes back malformed.
    """
    from .dialects.lite import LiteAdapter
    from .dialects.native import NativeAdapter
    from .dialects.react_text import ReactTextAdapter

    dialect = profile.dialect
    if dialect is Dialect.NATIVE and not getattr(provider, "supports_native_tools", False):
        logger.info(
            "%s does not support native tool calling; using the text grammar for %s",
            getattr(provider, "name", "provider"),
            profile.model,
        )
        profile = profile.downgraded()
        dialect = profile.dialect

    if dialect is Dialect.NATIVE:
        return NativeAdapter(provider, profile, registry)
    if dialect is Dialect.REACT_TEXT:
        return ReactTextAdapter(provider, profile, registry)
    return LiteAdapter(provider, profile, registry)


def synthesize_call_id(prefix: str, index: int) -> str:
    """A stable handle for a call the provider did not give an id for.

    The text dialects parse calls out of prose, so the loop needs to mint one to
    correlate results, events and journal lines.
    """
    return f"{prefix}_{index}"


def finality_for(tool_calls: Sequence[Any], text: str) -> Finality:
    """The rule shared by the two dialects where "no calls" means "done"."""
    if tool_calls:
        return Finality.CONTINUE
    return Finality.FINAL if text.strip() else Finality.CONTINUE
