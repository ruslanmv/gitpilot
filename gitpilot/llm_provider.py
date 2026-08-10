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

from .settings import (
    LLMProvider,
    OPENWEBUI_API_SUFFIX,
    OPENWEBUI_DEFAULT_BASE_URL,
    get_settings,
    openai_compatible_api_base,
)
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


def _crewai_native_providers() -> tuple[str, ...]:
    """The providers this CrewAI can reach without LiteLLM.

    CrewAI grew native adapters over several releases, and which providers
    are in the list decides whether a call is routed directly or handed to
    the optional LiteLLM fallback. Reading the list is a capability check;
    guessing from the version number is not, because the list changed
    within the 1.x line.

    An empty tuple means this CrewAI predates native adapters entirely — in
    which case LiteLLM is a hard dependency of CrewAI itself and is present.
    """
    try:
        from crewai.llm import SUPPORTED_NATIVE_PROVIDERS  # type: ignore
    except Exception:  # pragma: no cover - very old or patched CrewAI
        return ()
    try:
        return tuple(SUPPORTED_NATIVE_PROVIDERS)
    except TypeError:  # pragma: no cover - defensive
        return ()


def _build_openai_compatible_llm(
    LLM: Any,
    *,
    model: str,
    api_base: str,
    api_key: str,
    **extra: Any,
) -> Any:
    """Build a CrewAI LLM for any endpoint that speaks the OpenAI chat API.

    Ollama, OllaBridge, Open WebUI and user-supplied gateways all serve the
    OpenAI chat-completions shape, so CrewAI's native OpenAI adapter can
    drive every one of them. Getting to that adapter is the hard part.

    Naming the endpoint's own provider does not do it. ``provider="ollama"``
    is honest and, on CrewAI releases where Ollama is not yet a native
    adapter, useless: the provider is not in the native list, so the call
    falls through to the optional LiteLLM fallback and dies as "Fallback to
    LiteLLM is not available" — an error naming neither the provider nor the
    model that was rejected.

    Prefixing the model does not do it either. ``LLM(model="openai/qwen2.5")``
    looks unambiguous and is not: CrewAI maps the prefix to a canonical
    provider and then validates the model part against its own constants and
    naming patterns. Every model these endpoints serve fails that check,
    because none of them are OpenAI models — so this route also lands in the
    LiteLLM fallback.

    ``provider="openai"`` is the one form that skips both the inference and
    the validation, and "openai" has been in the native list for as long as
    the list has existed. The model name is passed through untouched, which
    is what a local or self-hosted endpoint needs.

    ``model`` must be the bare model id, with no provider prefix; callers
    strip theirs. Slashes inside a model id are meaningful to Ollama
    (``hf.co/user/model``) and are left alone.
    """
    if "openai" in _crewai_native_providers():
        return LLM(
            model=model,
            provider="openai",
            base_url=api_base,
            api_key=api_key,
            **extra,
        )

    # A CrewAI with no native adapters at all. LiteLLM is a hard dependency
    # of those releases, so the prefixed form is both available and correct.
    logger.debug(
        "CrewAI exposes no native providers; routing %s through the "
        "prefixed model name",
        model,
    )
    return LLM(model=f"openai/{model}", base_url=api_base, api_key=api_key, **extra)


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

        # Anthropic requires an explicit max_tokens; without it litellm falls
        # back to a low default that truncates large single-file generations
        # (e.g. a full self-contained game). Allow override via env.
        return _wrap_llm(
            LLM(
                model=model,
                api_key=api_key,
                base_url=base_url if base_url else None,
                max_tokens=int(os.getenv("GITPILOT_MAX_TOKENS", "32000")),
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
                max_tokens=int(os.getenv("GITPILOT_MAX_TOKENS", "4096")),
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

        # Ollama serves an OpenAI-compatible API at /v1, so it is driven
        # through the same native adapter as every other endpoint of that
        # shape. See _build_openai_compatible_llm for why the obvious
        # spellings — provider="ollama", or an "ollama/" model prefix — both
        # end up in CrewAI's LiteLLM fallback instead of talking to Ollama.
        model_name = model.removeprefix("ollama/")
        api_base = openai_compatible_api_base(base_url)

        # Deliberately no OPENAI_API_* environment writes here. The adapter
        # is given the base URL and key directly, and those variables are
        # process-wide and outlive the request: seeding OPENAI_API_KEY with
        # the placeholder "ollama" makes a later switch to OpenAI look
        # configured when it is not, so instead of "OpenAI API key is
        # required" the user gets a 401 from api.openai.com for a key that
        # was never theirs. direct_chat.resolve_endpoint reads exactly that
        # variable to decide whether a key is present.

        return _wrap_llm(
            _build_openai_compatible_llm(
                LLM,
                model=model_name,
                api_base=api_base,
                # Ollama ignores it; the OpenAI-shaped client insists on one.
                api_key="ollama",
            ),
            f"ollama/{model_name}",
        )

    if provider == LLMProvider.ollabridge:
        # OllaBridge / OllaBridge Cloud - OpenAI-compatible API
        model = settings.ollabridge.model or os.getenv("GITPILOT_OLLABRIDGE_MODEL", "qwen2.5:1.5b")
        from .settings import OLLABRIDGE_DEFAULT_BASE_URL

        base_url = settings.ollabridge.base_url or os.getenv(
            "OLLABRIDGE_BASE_URL", OLLABRIDGE_DEFAULT_BASE_URL
        )
        api_key = settings.ollabridge.api_key or os.getenv("OLLABRIDGE_API_KEY", "")

        # Validate required configuration
        if not base_url:
            raise ValueError(
                "OllaBridge base URL is required. "
                "Configure it in Admin / LLM Settings or set "
                "OLLABRIDGE_BASE_URL environment variable."
            )

        # OllaBridge exposes an OpenAI-compatible API at /v1/, so it goes
        # through CrewAI's native OpenAI adapter. The model id is the one
        # OllaBridge serves (qwen2.5:1.5b and friends), which is why the
        # provider is named rather than prefixed onto the model — see
        # _build_openai_compatible_llm.
        model_name = model.removeprefix("openai/")
        ollabridge_api_base = f"{base_url.rstrip('/')}/v1"
        ollabridge_key = api_key or "ollabridge"

        # CRITICAL: Set environment variables so litellm/OpenAI client uses
        # the remote OllaBridge URL instead of falling back to localhost.
        # Without this, the openai/ prefix causes litellm to check OPENAI_API_BASE
        # and default to localhost when it's not set.
        os.environ["OPENAI_API_KEY"] = ollabridge_key
        os.environ["OPENAI_API_BASE"] = ollabridge_api_base

        return _wrap_llm(
            _build_openai_compatible_llm(
                LLM,
                model=model_name,
                api_base=ollabridge_api_base,
                api_key=ollabridge_key,
            ),
            f"openai/{model_name}",
        )

    if provider == LLMProvider.openwebui:
        # Open WebUI speaks the OpenAI chat-completions API. Route it through
        # the OpenAI adapter with the instance's own base URL.
        model = settings.openwebui.model or os.getenv("GITPILOT_OPENWEBUI_MODEL", "")
        base_url = settings.openwebui.base_url or os.getenv(
            "OPENWEBUI_BASE_URL", OPENWEBUI_DEFAULT_BASE_URL
        )
        api_key = settings.openwebui.api_key or os.getenv("OPENWEBUI_API_KEY", "")

        if not base_url:
            raise ValueError(
                "Open WebUI base URL is required. "
                "Configure it in Settings → AI Providers or set the "
                "OPENWEBUI_BASE_URL environment variable."
            )
        if not model:
            raise ValueError(
                "Open WebUI model is required. Pick one in Settings → AI Providers."
            )

        api_base = openai_compatible_api_base(base_url, OPENWEBUI_API_SUFFIX)
        # Open WebUI accepts an unauthenticated call only when the instance is
        # open; litellm still wants a non-empty key, so send a placeholder.
        key = api_key or "openwebui"
        model_name = model.removeprefix("openai/")

        os.environ["OPENAI_API_KEY"] = key
        os.environ["OPENAI_API_BASE"] = api_base

        return _wrap_llm(
            _build_openai_compatible_llm(
                LLM, model=model_name, api_base=api_base, api_key=key
            ),
            f"openai/{model_name}",
        )

    if provider == LLMProvider.custom:
        # A user-supplied OpenAI-compatible endpoint. Everything is explicit
        # here — there is no vendor default to fall back on.
        model = settings.custom.model or os.getenv("GITPILOT_CUSTOM_MODEL", "")
        base_url = settings.custom.base_url or os.getenv("GITPILOT_CUSTOM_BASE_URL", "")
        api_key = settings.custom.api_key or os.getenv("GITPILOT_CUSTOM_API_KEY", "")
        headers = dict(settings.custom.headers or {})

        if not base_url:
            raise ValueError(
                "Custom endpoint base URL is required. "
                "Configure it in Settings → AI Providers."
            )
        if not model:
            raise ValueError(
                "Custom endpoint model is required. "
                "Enter the model id your endpoint expects."
            )

        api_base = openai_compatible_api_base(base_url)
        key = api_key or "custom"
        model_name = model.removeprefix("openai/")

        os.environ["OPENAI_API_KEY"] = key
        os.environ["OPENAI_API_BASE"] = api_base

        # Gateways commonly require extra headers for attribution or routing;
        # pass them through untouched.
        extra: dict[str, Any] = {}
        if headers:
            extra["extra_headers"] = headers

        return _wrap_llm(
            _build_openai_compatible_llm(
                LLM, model=model_name, api_base=api_base, api_key=key, **extra
            ),
            f"openai/{model_name}",
        )

    raise ValueError(f"Unsupported provider: {provider}")


# ---------------------------------------------------------------------------
# Batch P2-A — structured system-prompt builder.
#
# This helper is purely additive: it composes a :class:`SystemPayload` with
# cacheable / non-cacheable segments via :mod:`gitpilot.prompt_cache`.  The
# legacy code path (callers that feed a flat ``system`` string into
# ``build_llm()`` results) is untouched — they keep working with no behaviour
# change.  Callers that want the cache markers should adopt this helper
# incrementally.
# ---------------------------------------------------------------------------
def build_system_blocks(
    *,
    base_system: str = "",
    workspace: Any = None,
    mode_slug: Any = None,
    tool_defs: Any = None,
    session_conventions: str = "",
) -> Any:
    """Return the structured system payload for the active provider.

    The active provider is read from settings; the prompt-cache markers
    are emitted only when both ``prompt_cache`` is on and the provider
    is Anthropic.  For every other provider the payload still carries
    the same content and a stable ordering, just without cache markers.
    """
    from .prompt_cache import build_system_blocks as _build  # local import

    try:
        provider = get_settings().provider.value  # type: ignore[union-attr]
    except Exception:
        provider = None

    return _build(
        base_system=base_system,
        workspace=workspace,
        mode_slug=mode_slug,
        tool_defs=tool_defs,
        session_conventions=session_conventions,
        provider=provider,
    )


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

            elif provider == LLMProvider.openwebui:
                base = openai_compatible_api_base(
                    settings.openwebui.base_url or OPENWEBUI_DEFAULT_BASE_URL,
                    OPENWEBUI_API_SUFFIX,
                )
                headers = {}
                if settings.openwebui.api_key:
                    headers["Authorization"] = f"Bearer {settings.openwebui.api_key}"
                resp = await client.get(f"{base}/models", headers=headers)
                _apply_health(summary, resp.status_code)

            elif provider == LLMProvider.custom:
                if not settings.custom.base_url:
                    summary.health = ProviderHealth.error
                    summary.warning = "No endpoint URL configured"
                    return summary
                base = openai_compatible_api_base(settings.custom.base_url)
                headers = dict(settings.custom.headers or {})
                if settings.custom.api_key:
                    headers["Authorization"] = f"Bearer {settings.custom.api_key}"
                resp = await client.get(f"{base}/models", headers=headers)
                # A gateway may expose chat-completions without a model
                # listing; that is a working endpoint, not a failure.
                if resp.status_code in (404, 405):
                    summary.health = ProviderHealth.ok
                    summary.warning = (
                        "Endpoint reachable, but it does not list models. "
                        "Enter the model id manually."
                    )
                else:
                    _apply_health(summary, resp.status_code)

            elif provider == LLMProvider.ollabridge:
                from .settings import OLLABRIDGE_DEFAULT_BASE_URL

                base = settings.ollabridge.base_url or OLLABRIDGE_DEFAULT_BASE_URL
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
