from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx

# LAZY IMPORT: `from crewai import LLM` pulls in litellm, chromadb, lancedb,
# opentelemetry, onnxruntime, and ~180 other packages. Importing it at module
# top-level adds 10-60s to every backend startup (especially on WSL).
# We defer it into build_llm() so it only loads when a chat is actually sent.
if TYPE_CHECKING:
    from crewai import LLM  # noqa: F401 — type hint only

from gitpilot.models import ProviderHealth, ProviderSummary

from .settings import LLMProvider, get_settings
from .reasoning_normalizer import wrap_if_reasoning_model

logger = logging.getLogger(__name__)


def _wrap_llm(llm: Any, model: str) -> Any:
    """Auto-wrap the LLM with ReasoningAwareLLM if the model is a reasoning
    model (deepseek-r1, qwq, marco-o1, r1-distill, etc.).

    This is the single point where reasoning-model normalization is applied.
    For non-reasoning models this is a no-op — the original LLM is returned
    unchanged with zero overhead.

    The wrapper strips <think>...</think> blocks from LLM responses before
    CrewAI's ReAct parser sees them, preventing the common
    "Invalid response from LLM call - None or empty" error.
    """
    return wrap_if_reasoning_model(llm, model)


def build_llm() -> Any:
    """Return an initialized CrewAI LLM using the active provider.

    CrewAI is lazy-imported here to avoid loading ~180 packages (litellm,
    chromadb, lancedb, opentelemetry, onnxruntime, etc.) at server startup.
    First call adds 5-15s; subsequent calls are instant.

    If the active model is a reasoning model (deepseek-r1, qwq, etc.),
    the returned LLM is automatically wrapped with ReasoningAwareLLM
    for CrewAI compatibility. For non-reasoning models, the original
    LLM is returned unchanged.
    """
    # LAZY IMPORT — see module-level comment for rationale
    from crewai import LLM

    settings = get_settings()
    provider = settings.provider

    if provider == LLMProvider.openai:
        # Use settings config if available, otherwise fall back to env vars
        api_key = settings.openai.api_key or os.getenv("OPENAI_API_KEY", "")
        model = settings.openai.model or os.getenv("GITPILOT_OPENAI_MODEL", "gpt-4o-mini")
        base_url = settings.openai.base_url or os.getenv("OPENAI_BASE_URL", "")

        # Validate required credentials
        if not api_key:
            raise ValueError(
                "OpenAI API key is required. "
                "Configure it in Admin / LLM Settings or set OPENAI_API_KEY environment variable."
            )

        # Ensure model has provider prefix for CrewAI
        if not model.startswith("openai/"):
            model = f"openai/{model}"

        return _wrap_llm(
            LLM(
                model=model,
                api_key=api_key,
                base_url=base_url if base_url else None,
            ),
            model,
        )

    if provider == LLMProvider.claude:
        # Use settings config if available, otherwise fall back to env vars
        api_key = settings.claude.api_key or os.getenv("ANTHROPIC_API_KEY", "")
        model = settings.claude.model or os.getenv("GITPILOT_CLAUDE_MODEL", "claude-sonnet-4-5")
        base_url = settings.claude.base_url or os.getenv("ANTHROPIC_BASE_URL", "")

        # Validate required credentials
        if not api_key:
            raise ValueError(
                "Claude API key is required. "
                "Configure it in Admin / LLM Settings or set "
                "ANTHROPIC_API_KEY environment variable."
            )

        # CRITICAL: Set API key as environment variable
        # (required by CrewAI's native Anthropic provider)
        # CrewAI's Anthropic integration checks for this env var internally
        os.environ["ANTHROPIC_API_KEY"] = api_key

        # Optional: Set base URL as environment variable if provided
        if base_url:
            os.environ["ANTHROPIC_BASE_URL"] = base_url

        # Ensure model has provider prefix for CrewAI
        if not model.startswith("anthropic/"):
            model = f"anthropic/{model}"

        return _wrap_llm(
            LLM(
                model=model,
                api_key=api_key,
                base_url=base_url if base_url else None,
            ),
            model,
        )

    if provider == LLMProvider.watsonx:
        # FIXED: Use settings config with proper watsonx.ai integration
        api_key = settings.watsonx.api_key or os.getenv("WATSONX_API_KEY", "")
        project_id = settings.watsonx.project_id or os.getenv("WATSONX_PROJECT_ID", "")
        model = settings.watsonx.model_id or os.getenv(
            "GITPILOT_WATSONX_MODEL",
            "ibm/granite-3-8b-instruct",  # Default model (without prefix)
        )
        base_url = settings.watsonx.base_url or os.getenv(
            "WATSONX_BASE_URL",
            "https://us-south.ml.cloud.ibm.com",  # Default to US South
        )

        # Validate required credentials
        if not api_key:
            raise ValueError(
                "Watsonx API key is required. "
                "Configure it in Admin / LLM Settings or set WATSONX_API_KEY environment variable."
            )
        if not project_id:
            raise ValueError(
                "Watsonx project ID is required. "
                "Configure it in Admin / LLM Settings or set "
                "WATSONX_PROJECT_ID environment variable."
            )

        # CRITICAL: Set project ID as environment variable (required by watsonx.ai SDK)
        os.environ["WATSONX_PROJECT_ID"] = project_id

        # CRITICAL: Also set the base URL as WATSONX_URL (some integrations use this)
        os.environ["WATSONX_URL"] = base_url

        # Ensure model has provider prefix for CrewAI (watsonx/provider/model)
        # Format: watsonx/ibm/granite-3-8b-instruct
        if not model.startswith("watsonx/"):
            model = f"watsonx/{model}"

        # FIXED: Create LLM with project_id parameter (CRITICAL!)
        return _wrap_llm(
            LLM(
                model=model,
                api_key=api_key,
                base_url=base_url,
                project_id=project_id,  # \u2190 CRITICAL: This was missing!
                temperature=0.3,  # Default temperature
                max_tokens=1024,  # Default max tokens
            ),
            model,
        )

    if provider == LLMProvider.ollama:
        # Use settings config if available, otherwise fall back to env vars
        model = settings.ollama.model or os.getenv("GITPILOT_OLLAMA_MODEL", "llama3")
        base_url = settings.ollama.base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

        # Validate required configuration
        if not base_url:
            raise ValueError(
                "Ollama base URL is required. "
                "Configure it in Admin / LLM Settings or set OLLAMA_BASE_URL environment variable."
            )

        # Ensure model has provider prefix for CrewAI
        if not model.startswith("ollama/"):
            model = f"ollama/{model}"

        return _wrap_llm(LLM(model=model, base_url=base_url), model)

    if provider == LLMProvider.ollabridge:
        # OllaBridge / OllaBridge Cloud - OpenAI-compatible API
        model = settings.ollabridge.model or os.getenv("GITPILOT_OLLABRIDGE_MODEL", "qwen2.5:1.5b")
        base_url = settings.ollabridge.base_url or os.getenv("OLLABRIDGE_BASE_URL", "http://localhost:8000")
        api_key = settings.ollabridge.api_key or os.getenv("OLLABRIDGE_API_KEY", "")

        # Validate required configuration
        if not base_url:
            raise ValueError(
                "OllaBridge base URL is required. "
                "Configure it in Admin / LLM Settings or set "
                "OLLABRIDGE_BASE_URL environment variable."
            )

        # OllaBridge exposes an OpenAI-compatible API at /v1/
        # Use the openai/ prefix so CrewAI routes through the OpenAI adapter
        if not model.startswith("openai/"):
            model = f"openai/{model}"

        ollabridge_api_base = f"{base_url.rstrip('/')}/v1"
        ollabridge_key = api_key or "ollabridge"

        # CRITICAL: Set environment variables so litellm/OpenAI client uses
        # the remote OllaBridge URL instead of falling back to localhost.
        # Without this, the openai/ prefix causes litellm to check OPENAI_API_BASE
        # and default to localhost when it's not set.
        os.environ["OPENAI_API_KEY"] = ollabridge_key
        os.environ["OPENAI_API_BASE"] = ollabridge_api_base

        return _wrap_llm(
            LLM(
                model=model,
                api_key=ollabridge_key,
                base_url=ollabridge_api_base,
            ),
            model,
        )

    raise ValueError(f"Unsupported provider: {provider}")


def validate_provider_config(settings) -> tuple[bool, list[str]]:
    """Validate provider configuration and return (is_valid, errors)."""
    errors = []
    provider = settings.provider

    if provider == LLMProvider.openai:
        if not settings.openai.api_key:
            errors.append("OpenAI API key is required")
    elif provider == LLMProvider.claude:
        if not settings.claude.api_key:
            errors.append("Anthropic API key is required")
    elif provider == LLMProvider.watsonx:
        if not settings.watsonx.api_key:
            errors.append("Watsonx API key is required")
        if not settings.watsonx.project_id:
            errors.append("Watsonx project ID is required")
    elif provider == LLMProvider.ollama:
        pass  # Local, always valid
    elif provider == LLMProvider.ollabridge:
        pass  # Local default, always valid

    return (len(errors) == 0, errors)


def get_effective_model(settings) -> str | None:
    """Get the active model name for the current provider."""
    provider = settings.provider
    if provider == LLMProvider.openai:
        return settings.openai.model
    if provider == LLMProvider.claude:
        return settings.claude.model
    if provider == LLMProvider.watsonx:
        return settings.watsonx.model_id
    if provider == LLMProvider.ollama:
        return settings.ollama.model
    if provider == LLMProvider.ollabridge:
        return settings.ollabridge.model
    return None


def _apply_health(summary: ProviderSummary, status_code: int) -> None:
    """Set health and models_available from HTTP status code."""
    ok = status_code == 200
    summary.health = ProviderHealth.ok if ok else ProviderHealth.error
    summary.models_available = ok


async def test_provider_connection(settings) -> ProviderSummary:
    """Test the current provider connection and return status."""
    summary = settings.get_provider_summary()
    provider = settings.provider

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if provider == LLMProvider.openai:
                url = settings.openai.base_url or "https://api.openai.com"
                resp = await client.get(
                    f"{url}/v1/models",
                    headers={"Authorization": f"Bearer {settings.openai.api_key}"},
                )
                _apply_health(summary, resp.status_code)

            elif provider == LLMProvider.claude:
                url = settings.claude.base_url or "https://api.anthropic.com"
                headers = {
                    "x-api-key": settings.claude.api_key,
                    "anthropic-version": "2023-06-01",
                }
                resp = await client.get(f"{url}/v1/models", headers=headers)
                _apply_health(summary, resp.status_code)

            elif provider == LLMProvider.watsonx:
                base = settings.watsonx.base_url or "https://us-south.ml.cloud.ibm.com"
                resp = await client.get(
                    f"{base}/ml/v1/foundation_model_specs",
                    params={"version": "2024-03-14", "limit": "1"},
                    headers={"Authorization": f"Bearer {settings.watsonx.api_key}"},
                )
                _apply_health(summary, resp.status_code)

            elif provider == LLMProvider.ollama:
                base = settings.ollama.base_url or "http://127.0.0.1:11434"
                resp = await client.get(f"{base}/api/tags")
                _apply_health(summary, resp.status_code)

            elif provider == LLMProvider.ollabridge:
                base = settings.ollabridge.base_url or "http://127.0.0.1:8000"
                base = base.rstrip("/")
                if base.endswith("/v1"):
                    base = base[:-3]
                    summary.warning = (
                        "Do not include /v1; GitPilot adds it automatically."
                    )
                api_key = settings.ollabridge.api_key or "ollabridge"
                resp = await client.get(
                    f"{base}/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                _apply_health(summary, resp.status_code)

    except httpx.ConnectError:
        summary.health = ProviderHealth.error
        summary.warning = f"Cannot connect to {provider.value} server"
    except httpx.TimeoutException:
        summary.health = ProviderHealth.warning
        summary.warning = f"Connection to {provider.value} timed out"
    except Exception as e:
        summary.health = ProviderHealth.error
        summary.warning = str(e)

    return summary
