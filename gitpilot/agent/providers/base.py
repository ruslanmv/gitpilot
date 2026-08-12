# gitpilot/agent/providers/base.py
"""Provider-agnostic chat contracts — Batch V4-B1.

What every provider client in this package returns, and the error taxonomy the
dialects and the loop branch on.

Two rules hold for every client here, both learned from
:mod:`gitpilot.llm_provider`'s docstrings:

* **No CrewAI, no litellm.**  Those cost 10–60 seconds of import time and pull
  ~180 packages; the loop calls a provider on every iteration.
* **No writes to ``os.environ``.**  ``llm_provider`` sets ``OPENAI_API_KEY`` and
  ``OPENAI_API_BASE`` process-wide to configure CrewAI, and documents the
  contamination that causes — ``direct_chat`` then reads those variables and
  reaches a conclusion about a provider the user never selected.  Credentials
  travel in the request, never in the process.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol, Sequence

from ..contracts import TokenUsage


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProviderError(RuntimeError):
    """Base class.  Every message here is meant to be shown to a user.

    These are the errors people are most likely to hit and least able to debug,
    so each subclass carries a sentence that says what to do rather than what
    went wrong internally.
    """


class ProviderNotReachable(ProviderError):
    """Configured but did not answer: wrong URL, nothing listening, timeout."""


class ProviderAuthError(ProviderError):
    """Rejected the credentials (401/403)."""


class ProviderQuotaError(ProviderError):
    """Out of credit or rate-limited (429, or a 400 that says quota)."""


class ModelNotFound(ProviderError):
    """The endpoint is fine; that model is not there (404, or Ollama's 400)."""


class MalformedResponse(ProviderError):
    """Answered with something GitPilot could not read."""


# ---------------------------------------------------------------------------
# Request pieces
# ---------------------------------------------------------------------------


@dataclass
class ToolDef:
    """One tool as the model should see it.

    Built from :meth:`gitpilot.toolkit.ToolRegistry.render`, so the schema a
    provider receives and the schema the registry validates against are the
    same object — they cannot drift into "the model was told one thing and
    checked against another".
    """

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})

    @classmethod
    def from_render(cls, rendered: Dict[str, Any]) -> "ToolDef":
        return cls(
            name=rendered["name"],
            description=rendered.get("description", ""),
            parameters=rendered.get("parameters") or {"type": "object", "properties": {}},
        )


@dataclass
class RawToolCall:
    """A tool call as the provider reported it, before registry resolution."""

    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResponse:
    """One completed turn from a provider."""

    text: str = ""
    tool_calls: List[RawToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: Optional[TokenUsage] = None
    #: Provider-specific extras a dialect may need (Anthropic's stop_reason,
    #: a gateway's reasoning field). Never persisted (see §9.2).
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


@dataclass
class StreamChunk:
    """One streamed increment.

    Tool calls arrive as fragments — a name in one chunk, JSON arguments split
    across several — so a chunk carries an index to accumulate against rather
    than a finished call.  :class:`ToolCallAccumulator` does the assembly.
    """

    text: str = ""
    tool_call_index: Optional[int] = None
    tool_call_id: str = ""
    tool_call_name: str = ""
    tool_call_arguments_delta: str = ""
    done: bool = False
    finish_reason: str = ""
    usage: Optional[TokenUsage] = None


class ToolCallAccumulator:
    """Assembles streamed tool-call fragments into :class:`RawToolCall`s.

    Argument JSON is concatenated verbatim and parsed only at the end: a
    fragment is not valid JSON on its own, and providers split it at arbitrary
    byte boundaries.  A payload that never becomes parseable yields ``{}``
    rather than raising — the registry's argument validation will then tell the
    model precisely what was missing, which is a better correction than a
    transport-level error.
    """

    def __init__(self) -> None:
        self._calls: Dict[int, Dict[str, str]] = {}

    def add(self, chunk: StreamChunk) -> None:
        if chunk.tool_call_index is None:
            return
        slot = self._calls.setdefault(
            chunk.tool_call_index, {"id": "", "name": "", "arguments": ""},
        )
        if chunk.tool_call_id:
            slot["id"] = chunk.tool_call_id
        if chunk.tool_call_name:
            slot["name"] = chunk.tool_call_name
        if chunk.tool_call_arguments_delta:
            slot["arguments"] += chunk.tool_call_arguments_delta

    def finish(self) -> List[RawToolCall]:
        import json

        out: List[RawToolCall] = []
        for index in sorted(self._calls):
            slot = self._calls[index]
            if not slot["name"]:
                continue
            raw = slot["arguments"].strip()
            try:
                arguments = json.loads(raw) if raw else {}
            except ValueError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {"value": arguments}
            out.append(
                RawToolCall(
                    id=slot["id"] or f"call_{index}",
                    name=slot["name"],
                    arguments=arguments,
                )
            )
        return out


# ---------------------------------------------------------------------------
# The client protocol
# ---------------------------------------------------------------------------


class ChatProvider(Protocol):
    """What a provider client must offer.

    A Protocol rather than a base class: the three implementations share no
    logic worth inheriting, and structural typing keeps a test double from
    needing to subclass anything.
    """

    name: str
    model: str
    supports_native_tools: bool
    supports_parallel_tool_calls: bool
    supports_streaming: bool

    async def complete(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        tools: Optional[Sequence[ToolDef]] = None,
        system: Any = None,
        **opts: Any,
    ) -> ProviderResponse: ...

    def stream(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        tools: Optional[Sequence[ToolDef]] = None,
        system: Any = None,
        **opts: Any,
    ) -> AsyncIterator[StreamChunk]: ...


#: Sampling options forwarded to providers.  A whitelist, following
#: ``inference.OpenAICompatibleClient``: gateways reject unknown fields, and an
#: unrecognised key is more likely a caller's typo than a feature.
ALLOWED_OPTS = ("temperature", "max_tokens", "top_p", "stop", "seed")


def filtered_opts(opts: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in opts.items() if k in ALLOWED_OPTS and v is not None}
