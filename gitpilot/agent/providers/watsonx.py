# gitpilot/agent/providers/watsonx.py
"""watsonx.ai chat client — Batch V4-B1.

watsonx speaks ``POST {url}/ml/v1/text/chat?version=YYYY-MM-DD``, which is close
to the OpenAI shape (``messages``, ``choices[0].message``) but differs in three
ways that matter: the version query parameter is mandatory, the project id
travels in the body, and authentication is an IAM bearer token exchanged from an
API key rather than the key itself.

**Native tool calling is reported as unsupported.**  watsonx does document tools
on this endpoint, but GitPilot has no account to verify the request shape,
streaming event names, or which of the hosted models actually honour them — and
claiming support that turns out to be wrong is worse than not claiming it: the
profile would route watsonx to the NATIVE dialect and every tool call would come
back malformed until the degradation ladder noticed. Reporting ``False`` sends
watsonx to the REACT_TEXT dialect, which works on any model that can follow a
grammar. Flip this the moment someone can test it against a live project.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

import httpx

from ...reasoning_normalizer import visible_answer
from ..contracts import TokenUsage
from .base import (
    MalformedResponse,
    ProviderAuthError,
    ProviderNotReachable,
    ProviderQuotaError,
    ProviderResponse,
    StreamChunk,
    ToolDef,
    filtered_opts,
)

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://us-south.ml.cloud.ibm.com"
DEFAULT_API_VERSION = "2024-05-31"
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_MAX_TOKENS = 4096
IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"


class WatsonxProvider:
    """Chat over watsonx.ai's text/chat endpoint."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        project_id: str = "",
        url: str = DEFAULT_URL,
        api_version: str = DEFAULT_API_VERSION,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.name = "watsonx"
        self.model = model
        self.url = (url or DEFAULT_URL).rstrip("/")
        #: See the module docstring: unverifiable, so not claimed.
        self.supports_native_tools = False
        self.supports_parallel_tool_calls = False
        self.supports_streaming = False
        self._api_key = api_key
        self._project_id = project_id
        self._api_version = api_version
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._transport = transport
        self._bearer: Optional[str] = None

    @property
    def chat_url(self) -> str:
        return f"{self.url}/ml/v1/text/chat?version={self._api_version}"

    def _client(self) -> httpx.AsyncClient:
        kwargs: Dict[str, Any] = {"timeout": self._timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        """Exchange the API key for an IAM bearer token.

        Cached per provider instance: a run makes many calls and the token is
        valid for an hour.  No process-wide state and no environment writes —
        that is the contamination this package exists to avoid.
        """
        if self._bearer:
            return self._bearer
        if not self._api_key:
            raise ProviderAuthError(
                "watsonx needs an API key. Set it in Settings → AI Providers."
            )

        response = await client.post(
            IAM_TOKEN_URL,
            data={
                "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                "apikey": self._api_key,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code >= 400:
            raise ProviderAuthError(
                f"watsonx rejected the API key when requesting a token "
                f"(HTTP {response.status_code})."
            )
        try:
            token = str(response.json().get("access_token") or "")
        except ValueError as exc:
            raise ProviderAuthError("watsonx returned an unreadable token response.") from exc
        if not token:
            raise ProviderAuthError("watsonx returned no access token.")
        self._bearer = token
        return token

    def build_body(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        tools: Optional[Sequence[ToolDef]] = None,
        system: Any = None,
        **opts: Any,
    ) -> Dict[str, Any]:
        merged: List[Dict[str, Any]] = []
        if system:
            merged.append({"role": "system", "content": _flatten(system)})
        merged.extend(dict(message) for message in messages)

        body: Dict[str, Any] = {
            "model_id": self.model,
            "messages": merged,
            "max_tokens": int(opts.pop("max_tokens", None) or self._max_tokens),
        }
        if self._project_id:
            body["project_id"] = self._project_id

        allowed = filtered_opts(opts)
        allowed.pop("max_tokens", None)
        body.update(allowed)
        return body

    async def complete(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        tools: Optional[Sequence[ToolDef]] = None,
        system: Any = None,
        **opts: Any,
    ) -> ProviderResponse:
        if tools:
            logger.debug(
                "watsonx: %d tool definitions ignored — native tool calling is not "
                "claimed for this provider; the REACT_TEXT dialect renders them "
                "into the prompt instead",
                len(tools),
            )

        body = self.build_body(messages, system=system, **opts)

        try:
            async with self._client() as client:
                token = await self._access_token(client)
                response = await client.post(
                    self.chat_url,
                    json=body,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Authorization": f"Bearer {token}",
                    },
                )
        except httpx.TimeoutException as exc:
            raise ProviderNotReachable(
                f"watsonx did not respond within {int(self._timeout)}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderNotReachable(f"Could not reach watsonx at {self.url}. ({exc})") from exc

        if response.status_code in (401, 403):
            raise ProviderAuthError(
                "watsonx rejected the request. Check the API key and project id in "
                "Settings → AI Providers."
            )
        if response.status_code == 429:
            raise ProviderQuotaError("watsonx is rate-limited. Try again shortly.")
        if response.status_code >= 400:
            raise ProviderNotReachable(
                f"watsonx returned HTTP {response.status_code}: {response.text[:300]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise MalformedResponse(
                "watsonx returned a response GitPilot could not read."
            ) from exc

        return self.parse_response(payload)

    def parse_response(self, payload: Dict[str, Any]) -> ProviderResponse:
        if not isinstance(payload, dict):
            raise MalformedResponse("watsonx returned a non-object response.")

        choices = payload.get("choices") or []
        if not choices:
            raise MalformedResponse("watsonx returned no choices.")

        message = (choices[0] or {}).get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            text = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            ).strip()
        else:
            text = str(content or "").strip()

        usage_payload = payload.get("usage") or {}
        return ProviderResponse(
            text=visible_answer(text, self.model),
            finish_reason=str((choices[0] or {}).get("finish_reason") or ""),
            usage=TokenUsage(
                prompt_tokens=int(usage_payload.get("prompt_tokens") or 0),
                completion_tokens=int(usage_payload.get("completion_tokens") or 0),
            ),
        )

    async def stream(
        self,
        messages: Sequence[Dict[str, Any]],
        *,
        tools: Optional[Sequence[ToolDef]] = None,
        system: Any = None,
        **opts: Any,
    ) -> AsyncIterator[StreamChunk]:
        """Fall back to one completion delivered as a single chunk.

        watsonx has a streaming endpoint, but its event names are another thing
        this cannot be verified against a live project, and a wrong guess would
        make streaming silently produce nothing.  One chunk is honest and the
        loop handles it identically.
        """
        response = await self.complete(messages, tools=tools, system=system, **opts)
        if response.text:
            yield StreamChunk(text=response.text)
        yield StreamChunk(done=True, finish_reason=response.finish_reason, usage=response.usage)


def _flatten(system: Any) -> str:
    if isinstance(system, str):
        return system
    render = getattr(system, "to_text", None)
    if callable(render):
        return str(render())
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
