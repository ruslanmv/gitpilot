"""Chat with the configured provider over plain HTTP.

Why this exists
---------------
Every chat path used to go through :func:`gitpilot.llm_provider.build_llm`,
which lazy-imports CrewAI — and CrewAI pulls in LiteLLM plus ~180 other
packages. That is the right machinery for a multi-agent run against a GitHub
repository. It is the wrong machinery for answering "explain this project" in
a folder-only session, and it meant a working, reachable Ollama could not
answer a single question unless the whole agent stack was installed and
importable.

The startup banner made that worse rather than better: it validated the
provider *configuration* and printed ``LLM Provider ✅ OLLAMA``, then the first
message failed on an import the banner had never looked at. A product that
says it is ready has to be ready for the thing it is for.

So: five of the seven providers speak the OpenAI ``/v1/chat/completions``
shape, which needs nothing but ``httpx``. Those are answered here directly.
The two that do not — Claude and watsonx — still go through CrewAI, and if it
is missing they now say so in a sentence you can act on.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from .settings import (
    LLMProvider,
    OPENWEBUI_API_SUFFIX,
    OPENWEBUI_DEFAULT_BASE_URL,
    get_settings,
    openai_compatible_api_base,
)

logger = logging.getLogger(__name__)

#: Long enough for a large local model on cold cache, short enough that a
#: wedged endpoint does not hang the request forever.
DEFAULT_TIMEOUT_SECONDS = 180.0


class ProviderNotReachable(RuntimeError):
    """The endpoint is configured but did not answer.

    Carries a sentence a user can act on rather than a stack trace, because
    this is the error they are most likely to see and least able to debug.
    """


@dataclass
class ChatEndpoint:
    """Everything needed to POST one chat completion."""

    provider: str
    base_url: str
    model: str
    api_key: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def resolve_endpoint(settings: Any = None) -> ChatEndpoint | None:
    """The active provider as an OpenAI-compatible endpoint, or None.

    None means "this provider does not speak that shape" — Claude and watsonx
    — not "not configured". Callers use that to decide whether the CrewAI path
    is genuinely required.
    """
    settings = settings or get_settings()
    provider = settings.provider

    if provider == LLMProvider.ollama:
        base = settings.ollama.base_url or _env("OLLAMA_BASE_URL", "http://localhost:11434")
        model = settings.ollama.model or _env("GITPILOT_OLLAMA_MODEL", "llama3")
        # Ollama serves an OpenAI-compatible surface at /v1 alongside its own
        # native API; the /v1 one is what every other provider here speaks.
        return ChatEndpoint(
            provider="ollama",
            base_url=openai_compatible_api_base(base),
            model=model,
            # Ollama ignores the key but rejects a missing Authorization on
            # some proxies in front of it.
            api_key="ollama",
        )

    if provider == LLMProvider.ollabridge:
        from .settings import OLLABRIDGE_DEFAULT_BASE_URL

        base = settings.ollabridge.base_url or _env(
            "OLLABRIDGE_BASE_URL", OLLABRIDGE_DEFAULT_BASE_URL
        )
        return ChatEndpoint(
            provider="ollabridge",
            base_url=openai_compatible_api_base(base),
            model=settings.ollabridge.model or _env("GITPILOT_OLLABRIDGE_MODEL", "qwen2.5:1.5b"),
            api_key=settings.ollabridge.api_key or _env("OLLABRIDGE_API_KEY") or "ollabridge",
        )

    if provider == LLMProvider.openwebui:
        base = settings.openwebui.base_url or _env(
            "OPENWEBUI_BASE_URL", OPENWEBUI_DEFAULT_BASE_URL
        )
        return ChatEndpoint(
            provider="openwebui",
            base_url=openai_compatible_api_base(base, OPENWEBUI_API_SUFFIX),
            model=settings.openwebui.model or _env("GITPILOT_OPENWEBUI_MODEL"),
            api_key=settings.openwebui.api_key or _env("OPENWEBUI_API_KEY"),
        )

    if provider == LLMProvider.custom:
        base = settings.custom.base_url or _env("GITPILOT_CUSTOM_BASE_URL")
        if not base:
            return None
        return ChatEndpoint(
            provider="custom",
            base_url=openai_compatible_api_base(base),
            model=settings.custom.model or _env("GITPILOT_CUSTOM_MODEL"),
            api_key=settings.custom.api_key or _env("GITPILOT_CUSTOM_API_KEY"),
            headers=dict(getattr(settings.custom, "headers", {}) or {}),
        )

    if provider == LLMProvider.openai:
        base = settings.openai.base_url or _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
        key = settings.openai.api_key or _env("OPENAI_API_KEY")
        if not key:
            return None  # No key: let the CrewAI path raise its own clear error.
        return ChatEndpoint(
            provider="openai",
            base_url=openai_compatible_api_base(base),
            model=settings.openai.model or _env("GITPILOT_OPENAI_MODEL", "gpt-4o-mini"),
            api_key=key,
        )

    # Claude and watsonx have their own wire formats.
    return None


def _extract(payload: dict[str, Any]) -> str:
    """The assistant text out of an OpenAI-shaped response.

    Gateways vary in the corners, so this reads defensively rather than
    indexing straight into ``choices[0].message.content``.
    """
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")

    # Some gateways return content as a list of parts, the way the
    # multi-modal APIs do, even for a plain text answer.
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        ).strip()

    if isinstance(content, str) and content.strip():
        return content.strip()

    # A reasoning model that spent its whole budget thinking leaves `content`
    # empty and the substance in a sibling field — Ollama 0.32 calls it
    # `reasoning`, others `reasoning_content`. Reading only `content` there
    # reports "returned an empty response", which sends the user looking for
    # a broken provider when the model simply never stopped thinking.
    for key in ("reasoning", "reasoning_content"):
        thinking = message.get(key)
        if isinstance(thinking, str) and thinking.strip():
            return thinking.strip()

    if isinstance(content, str):
        return content.strip()

    # A couple of local servers still answer in the legacy completion shape.
    return str(choices[0].get("text") or "").strip()


def _without_reasoning(answer: str, model: str) -> str:
    """Drop a reasoning model's private thinking from the visible answer.

    deepseek-r1, QwQ and friends wrap their scratchpad in ``<think>`` tags
    and put the reply after it. The CrewAI path has always known this —
    build_llm() wraps those models — but this path does not go through
    CrewAI, so its callers were shown the raw output: a wall of reasoning
    with the actual answer somewhere at the end, or nothing they would
    recognise as an answer at all.

    A model that spends its whole budget thinking leaves nothing after the
    close tag. Returning "" there would surface as "returned an empty
    response", which is both unhelpful and untrue — so the raw text is
    kept instead. Partial output beats a wrong diagnosis.
    """
    if not answer:
        return answer
    try:
        from .reasoning_normalizer import is_reasoning_model, strip_reasoning_content
    except Exception:  # pragma: no cover - defensive
        return answer

    # Tags rather than the model name decide it. A local `ollama create`
    # name need not mention deepseek, and a model that emitted no reasoning
    # is unaffected either way.
    if "<think" not in answer and not is_reasoning_model(model or ""):
        return answer

    cleaned, reasoning = strip_reasoning_content(answer)
    cleaned = (cleaned or "").strip()
    if not cleaned:
        logger.debug(
            "%s produced only reasoning (%d chars); showing it unmodified "
            "rather than reporting an empty response",
            model,
            len(reasoning or ""),
        )
        return answer
    return cleaned


async def chat(
    messages: list[dict[str, str]],
    endpoint: ChatEndpoint | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """One chat completion against the configured provider.

    Raises :class:`ProviderNotReachable` with an actionable message when the
    endpoint cannot be reached or answers with an error.
    """
    endpoint = endpoint or resolve_endpoint()
    if endpoint is None:
        raise ProviderNotReachable(
            "The active provider does not expose an OpenAI-compatible API."
        )

    if not endpoint.model:
        raise ProviderNotReachable(
            f"No model is selected for {endpoint.provider}. "
            "Pick one in GitPilot Settings → AI Providers."
        )

    headers = {"Content-Type": "application/json", **endpoint.headers}
    if endpoint.api_key:
        headers.setdefault("Authorization", f"Bearer {endpoint.api_key}")

    body = {"model": endpoint.model, "messages": messages, "stream": False}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(endpoint.url, json=body, headers=headers)
    except httpx.TimeoutException as e:
        raise ProviderNotReachable(
            f"{endpoint.provider} did not respond within {int(timeout)}s at "
            f"{endpoint.base_url}. A large model on a cold cache can exceed this — "
            "try again, or pick a smaller model in Settings."
        ) from e
    except httpx.HTTPError as e:
        raise ProviderNotReachable(
            f"Could not reach {endpoint.provider} at {endpoint.base_url}. "
            f"Check that it is running and the URL is right in Settings. ({e})"
        ) from e

    if response.status_code == 404:
        raise ProviderNotReachable(
            f"{endpoint.provider} has no model named {endpoint.model!r} at "
            f"{endpoint.base_url}. Pull it first, or pick another in Settings."
        )

    if response.status_code >= 400:
        raise ProviderNotReachable(
            f"{endpoint.provider} returned HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    try:
        answer = _extract(response.json())
    except ValueError as e:
        raise ProviderNotReachable(
            f"{endpoint.provider} returned a response GitPilot could not read."
        ) from e

    answer = _without_reasoning(answer, endpoint.model)

    if not answer:
        raise ProviderNotReachable(
            f"{endpoint.provider} returned an empty response. This is common with "
            "very small models — try a larger one in Settings."
        )

    return answer


def describe_runtime() -> dict[str, Any]:
    """Whether chat can actually run, for the doctor and the startup banner.

    Configuration alone was never the question worth answering: a provider can
    be perfectly configured and still be unable to answer because the runtime
    it needs is not installed.
    """
    settings = get_settings()
    endpoint = resolve_endpoint(settings)

    if endpoint is not None:
        return {
            "provider": str(settings.provider),
            "path": "direct",
            "ready": bool(endpoint.model),
            "detail": ""
            if endpoint.model
            else f"No model selected for {endpoint.provider}.",
            "endpoint": endpoint.base_url,
        }

    import importlib.util

    have_crewai = importlib.util.find_spec("crewai") is not None
    return {
        "provider": str(settings.provider),
        "path": "crewai",
        "ready": have_crewai,
        "detail": ""
        if have_crewai
        else (
            f"{settings.provider} needs the agent runtime, which is not installed. "
            "Install it with: pip install 'gitcopilot[agents]'"
        ),
        "endpoint": "",
    }
