# gitpilot/model_catalog.py
from __future__ import annotations

import os
from datetime import datetime
from typing import List, Tuple, Optional, Dict, Any

import requests

from .settings import AppSettings, LLMProvider, get_settings

# --- Watsonx.ai config (public endpoint, no key needed for IBM-managed models) ---

WATSONX_BASE_URLS = [
    "https://us-south.ml.cloud.ibm.com",
    "https://eu-de.ml.cloud.ibm.com",
    "https://jp-tok.ml.cloud.ibm.com",
    "https://au-syd.ml.cloud.ibm.com",
]

WATSONX_ENDPOINT = "/ml/v1/foundation_model_specs"
WATSONX_PARAMS = {
    "version": "2024-09-16",
    "filters": "!function_embedding,!lifecycle_withdrawn",
}
TODAY = datetime.today().strftime("%Y-%m-%d")


def _is_deprecated_or_withdrawn(lifecycle: List[Dict[str, Any]]) -> bool:
    """Return True if a model lifecycle includes a deprecated/withdrawn item active today."""
    for entry in lifecycle:
        if entry.get("id") in {"deprecated", "withdrawn"} and entry.get("start_date", "") <= TODAY:
            return True
    return False


# --- Provider-specific listing functions --------------------------------------


def _list_openai_models(settings: AppSettings) -> Tuple[List[str], Optional[str]]:
    """
    Use OpenAI /v1/models endpoint to list models available to the configured key.
    Requires OPENAI_API_KEY or settings.openai.api_key.
    """
    api_key = settings.openai.api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return [], "OpenAI API key not configured"

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
    url = f"{base_url.rstrip('/')}/v1/models"

    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        models = sorted({m.get("id", "") for m in data if m.get("id")})
        return models, None
    except Exception as e:
        return [], f"Error listing OpenAI models: {e}"


def _list_claude_models(settings: AppSettings) -> Tuple[List[str], Optional[str]]:
    """
    Use Anthropic /v1/models endpoint to list Claude models available to the key.
    Requires ANTHROPIC_API_KEY or settings.claude.api_key.
    """
    api_key = settings.claude.api_key or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return [], "Claude (Anthropic) API key not configured"

    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    url = f"{base_url.rstrip('/')}/v1/models"
    anthropic_version = os.getenv("ANTHROPIC_VERSION", "2023-06-01")

    try:
        resp = requests.get(
            url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": anthropic_version,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        models = sorted({m.get("id", "") for m in data if m.get("id")})
        return models, None
    except Exception as e:
        return [], f"Error listing Claude models: {e}"


def _list_watsonx_models(settings: AppSettings) -> Tuple[List[str], Optional[str]]:
    """
    List foundation models from Watsonx public specs endpoint.
    No API key required for IBM-managed models.
    Returns a unique sorted list of model_id's across major regions.
    """
    all_models = set()

    for base in WATSONX_BASE_URLS:
        url = f"{base}{WATSONX_ENDPOINT}"
        try:
            resp = requests.get(url, params=WATSONX_PARAMS, timeout=10)
            resp.raise_for_status()
            resources = resp.json().get("resources", [])
            for m in resources:
                if _is_deprecated_or_withdrawn(m.get("lifecycle", [])):
                    continue
                model_id = m.get("model_id")
                if model_id:
                    all_models.add(model_id)
        except Exception:
            # Just skip this region on error
            continue

    if not all_models:
        return [], "No Watsonx models found (public specs call failed for all regions?)"

    return sorted(all_models), None


def _list_ollama_models(settings: AppSettings) -> Tuple[List[str], Optional[str]]:
    """
    List models from a local/remote Ollama server via /api/tags.
    """
    base_url = getattr(settings.ollama, "base_url", None) or os.getenv(
        "OLLAMA_BASE_URL", "http://localhost:11434"
    )
    url = f"{base_url.rstrip('/')}/api/tags"

    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json().get("models", [])
        models = sorted({m.get("name", "") for m in data if m.get("name")})
        return models, None
    except Exception as e:
        return [], f"Error listing Ollama models from {url}: {e}"


# --- Public helper ------------------------------------------------------------


def list_models_for_provider(
    provider: LLMProvider,
    settings: Optional[AppSettings] = None,
) -> Tuple[List[str], Optional[str]]:
    """
    Return (models, error) for a given provider.

    models: list of strings (model IDs / names)
    error: human-readable error if something went wrong, otherwise None
    """
    if settings is None:
        settings = get_settings()

    if provider == LLMProvider.openai:
        return _list_openai_models(settings)
    if provider == LLMProvider.claude:
        return _list_claude_models(settings)
    if provider == LLMProvider.watsonx:
        return _list_watsonx_models(settings)
    if provider == LLMProvider.ollama:
        return _list_ollama_models(settings)

    return [], f"Unsupported provider: {provider}"
