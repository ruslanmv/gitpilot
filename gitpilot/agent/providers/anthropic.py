# gitpilot/agent/providers/anthropic.py
"""Anthropic Messages client — Batch V4-B1.

Four things differ from the OpenAI shape, and each matters to the loop:

* **System is a parameter, not a message.**  That is what makes prompt caching
  possible: :mod:`gitpilot.prompt_cache` builds a block list with
  ``cache_control: ephemeral`` markers on the stable prefix, and this is the
  only provider that can use them.  Batch P2-A built that assembly and it has
  never reached a real call; here it does.
* **Tools take ``input_schema``**, not ``function.parameters``.
* **Content is a list of typed blocks.**  ``text`` and ``tool_use`` arrive in
  one response, so a turn can carry prose and calls together.
* **Tool results go back as ``tool_result`` blocks in a user message**, keyed by
  ``tool_use_id`` — not as a separate ``role: tool`` message.

``x-api-key`` rather than ``Authorization``, and the version header is
mandatory.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

import httpx

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
    ToolDef,
    filtered_opts,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_VERSION = "2023-06-01"
DEFAULT_TIMEOUT_SECONDS = 180.0
#: Anthropic requires ``max_tokens``; this mirrors ``llm_provider``'s default.
DEFAULT_MAX_TOKENS = 32_000


class AnthropicProvider:
    """Chat over ``POST {base_url}/v1/messages``."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        version: str = DEFAULT_VERSION,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.name = "claude"
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.supports_native_tools = True
        self.supports_parallel_tool_calls = True
        self.supports_streaming = True
        self._api_key = api_key
        self._version = version
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._transport = transport

    @property
    def url(self) -> str:
        return f"{self.base_url}/v1/messages"

    def _request_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": self._version,
        }

    # -- request assembly -------------------------------------------------

    def build_body(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        tools: Optional[Sequence[ToolDef]] = None,
        system: Any = None,
        stream: bool = False,
        **opts: Any,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": [_to_anthropic_message(message) for message in messages],
            "max_tokens": int(opts.pop("max_tokens", None) or self._max_tokens),
        }
        if stream:
            body["stream"] = True

        system_blocks = _system_blocks(system)
        if system_blocks:
            body["system"] = system_blocks

        if tools:
            body["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in tools
            ]

        allowed = filtered_opts(opts)
        allowed.pop("max_tokens", None)   # already applied above
        if "stop" in allowed:
            body["stop_sequences"] = allowed.pop("stop")
        body.update(allowed)
        return body

    def _raise_for_status(self, status: int, text: str) -> None:
        if status in (401, 403):
            raise ProviderAuthError(
                "Anthropic rejected the API key. Check it in Settings → AI Providers."
            )
        if status == 404:
            raise ModelNotFound(
                f"Anthropic has no model named {self.model!r}. Pick another in Settings."
            )
        if status == 429:
            raise ProviderQuotaError(
                "Anthropic is rate-limited or out of credit. Check your plan and billing, "
                "or switch to a local provider in Settings."
            )
        if status >= 400:
            lowered = text.lower()
            if "credit balance" in lowered or "quota" in lowered:
                raise ProviderQuotaError(f"Anthropic: {text[:300]}")
            raise ProviderNotReachable(f"Anthropic returned HTTP {status}: {text[:300]}")

    # -- completion -------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        kwargs: Dict[str, Any] = {"timeout": self._timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

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
                f"Anthropic did not respond within {int(self._timeout)}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderNotReachable(f"Could not reach Anthropic. ({exc})") from exc

        self._raise_for_status(response.status_code, response.text)

        try:
            payload = response.json()
        except ValueError as exc:
            raise MalformedResponse(
                "Anthropic returned a response GitPilot could not read."
            ) from exc

        return self.parse_response(payload)

    def parse_response(self, payload: Dict[str, Any]) -> ProviderResponse:
        if not isinstance(payload, dict):
            raise MalformedResponse("Anthropic returned a non-object response.")

        blocks = payload.get("content")
        if not isinstance(blocks, list):
            raise MalformedResponse("Anthropic returned no content blocks.")

        text_parts: List[str] = []
        tool_calls: List[RawToolCall] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                text_parts.append(str(block.get("text") or ""))
            elif kind == "tool_use":
                arguments = block.get("input")
                tool_calls.append(
                    RawToolCall(
                        id=str(block.get("id") or f"call_{len(tool_calls)}"),
                        name=str(block.get("name") or ""),
                        arguments=arguments if isinstance(arguments, dict) else {},
                    )
                )

        usage_payload = payload.get("usage") or {}
        usage = TokenUsage(
            prompt_tokens=int(usage_payload.get("input_tokens") or 0),
            completion_tokens=int(usage_payload.get("output_tokens") or 0),
        )

        return ProviderResponse(
            text="".join(text_parts).strip(),
            tool_calls=[call for call in tool_calls if call.name],
            finish_reason=str(payload.get("stop_reason") or ""),
            usage=usage,
            extra={"cache_read_input_tokens": usage_payload.get("cache_read_input_tokens")},
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
            async with self._client() as client:
                async with client.stream(
                    "POST", self.url, json=body, headers=self._request_headers(),
                ) as response:
                    if response.status_code >= 400:
                        text = (await response.aread()).decode("utf-8", "replace")
                        self._raise_for_status(response.status_code, text)

                    # ``content_block_start`` carries the tool name and id;
                    # ``input_json_delta`` events carry its arguments in
                    # fragments, so both must be tied to the same block index.
                    async for line in response.aiter_lines():
                        for chunk in _parse_stream_line(line):
                            yield chunk
        except httpx.TimeoutException as exc:
            raise ProviderNotReachable("Anthropic stopped responding mid-stream.") from exc
        except httpx.HTTPError as exc:
            raise ProviderNotReachable(f"Could not reach Anthropic. ({exc})") from exc


# ---------------------------------------------------------------------------
# Shape conversion
# ---------------------------------------------------------------------------


def _system_blocks(system: Any) -> List[Dict[str, Any]]:
    """Normalise a system payload into Anthropic's block list.

    A :class:`gitpilot.prompt_cache.SystemPayload` already knows how to render
    itself with cache markers, so it is asked; anything else is wrapped.  This is
    where P2-A's prompt cache finally reaches a request.
    """
    if not system:
        return []

    to_anthropic = getattr(system, "to_anthropic_system", None)
    if callable(to_anthropic):
        blocks = to_anthropic()
        if isinstance(blocks, list):
            return blocks
        if isinstance(blocks, str):
            return [{"type": "text", "text": blocks}]

    if isinstance(system, str):
        return [{"type": "text", "text": system}]
    if isinstance(system, dict):
        return [system]
    if isinstance(system, (list, tuple)):
        out: List[Dict[str, Any]] = []
        for block in system:
            if isinstance(block, dict):
                out.append(block)
            elif isinstance(block, str):
                out.append({"type": "text", "text": block})
            else:
                text = getattr(block, "text", None)
                if text:
                    out.append({"type": "text", "text": str(text)})
        return out
    return [{"type": "text", "text": str(system)}]


def _to_anthropic_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """Translate one normalised message into Anthropic's shape.

    The interesting case is a tool result: OpenAI sends ``role: tool`` with a
    ``tool_call_id``; Anthropic expects a ``user`` message carrying a
    ``tool_result`` block.  Keeping the loop's messages in the OpenAI-ish shape
    and converting here means the loop has one message format regardless of
    provider — which is what lets a run resume against a different one.
    """
    role = message.get("role")

    if role == "tool":
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": str(message.get("tool_call_id") or ""),
                    "content": str(message.get("content") or ""),
                }
            ],
        }

    if role == "assistant" and message.get("tool_calls"):
        blocks: List[Dict[str, Any]] = []
        text = message.get("content")
        if isinstance(text, str) and text.strip():
            blocks.append({"type": "text", "text": text})
        for raw in message["tool_calls"]:
            function = raw.get("function") or {}
            arguments = function.get("arguments", raw.get("arguments"))
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(raw.get("id") or ""),
                    "name": str(function.get("name") or raw.get("name") or ""),
                    "input": arguments if isinstance(arguments, dict) else {},
                }
            )
        return {"role": "assistant", "content": blocks}

    return {"role": role or "user", "content": message.get("content") or ""}


def _parse_stream_line(line: str) -> List[StreamChunk]:
    """One SSE line into zero or more chunks.

    Anthropic sends named events (``event: content_block_delta``) followed by a
    ``data:`` line; only the data carries what we need, and its ``type`` field
    repeats the event name, so the event lines are skipped.
    """
    if not line or not line.startswith("data:"):
        return []
    data = line[len("data:"):].strip()
    if not data:
        return []

    try:
        payload = json.loads(data)
    except ValueError:
        logger.debug("unparseable anthropic stream line: %r", data[:200])
        return []

    kind = payload.get("type")

    if kind == "content_block_start":
        block = payload.get("content_block") or {}
        if block.get("type") == "tool_use":
            return [
                StreamChunk(
                    tool_call_index=int(payload.get("index") or 0),
                    tool_call_id=str(block.get("id") or ""),
                    tool_call_name=str(block.get("name") or ""),
                )
            ]
        return []

    if kind == "content_block_delta":
        delta = payload.get("delta") or {}
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            return [StreamChunk(text=str(delta.get("text") or ""))]
        if delta_type == "input_json_delta":
            return [
                StreamChunk(
                    tool_call_index=int(payload.get("index") or 0),
                    tool_call_arguments_delta=str(delta.get("partial_json") or ""),
                )
            ]
        return []

    if kind == "message_delta":
        delta = payload.get("delta") or {}
        usage_payload = payload.get("usage") or {}
        return [
            StreamChunk(
                finish_reason=str(delta.get("stop_reason") or ""),
                usage=TokenUsage(
                    completion_tokens=int(usage_payload.get("output_tokens") or 0),
                ),
            )
        ]

    if kind == "message_stop":
        return [StreamChunk(done=True)]

    if kind == "error":
        error = payload.get("error") or {}
        raise ProviderNotReachable(f"Anthropic stream error: {error.get('message', '')}")

    return []
