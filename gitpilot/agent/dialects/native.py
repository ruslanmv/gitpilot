# gitpilot/agent/dialects/native.py
"""The NATIVE dialect — Batch V4-B3.

For models whose provider takes tool definitions and returns structured tool
calls.  This is the cheapest dialect to be correct in: the provider does the
parsing, so there is no grammar to coax a model into and no repair to attempt.

Streaming is the one piece of real work here.  Tool calls arrive in fragments —
a name in one chunk, argument JSON split at arbitrary byte boundaries across
several — so :class:`~..providers.base.ToolCallAccumulator` assembles them while
text deltas are forwarded as they land.  That is what replaces
``agent_executor``'s 80-characters-every-15ms chunking of an already-complete
answer.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from ...toolkit.registry import CapabilitySet, ToolCall, ToolResult
from ..adapter import ModelAdapter, finality_for
from ..contracts import AgentTurn, Dialect
from ..providers.base import ProviderResponse, RawToolCall, ToolCallAccumulator

logger = logging.getLogger(__name__)

#: Called with each text delta as it arrives, for the loop's ``text_delta``
#: events.  Streaming is pointless without somewhere to put the increments.
TextSink = Callable[[str], Awaitable[None]]


class NativeAdapter(ModelAdapter):
    """Provider-native tool calling."""

    dialect = Dialect.NATIVE

    async def generate(
        self,
        *,
        system: Any = None,
        messages: Sequence[Dict[str, Any]],
        capabilities: Optional[CapabilitySet] = None,
        on_text: Optional[TextSink] = None,
        stream: Optional[bool] = None,
        **opts: Any,
    ) -> AgentTurn:
        tools = self.tool_defs(capabilities)

        should_stream = (
            self.profile.supports_streaming
            and getattr(self.provider, "supports_streaming", False)
            and on_text is not None
        ) if stream is None else stream

        if should_stream:
            response = await self._stream_response(
                system=system, messages=messages, tools=tools, on_text=on_text, **opts,
            )
        else:
            response = await self.provider.complete(
                messages, tools=tools or None, system=system, **opts,
            )

        return self._to_turn(response)

    async def _stream_response(
        self,
        *,
        system: Any,
        messages: Sequence[Dict[str, Any]],
        tools: List[Any],
        on_text: Optional[TextSink],
        **opts: Any,
    ) -> ProviderResponse:
        accumulator = ToolCallAccumulator()
        text_parts: List[str] = []
        finish_reason = ""
        usage = None

        async for chunk in self.provider.stream(
            messages, tools=tools or None, system=system, **opts,
        ):
            if chunk.text:
                text_parts.append(chunk.text)
                if on_text is not None:
                    await on_text(chunk.text)
            if chunk.tool_call_index is not None:
                accumulator.add(chunk)
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason
            if chunk.usage is not None:
                usage = chunk.usage

        return ProviderResponse(
            text="".join(text_parts).strip(),
            tool_calls=accumulator.finish(),
            finish_reason=finish_reason,
            usage=usage,
        )

    def _to_turn(self, response: ProviderResponse) -> AgentTurn:
        calls = [
            ToolCall(id=raw.id, tool=raw.name, arguments=dict(raw.arguments))
            for raw in response.tool_calls
        ]

        if not self.profile.supports_parallel_tool_calls and len(calls) > 1:
            # Serial-only models sometimes emit several anyway; running the rest
            # against state the first call changed is how a model ends up acting
            # on a file it has already rewritten.
            logger.debug(
                "%s returned %d calls but is not marked parallel-capable; keeping the first",
                self.profile.model,
                len(calls),
            )
            calls = calls[:1]

        return AgentTurn(
            text=response.text,
            tool_calls=calls,
            finality=finality_for(calls, response.text),
            usage=response.usage,
            meta={"finish_reason": response.finish_reason},
        )

    # -- message shapes ---------------------------------------------------

    def assistant_message(self, turn: AgentTurn) -> Optional[Dict[str, Any]]:
        """Record the turn in the OpenAI-ish shape the loop keeps history in.

        Anthropic's block format is produced by its provider client at request
        time, so the history stays provider-neutral — which is what lets a
        resumed run continue against a different provider (§9.3).
        """
        if not turn.text and not turn.tool_calls:
            return None

        message: Dict[str, Any] = {"role": "assistant", "content": turn.text or ""}
        if turn.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.tool,
                        "arguments": json.dumps(call.arguments, sort_keys=True),
                    },
                }
                for call in turn.tool_calls
            ]
        return message

    def result_messages(self, call: ToolCall, result: ToolResult) -> List[Dict[str, Any]]:
        return [{
            "role": "tool",
            "tool_call_id": call.id,
            "name": call.tool,
            "content": result.content,
        }]
