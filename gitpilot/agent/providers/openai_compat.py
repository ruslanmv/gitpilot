# gitpilot/agent/providers/openai_compat.py
"""OpenAI-compatible chat client — Batch V4-B1.

Serves OpenAI, Ollama, Open WebUI, OllaBridge and any custom endpoint that
speaks ``/chat/completions`` — five of GitPilot's seven providers, and the two
that matter most for the low-end case.

Grown from :func:`gitpilot.direct_chat.chat`: its error taxonomy and its
defensive response reading are reproduced here rather than reimplemented,
because both encode things learned from real gateways —

* content can arrive as a list of parts even for a plain text answer;
* a reasoning model that spends its whole budget thinking leaves ``content``
  empty and the substance in ``reasoning`` (Ollama 0.32) or
  ``reasoning_content`` (vLLM/DeepSeek), and reading only ``content`` reports
  "empty response", sending the user to debug a provider that is working;
* a few local servers still answer in the legacy completion shape.

What is new is tool calling and streaming, neither of which ``direct_chat``
needed for a single-shot answer.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

import httpx

from ...reasoning_normalizer import visible_answer
from ..contracts import TokenUsage
from .base import (
    MalformedResponse,
    ModelNotFound,
    ProviderAuthError,
    ProviderNotReachable,
    ProviderQuotaError,
    ProviderResponse,
    RawToolCall,
    StreamChunk,
    ToolCallAccumulator,
    ToolDef,
    filtered_opts,
)

logger = logging.getLogger(__name__)

#: Long enough for a large local model on a cold cache, short enough that a
#: wedged endpoint does not hang a run forever.  Same value as ``direct_chat``.
DEFAULT_TIMEOUT_SECONDS = 180.0

_QUOTA_MARKERS = (
    "insufficient_quota",
    "exceeded your current quota",
    "rate_limit_exceeded",
    "billing",
    "too many requests",
)


class OpenAICompatibleProvider:
    """Chat over ``POST {base_url}/chat/completions``."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        headers: Optional[Dict[str, str]] = None,
        name: str = "openai_compat",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        supports_native_tools: bool = True,
        supports_parallel_tool_calls: bool = True,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = name
        self.supports_native_tools = supports_native_tools
        self.supports_parallel_tool_calls = supports_parallel_tool_calls
        self.supports_streaming = True
        self._api_key = api_key
        self._headers = dict(headers or {})
        self._timeout = timeout
        #: Injected by the record/replay harness.  Production passes None and
        #: httpx opens a real connection.
        self._transport = transport

    # -- request assembly -------------------------------------------------

    @property
    def url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _request_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", **self._headers}
        if self._api_key:
            headers.setdefault("Authorization", f"Bearer {self._api_key}")
        return headers

    def build_body(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        tools: Optional[Sequence[ToolDef]] = None,
        system: Any = None,
        stream: bool = False,
        **opts: Any,
    ) -> Dict[str, Any]:
        """The JSON body.  Exposed so tests can assert the wire shape directly."""
        merged: List[Dict[str, Any]] = []
        if system:
            # This shape has no separate system parameter; a leading system
            # message is the convention.
            merged.append({"role": "system", "content": _system_text(system)})
        merged.extend(dict(message) for message in messages)

        body: Dict[str, Any] = {"model": self.model, "messages": merged, "stream": stream}
        if tools and self.supports_native_tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
            body["tool_choice"] = "auto"
        body.update(filtered_opts(opts))
        return body

    # -- transport --------------------------------------------------------

    def _client(self, *, stream: bool = False) -> httpx.AsyncClient:
        kwargs: Dict[str, Any] = {"timeout": self._timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    def _raise_for_status(self, status: int, text: str) -> None:
        """Map a status onto the taxonomy, so callers branch on meaning."""
        if status == 404:
            raise ModelNotFound(
                f"{self.name} has no model named {self.model!r} at {self.base_url}. "
                "Pull it first, or pick another in Settings."
            )
        if status in (401, 403):
            raise ProviderAuthError(
                f"{self.name} rejected the credentials for {self.base_url}. "
                "Check the API key in Settings → AI Providers."
            )
        lowered = text.lower()
        if status == 429 or any(marker in lowered for marker in _QUOTA_MARKERS):
            raise ProviderQuotaError(
                f"{self.name} is rate-limited or out of credit. Check your plan and "
                "billing, or switch to a local provider in Settings."
            )
        if status >= 400:
            # Ollama answers 400 for an unpulled model rather than 404.
            if status == 400 and ("not found" in lowered or "try pulling" in lowered):
                raise ModelNotFound(
                    f"{self.name} has no model named {self.model!r} at {self.base_url}. "
                    "Pull it first, or pick another in Settings."
                )
            raise ProviderNotReachable(f"{self.name} returned HTTP {status}: {text[:300]}")

    # -- completion -------------------------------------------------------

    async def complete(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        tools: Optional[Sequence[ToolDef]] = None,
        system: Any = None,
        **opts: Any,
    ) -> ProviderResponse:
        body = self.build_body(messages, tools=tools, system=system, stream=False, **opts)

        try:
            async with self._client() as client:
                response = await client.post(self.url, json=body, headers=self._request_headers())
        except httpx.TimeoutException as exc:
            raise ProviderNotReachable(
                f"{self.name} did not respond within {int(self._timeout)}s at "
                f"{self.base_url}. A large model on a cold cache can exceed this — "
                "try again, or pick a smaller model in Settings."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderNotReachable(
                f"Could not reach {self.name} at {self.base_url}. Check that it is "
                f"running and the URL is right in Settings. ({exc})"
            ) from exc

        self._raise_for_status(response.status_code, response.text)

        try:
            payload = response.json()
        except ValueError as exc:
            raise MalformedResponse(
                f"{self.name} returned a response GitPilot could not read."
            ) from exc

        return self.parse_response(payload)

    def parse_response(self, payload: Dict[str, Any]) -> ProviderResponse:
        """Read an OpenAI-shaped payload defensively."""
        if not isinstance(payload, dict):
            raise MalformedResponse(f"{self.name} returned a non-object response.")

        choices = payload.get("choices") or []
        if not choices:
            raise MalformedResponse(f"{self.name} returned no choices.")

        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") or {}

        text = _extract_text(message, choice)
        text = visible_answer(text, self.model)

        tool_calls = _extract_tool_calls(message)

        return ProviderResponse(
            text=text,
            tool_calls=tool_calls,
            finish_reason=str(choice.get("finish_reason") or ""),
            usage=_usage(payload),
        )

    # -- streaming --------------------------------------------------------

    async def stream(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        tools: Optional[Sequence[ToolDef]] = None,
        system: Any = None,
        **opts: Any,
    ) -> AsyncIterator[StreamChunk]:
        body = self.build_body(messages, tools=tools, system=system, stream=True, **opts)

        try:
            async with self._client(stream=True) as client:
                async with client.stream(
                    "POST", self.url, json=body, headers=self._request_headers(),
                ) as response:
                    if response.status_code >= 400:
                        text = (await response.aread()).decode("utf-8", "replace")
                        self._raise_for_status(response.status_code, text)

                    async for line in response.aiter_lines():
                        chunk = _parse_stream_line(line)
                        if chunk is not None:
                            yield chunk
        except httpx.TimeoutException as exc:
            raise ProviderNotReachable(
                f"{self.name} stopped responding mid-stream after "
                f"{int(self._timeout)}s at {self.base_url}."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderNotReachable(
                f"Could not reach {self.name} at {self.base_url}. ({exc})"
            ) from exc


# ---------------------------------------------------------------------------
# Payload reading
# ---------------------------------------------------------------------------


def _system_text(system: Any) -> str:
    """Flatten whatever the caller passed as a system prompt.

    Accepts a string or the block list :mod:`gitpilot.prompt_cache` produces, so
    the same payload can be handed to any provider — only Anthropic can use the
    block structure, and the rest get it joined.
    """
    if isinstance(system, str):
        return system
    if isinstance(system, dict):
        return str(system.get("text", ""))
    if isinstance(system, (list, tuple)):
        parts = []
        for block in system:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(getattr(block, "text", "")))
        return "\n\n".join(part for part in parts if part)
    return str(system or "")


def _extract_text(message: Dict[str, Any], choice: Dict[str, Any]) -> str:
    content = message.get("content")

    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        ).strip()

    if isinstance(content, str) and content.strip():
        return content.strip()

    for key in ("reasoning", "reasoning_content"):
        thinking = message.get(key)
        if isinstance(thinking, str) and thinking.strip():
            return thinking.strip()

    if isinstance(content, str):
        return content.strip()

    return str(choice.get("text") or "").strip()


def _extract_tool_calls(message: Dict[str, Any]) -> List[RawToolCall]:
    raw_calls = message.get("tool_calls") or []
    calls: List[RawToolCall] = []
    for index, raw in enumerate(raw_calls):
        if not isinstance(raw, dict):
            continue
        function = raw.get("function") or {}
        name = str(function.get("name") or raw.get("name") or "")
        if not name:
            continue
        arguments = function.get("arguments", raw.get("arguments"))
        calls.append(
            RawToolCall(
                id=str(raw.get("id") or f"call_{index}"),
                name=name,
                arguments=_coerce_arguments(arguments),
            )
        )
    return calls


def _coerce_arguments(arguments: Any) -> Dict[str, Any]:
    """Arguments arrive as a JSON string from most gateways, a dict from some.

    Unparseable JSON becomes ``{}`` on purpose: the registry then reports which
    required argument is missing, which is a correction the model can act on.
    An exception here would instead abort the turn.
    """
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str) and arguments.strip():
        try:
            parsed = json.loads(arguments)
        except ValueError:
            logger.debug("unparseable tool arguments: %r", arguments[:200])
            return {}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return {}


def _usage(payload: Dict[str, Any]) -> Optional[TokenUsage]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    return TokenUsage(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
    )


def _parse_stream_line(line: str) -> Optional[StreamChunk]:
    """One SSE line into a chunk, or None for framing noise."""
    if not line or not line.startswith("data:"):
        return None
    data = line[len("data:"):].strip()
    if not data:
        return None
    if data == "[DONE]":
        return StreamChunk(done=True)

    try:
        payload = json.loads(data)
    except ValueError:
        logger.debug("unparseable stream line: %r", data[:200])
        return None

    choices = payload.get("choices") or []
    if not choices:
        return None
    choice = choices[0]
    delta = choice.get("delta") or {}

    tool_calls = delta.get("tool_calls") or []
    if tool_calls:
        raw = tool_calls[0]
        function = raw.get("function") or {}
        return StreamChunk(
            tool_call_index=int(raw.get("index") or 0),
            tool_call_id=str(raw.get("id") or ""),
            tool_call_name=str(function.get("name") or ""),
            tool_call_arguments_delta=str(function.get("arguments") or ""),
            finish_reason=str(choice.get("finish_reason") or ""),
        )

    text = delta.get("content")
    if isinstance(text, list):
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))

    return StreamChunk(
        text=str(text or ""),
        finish_reason=str(choice.get("finish_reason") or ""),
        usage=_usage(payload),
    )
