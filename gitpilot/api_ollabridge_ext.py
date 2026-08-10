"""OllaBridge Cloud integration extension for GitPilot API.

This module patches the FastAPI app at import time to add:
- OllaBridge as a first-class LLM provider in settings
- /api/ollabridge/* proxy endpoints (pairing, models, health)

Completely additive - does not modify api.py.
Imported automatically via __init__.py or cli.py startup.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException
from pydantic import BaseModel

from .settings import (
    AppSettings,
    LLMProvider,
    get_settings,
    set_provider,
    update_settings,
)

logger = logging.getLogger(__name__)


# Extended SettingsResponse that includes ollabridge
class SettingsResponseExt(BaseModel):
    provider: LLMProvider
    providers: list[LLMProvider]
    openai: dict
    claude: dict
    watsonx: dict
    ollama: dict
    ollabridge: dict = {}
    openwebui: dict = {}
    custom: dict = {}
    ollabridge_connection_type: str | None = None
    langflow_url: str
    has_langflow_plan_flow: bool


ALL_PROVIDERS = [
    LLMProvider.ollabridge,
    LLMProvider.openai,
    LLMProvider.claude,
    LLMProvider.watsonx,
    LLMProvider.ollama,
    LLMProvider.openwebui,
    LLMProvider.custom,
]


def _build_settings_response(s: AppSettings) -> SettingsResponseExt:
    ollabridge_connection_type = "local"
    if s.ollabridge.api_key:
        ollabridge_connection_type = "api_key"

    # Warn if user included /v1 in base_url
    ob_base = s.ollabridge.base_url or ""
    if ob_base.rstrip("/").endswith("/v1"):
        # The response should carry a warning; we'll handle this in the settings response
        pass

    return SettingsResponseExt(
        provider=s.provider,
        providers=ALL_PROVIDERS,
        openai=s.openai.model_dump(),
        claude=s.claude.model_dump(),
        watsonx=s.watsonx.model_dump(),
        ollama=s.ollama.model_dump(),
        ollabridge=s.ollabridge.model_dump(),
        openwebui=s.openwebui.model_dump(),
        custom=s.custom.model_dump(),
        ollabridge_connection_type=ollabridge_connection_type,
        langflow_url=s.langflow_url,
        has_langflow_plan_flow=bool(s.langflow_plan_flow_id),
    )


def apply_ollabridge_extension(app):
    """Apply OllaBridge integration to the FastAPI app.

    Call this after the app is created but before it starts serving.
    Adds/overrides the settings endpoints to include ollabridge,
    and mounts the ollabridge proxy router.
    """
    from .ollabridge_proxy import router as ollabridge_router

    # Mount proxy routes
    app.include_router(ollabridge_router)
    logger.info("OllaBridge proxy mounted at /api/ollabridge/*")

    # Override settings endpoints to include ollabridge
    @app.get("/api/settings", response_model=SettingsResponseExt)
    async def api_get_settings_ext():
        return _build_settings_response(get_settings())

    class ProviderUpdate(BaseModel):
        provider: LLMProvider

    @app.post("/api/settings/provider", response_model=SettingsResponseExt)
    async def api_set_provider_ext(update: ProviderUpdate):
        s = set_provider(update.provider)
        return _build_settings_response(s)

    @app.put("/api/settings/llm", response_model=SettingsResponseExt)
    async def api_update_llm_settings_ext(updates: dict):
        try:
            s = update_settings(updates)
        except ValueError as exc:
            # Rejected configuration is the caller's mistake, not a crash.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _build_settings_response(s)

    logger.info("OllaBridge settings endpoints registered (overrides original)")
