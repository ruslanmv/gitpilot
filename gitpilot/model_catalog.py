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


def _list_ollabridge_models(settings: AppSettings) -> Tuple[List[str], Optional[str]]:
    """
    List models from an OllaBridge / OllaBridge Cloud instance via /v1/models.
    Uses the OpenAI-compatible endpoint.
    """
    from .settings import (
        OLLABRIDGE_DEFAULT_BASE_URL,
        points_at_gitpilot_server,
    )

    base_url = getattr(settings.ollabridge, "base_url", None) or os.getenv(
        "OLLABRIDGE_BASE_URL", OLLABRIDGE_DEFAULT_BASE_URL
    )
    if points_at_gitpilot_server(base_url):
        return [], (
            f"OllaBridge base URL {base_url} is GitPilot's own server address. "
            f"Point it at your OllaBridge gateway (default: "
            f"{OLLABRIDGE_DEFAULT_BASE_URL})."
        )

    api_key = getattr(settings.ollabridge, "api_key", None) or os.getenv("OLLABRIDGE_API_KEY", "")
    url = f"{base_url.rstrip('/')}/v1/models"

    headers: Dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        models = sorted({m.get("id", "") for m in data if m.get("id")})
        return models, None
    except Exception as e:
        return [], f"Error listing OllaBridge models from {url}: {e}"


def _list_openai_compatible_models(
    api_base: str,
    api_key: str,
    headers: Optional[Dict[str, str]] = None,
    label: str = "endpoint",
) -> Tuple[List[str], Optional[str]]:
    """List models from any OpenAI-compatible ``/models`` endpoint."""
    if not api_base:
        return [], f"No {label} URL configured"

    request_headers: Dict[str, str] = dict(headers or {})
    if api_key:
        request_headers["Authorization"] = f"Bearer {api_key}"

    url = f"{api_base}/models"
    try:
        resp = requests.get(url, headers=request_headers, timeout=10)
        if resp.status_code in (404, 405):
            # Plenty of gateways serve chat-completions without a catalogue.
            # That is a working endpoint, so say what to do rather than
            # reporting it as broken.
            return [], (
                f"{label} at {api_base} does not list models. "
                "Enter the model id manually."
            )
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", payload if isinstance(payload, list) else [])
        models = sorted(
            {
                m.get("id") or m.get("name", "")
                for m in data
                if isinstance(m, dict) and (m.get("id") or m.get("name"))
            }
        )
        return models, None
    except Exception as e:
        return [], f"Error listing models from {url}: {e}"


def _list_openwebui_models(settings: AppSettings) -> Tuple[List[str], Optional[str]]:
    """List models exposed by an Open WebUI instance."""
    from .settings import (
        OPENWEBUI_API_SUFFIX,
        OPENWEBUI_DEFAULT_BASE_URL,
        openai_compatible_api_base,
    )

    base_url = getattr(settings.openwebui, "base_url", None) or os.getenv(
        "OPENWEBUI_BASE_URL", OPENWEBUI_DEFAULT_BASE_URL
    )
    api_key = getattr(settings.openwebui, "api_key", None) or os.getenv(
        "OPENWEBUI_API_KEY", ""
    )
    return _list_openai_compatible_models(
        openai_compatible_api_base(base_url, OPENWEBUI_API_SUFFIX),
        api_key,
        label="Open WebUI",
    )


#: Well-known paths some gateways publish their model catalogue under.
#:
#: A published catalogue is richer than ``/v1/models`` — it carries display
#: names and context limits — and is often readable without a key, so it is
#: tried first. Vendor-neutral: any gateway serving one of these documents
#: works.
WELLKNOWN_CATALOG_PATHS = (
    "/.well-known/opencode",
    "/.well-known/models",
)


def _models_from_catalog_document(payload: Any) -> List[str]:
    """Pull model ids out of a published catalogue document.

    Two shapes are recognised, because gateways publish both:

    * a provider map — ``{"provider": {"<id>": {"models": {"<model-id>": …}}}}``
    * a flat listing — ``{"models": [...]}`` or ``{"data": [{"id": …}]}``
    """
    ids: set[str] = set()

    if isinstance(payload, dict):
        providers = payload.get("provider")
        if isinstance(providers, dict):
            for block in providers.values():
                models = (block or {}).get("models") if isinstance(block, dict) else None
                if isinstance(models, dict):
                    ids.update(str(k) for k in models if k)
                elif isinstance(models, list):
                    ids.update(_ids_from_list(models))

        for key in ("models", "data"):
            entries = payload.get(key)
            if isinstance(entries, dict):
                ids.update(str(k) for k in entries if k)
            elif isinstance(entries, list):
                ids.update(_ids_from_list(entries))

    elif isinstance(payload, list):
        ids.update(_ids_from_list(payload))

    return sorted(ids)


def _ids_from_list(entries: List[Any]) -> List[str]:
    out = []
    for entry in entries:
        if isinstance(entry, str) and entry:
            out.append(entry)
        elif isinstance(entry, dict):
            value = entry.get("id") or entry.get("name")
            if value:
                out.append(str(value))
    return out


def _origin(url: str) -> str:
    """Scheme and authority of ``url``, without any path."""
    from urllib.parse import urlparse

    parsed = urlparse(url if "//" in url else f"//{url}", scheme="http")
    if not parsed.hostname:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _list_wellknown_models(
    root: str,
    api_key: str,
    headers: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Try each well-known catalogue path; return the first non-empty result."""
    if not root:
        return []

    request_headers: Dict[str, str] = dict(headers or {})
    if api_key:
        request_headers["Authorization"] = f"Bearer {api_key}"

    for path in WELLKNOWN_CATALOG_PATHS:
        try:
            resp = requests.get(f"{root}{path}", headers=request_headers, timeout=8)
            if resp.status_code != 200:
                continue
            models = _models_from_catalog_document(resp.json())
            if models:
                return models
        except Exception:
            # A gateway that publishes nothing here is the common case, not an
            # error — fall through to the OpenAI-compatible listing.
            continue
    return []


def _list_custom_models(settings: AppSettings) -> Tuple[List[str], Optional[str]]:
    """List models from a user-supplied OpenAI-compatible endpoint.

    Discovery is best-effort and ordered by richness: a published catalogue
    first, then the OpenAI-compatible ``/models`` listing. Either way the user
    can always type a model id by hand.
    """
    from .settings import openai_compatible_api_base

    base_url = getattr(settings.custom, "base_url", None) or os.getenv(
        "GITPILOT_CUSTOM_BASE_URL", ""
    )
    api_key = getattr(settings.custom, "api_key", None) or os.getenv(
        "GITPILOT_CUSTOM_API_KEY", ""
    )
    headers = dict(getattr(settings.custom, "headers", None) or {})

    if not base_url:
        return [], "No custom endpoint URL configured"

    published = _list_wellknown_models(_origin(base_url), api_key, headers)
    if published:
        return published, None

    return _list_openai_compatible_models(
        openai_compatible_api_base(base_url),
        api_key,
        headers=headers,
        label="Custom endpoint",
    )


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
    if provider == LLMProvider.ollabridge:
        return _list_ollabridge_models(settings)
    if provider == LLMProvider.openwebui:
        return _list_openwebui_models(settings)
    if provider == LLMProvider.custom:
        return _list_custom_models(settings)

    return [], f"Unsupported provider: {provider}"
