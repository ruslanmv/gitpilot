# gitpilot/agent/providers/__init__.py
"""Provider clients for the agentic loop — Batch V4-B1.

:func:`build_provider` turns GitPilot's settings into a client the loop can call.
Five providers land on :class:`~.openai_compat.OpenAICompatibleProvider`, Claude
on :class:`~.anthropic.AnthropicProvider`, watsonx on
:class:`~.watsonx.WatsonxProvider`.

Endpoint resolution reuses :func:`gitpilot.direct_chat.resolve_endpoint` for the
OpenAI-compatible five rather than restating its per-provider defaults, env-var
fallbacks and base-URL suffix handling — that mapping is already in production
and getting it subtly different here is exactly the kind of divergence this
programme keeps eliminating.  ``resolve_endpoint`` returns ``None`` for Claude
and watsonx, which is where those two branch off.
"""
from __future__ import annotations

from typing import Any, Optional

from .anthropic import AnthropicProvider
from .base import (
    ALLOWED_OPTS,
    ChatProvider,
    MalformedResponse,
    ModelNotFound,
    ProviderAuthError,
    ProviderError,
    ProviderNotReachable,
    ProviderQuotaError,
    ProviderResponse,
    RawToolCall,
    StreamChunk,
    ToolCallAccumulator,
    ToolDef,
    filtered_opts,
)
from .openai_compat import OpenAICompatibleProvider
from .watsonx import WatsonxProvider

__all__ = [
    "ALLOWED_OPTS",
    "AnthropicProvider",
    "ChatProvider",
    "MalformedResponse",
    "ModelNotFound",
    "OpenAICompatibleProvider",
    "ProviderAuthError",
    "ProviderError",
    "ProviderNotReachable",
    "ProviderQuotaError",
    "ProviderResponse",
    "RawToolCall",
    "StreamChunk",
    "ToolCallAccumulator",
    "ToolDef",
    "WatsonxProvider",
    "build_provider",
    "filtered_opts",
]


def build_provider(
    settings: Any = None,
    *,
    model: Optional[str] = None,
    transport: Any = None,
) -> ChatProvider:
    """A provider client for the active configuration.

    *model* overrides the configured model — this is how Batch V4-B2's model
    router reaches a real call instead of only being logged.  *transport* is for
    the record/replay harness.
    """
    from ...settings import LLMProvider, get_settings

    settings = settings or get_settings()
    provider = settings.provider

    if provider == LLMProvider.claude:
        return AnthropicProvider(
            model=model or settings.claude.model,
            api_key=settings.claude.api_key or "",
            base_url=getattr(settings.claude, "base_url", "") or "https://api.anthropic.com",
            transport=transport,
        )

    if provider == LLMProvider.watsonx:
        return WatsonxProvider(
            model=model or settings.watsonx.model_id,
            api_key=settings.watsonx.api_key or "",
            project_id=getattr(settings.watsonx, "project_id", "") or "",
            url=getattr(settings.watsonx, "url", "") or "https://us-south.ml.cloud.ibm.com",
            transport=transport,
        )

    from ...direct_chat import resolve_endpoint

    endpoint = resolve_endpoint(settings)
    if endpoint is None:
        raise ProviderNotReachable(
            f"{provider} is not configured for direct chat. Set its base URL and "
            "model in Settings → AI Providers."
        )

    return OpenAICompatibleProvider(
        base_url=endpoint.base_url,
        model=model or endpoint.model,
        api_key=endpoint.api_key,
        headers=endpoint.headers,
        name=endpoint.provider,
        transport=transport,
    )
