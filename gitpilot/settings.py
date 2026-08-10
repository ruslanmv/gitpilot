from __future__ import annotations

import contextlib
import enum
import json
import logging
import threading
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from gitpilot.models import (
    ProviderConnectionType,
    ProviderName,
    ProviderSummary,
)

# Load .env file if it exists (from project root or current directory)
load_dotenv()

CONFIG_DIR = Path(os.getenv("GITPILOT_CONFIG_DIR", str(Path.home() / ".gitpilot")))
CONFIG_FILE = CONFIG_DIR / "settings.json"

logger = logging.getLogger(__name__)

#: Port GitPilot's own backend binds by default.  Mirrors ``cli.DEFAULT_PORT``;
#: duplicated here so ``settings`` stays free of a CLI import.
GITPILOT_DEFAULT_SERVER_PORT = 8000

#: Where an OllaBridge gateway listens by default.  This is deliberately *not*
#: port 8000: that is GitPilot's own backend, and pointing OllaBridge at it
#: makes model discovery query GitPilot about itself.  See
#: :func:`points_at_gitpilot_server`.
OLLABRIDGE_DEFAULT_BASE_URL = "http://localhost:11435"

#: Where an Open WebUI instance listens by default.
OPENWEBUI_DEFAULT_BASE_URL = "http://localhost:3000"

#: Open WebUI serves its OpenAI-compatible surface under ``/api``, not ``/v1``.
OPENWEBUI_API_SUFFIX = "/api"

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})

#: Suffixes that already identify an OpenAI-compatible API root.
_API_ROOT_SUFFIXES = ("/v1", "/api/v1", "/api", "/openai/v1")


def openai_compatible_api_base(url: str, suffix: str = "/v1") -> str:
    """Normalise a user-entered URL into an OpenAI-compatible API root.

    People paste whatever their gateway's documentation showed them — an
    instance root, an API root, or the full chat-completions path copied from
    a VS Code custom-endpoint config. All three mean the same endpoint, so
    they are reduced to one form rather than failing on the two that were not
    anticipated.

    >>> openai_compatible_api_base("https://x.example.com")
    'https://x.example.com/v1'
    >>> openai_compatible_api_base("https://x.example.com/v1/chat/completions")
    'https://x.example.com/v1'
    >>> openai_compatible_api_base("http://localhost:3000", "/api")
    'http://localhost:3000/api'
    """
    base = (url or "").strip().rstrip("/")
    if not base:
        return ""

    # Drop a pasted operation path — the API root is its parent.
    for tail in ("/chat/completions", "/completions", "/models"):
        if base.endswith(tail):
            base = base[: -len(tail)]
            break

    if base.endswith(_API_ROOT_SUFFIXES):
        return base
    return f"{base}{suffix}"


def gitpilot_server_port() -> int:
    """Port GitPilot's own backend is (or would be) serving on."""
    raw = (os.getenv("GITPILOT_SERVER_PORT") or os.getenv("GITPILOT_PORT") or "").strip()
    try:
        return int(raw)
    except ValueError:
        return GITPILOT_DEFAULT_SERVER_PORT


def points_at_gitpilot_server(url: str) -> bool:
    """True when ``url`` resolves back to GitPilot's own backend.

    A provider base URL that loops back to GitPilot is never what the user
    meant — it turns "list the models" into GitPilot interrogating itself and
    produces a confusing 404 rather than a configuration error.
    """
    from urllib.parse import urlparse

    if not url:
        return False
    try:
        parsed = urlparse(url if "//" in url else f"//{url}", scheme="http")
    except ValueError:
        return False

    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        return False

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return port == gitpilot_server_port()


class LLMProvider(enum.StrEnum):
    openai = "openai"
    claude = "claude"
    watsonx = "watsonx"
    ollama = "ollama"
    ollabridge = "ollabridge"
    openwebui = "openwebui"
    custom = "custom"


class OpenAIConfig(BaseModel):
    api_key: str = Field(default="")
    model: str = Field(default="gpt-4o-mini")
    base_url: str = Field(default="")  # Optional: for Azure OpenAI or proxies


class ClaudeConfig(BaseModel):
    api_key: str = Field(default="")
    model: str = Field(default="claude-sonnet-4-5")
    base_url: str = Field(default="")  # Optional: for proxies


class WatsonxConfig(BaseModel):
    api_key: str = Field(default="")
    project_id: str = Field(default="")
    model_id: str = Field(default="meta-llama/llama-3-3-70b-instruct")
    base_url: str = Field(default="https://api.watsonx.ai/v1")


class OllamaConfig(BaseModel):
    base_url: str = Field(default="http://localhost:11434")
    model: str = Field(default="llama3")


class OllaBridgeConfig(BaseModel):
    base_url: str = Field(default=OLLABRIDGE_DEFAULT_BASE_URL)
    model: str = Field(default="qwen2.5:1.5b")
    api_key: str = Field(default="")  # Optional: for authenticated endpoints


class OpenWebUIConfig(BaseModel):
    """An Open WebUI instance, used through its OpenAI-compatible API.

    Open WebUI serves ``/api/`` for its own UI and an OpenAI-compatible
    surface at ``/api/v1`` (and, on recent builds, ``/v1``). GitPilot talks to
    the OpenAI-compatible surface, so only the instance root is configured
    here; the suffix is appended when the request is made. The API key is the
    one minted under Settings → Account → API keys.
    """

    base_url: str = Field(default="http://localhost:3000")
    model: str = Field(default="")
    api_key: str = Field(default="")


class CustomEndpointConfig(BaseModel):
    """Any OpenAI-compatible chat-completions endpoint.

    This is the escape hatch for gateways GitPilot has no dedicated entry for:
    self-hosted inference, a corporate model gateway, a proxy in front of
    several vendors. It mirrors the shape VS Code's own custom-endpoint
    support uses — a base URL, a key, a model id, and arbitrary request
    headers, which gateways routinely require for user attribution or routing.
    """

    base_url: str = Field(default="")
    model: str = Field(default="")
    api_key: str = Field(default="")
    #: Extra headers sent with every request, e.g. {"x-user": "me@example.com"}.
    headers: dict[str, str] = Field(default_factory=dict)


class SandboxSettings(BaseModel):
    """Where code/commands generated by GitPilot run.

    ``subprocess`` is the safe local default — host subprocess with a cwd jail,
    secret-scrubbing, and the destructive-pattern denylist from
    :mod:`gitpilot.sandbox`.  Switch to ``matrixlab`` to delegate execution to
    a MatrixLab Runner (containerised, ephemeral, resource-limited) for
    enterprise-grade isolation.  ``off`` is the pass-through backend
    (:class:`gitpilot.sandbox.NullSandbox`) — same as subprocess but without
    the cwd jail; intended for local dev only.

    Persisted fields mirror the env vars the sandbox module already honours
    (``GITPILOT_SANDBOX``, ``GITPILOT_MATRIXLAB_URL``, ...).  Env vars still
    take precedence at sandbox-resolution time so deployments can override
    user settings without touching disk.
    """

    backend: str = Field(default="subprocess")
    # MatrixLab maps its container :8000 to host :8765 to avoid clobbering
    # GitPilot's own backend on :8000. Older installs on 8000 continue to
    # work — only the default is shifted.
    matrixlab_url: str = Field(default="http://localhost:8765")
    matrixlab_token: str = Field(default="")
    matrixlab_image: str = Field(default="")
    allow_network: bool = Field(default=False)
    timeout_sec: int = Field(default=120)


class AppSettings(BaseModel):
    provider: LLMProvider = Field(default=LLMProvider.ollabridge)

    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    watsonx: WatsonxConfig = Field(default_factory=WatsonxConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    ollabridge: OllaBridgeConfig = Field(default_factory=OllaBridgeConfig)
    openwebui: OpenWebUIConfig = Field(default_factory=OpenWebUIConfig)
    custom: CustomEndpointConfig = Field(default_factory=CustomEndpointConfig)

    # Sandbox runtime for "Run code" actions in the chat UI.  Defaults to a
    # local subprocess so trying simple snippets works out of the box; switch
    # to MatrixLab from the Settings modal when an enterprise-grade isolated
    # runner is needed.  See :class:`SandboxSettings` for the field shape and
    # :mod:`gitpilot.sandbox` for the resolution precedence.
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)

    # Lite Mode: optimized for small LLMs (< 7B parameters).
    # Uses simplified prompts, single-agent execution, and pre-fetched context
    # instead of multi-agent pipelines with tool-calling.
    # Default is False — user must explicitly opt-in via settings or env var.
    lite_mode: bool = Field(default=False)

    langflow_url: str = Field(default="http://localhost:7860")
    langflow_api_key: str | None = None
    langflow_plan_flow_id: str | None = None

    @classmethod
    def from_disk(cls) -> AppSettings:
        """
        Load settings from disk and merge with environment variables.

        Production behavior:
        - Persisted settings are the primary source of truth for user-editable choices.
        - `.env` is used to bootstrap defaults on first run when no config file exists.
        - Secrets from environment variables are always merged in, so they never need
          to be written to disk from the UI.
        - Test runs can isolate config by setting GITPILOT_CONFIG_DIR.
        """
        config_exists = CONFIG_FILE.exists()

        if config_exists:
            data = json.loads(CONFIG_FILE.read_text("utf-8"))
            settings = cls.model_validate(data)
        else:
            settings = cls()

        # ---------------------------------------------------------------------
        # Secrets and connection settings always merge from environment.
        # This lets operators provide credentials via .env / deployment config
        # without overwriting the user's provider/model choices every restart.
        # ---------------------------------------------------------------------

        # OpenAI
        if os.getenv("OPENAI_API_KEY"):
            settings.openai.api_key = os.getenv("OPENAI_API_KEY", "")
        if os.getenv("OPENAI_BASE_URL"):
            settings.openai.base_url = os.getenv("OPENAI_BASE_URL", "")

        # Claude
        if os.getenv("ANTHROPIC_API_KEY"):
            settings.claude.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if os.getenv("ANTHROPIC_BASE_URL"):
            settings.claude.base_url = os.getenv("ANTHROPIC_BASE_URL", "")

        # Watsonx
        if os.getenv("WATSONX_API_KEY"):
            settings.watsonx.api_key = os.getenv("WATSONX_API_KEY", "")
        if os.getenv("WATSONX_PROJECT_ID") or os.getenv("PROJECT_ID"):
            settings.watsonx.project_id = os.getenv(
                "WATSONX_PROJECT_ID", os.getenv("PROJECT_ID", "")
            )
        if os.getenv("WATSONX_BASE_URL"):
            settings.watsonx.base_url = os.getenv("WATSONX_BASE_URL", "")

        # Ollama
        if os.getenv("OLLAMA_BASE_URL"):
            settings.ollama.base_url = os.getenv("OLLAMA_BASE_URL", "")

        # OllaBridge
        if os.getenv("OLLABRIDGE_BASE_URL"):
            settings.ollabridge.base_url = os.getenv("OLLABRIDGE_BASE_URL", "")
        if os.getenv("OLLABRIDGE_API_KEY"):
            settings.ollabridge.api_key = os.getenv("OLLABRIDGE_API_KEY", "")

        # Open WebUI
        if os.getenv("OPENWEBUI_BASE_URL"):
            settings.openwebui.base_url = os.getenv("OPENWEBUI_BASE_URL", "")
        if os.getenv("OPENWEBUI_API_KEY"):
            settings.openwebui.api_key = os.getenv("OPENWEBUI_API_KEY", "")

        # Custom OpenAI-compatible endpoint
        if os.getenv("GITPILOT_CUSTOM_BASE_URL"):
            settings.custom.base_url = os.getenv("GITPILOT_CUSTOM_BASE_URL", "")
        if os.getenv("GITPILOT_CUSTOM_API_KEY"):
            settings.custom.api_key = os.getenv("GITPILOT_CUSTOM_API_KEY", "")
        if os.getenv("GITPILOT_CUSTOM_MODEL"):
            settings.custom.model = os.getenv("GITPILOT_CUSTOM_MODEL", "")

        # Installs created before the default moved off port 8000 carry a
        # base URL that loops straight back to GitPilot's own backend.  Repair
        # it once, here, rather than letting every model lookup fail.
        if points_at_gitpilot_server(settings.ollabridge.base_url):
            logger.warning(
                "OllaBridge base URL %s points at GitPilot's own server; "
                "resetting it to %s.",
                settings.ollabridge.base_url,
                OLLABRIDGE_DEFAULT_BASE_URL,
            )
            settings.ollabridge.base_url = OLLABRIDGE_DEFAULT_BASE_URL

        # LangFlow (optional)
        if os.getenv("GITPILOT_LANGFLOW_URL"):
            settings.langflow_url = os.getenv("GITPILOT_LANGFLOW_URL", "")
        if os.getenv("GITPILOT_LANGFLOW_API_KEY"):
            settings.langflow_api_key = os.getenv("GITPILOT_LANGFLOW_API_KEY")
        if os.getenv("GITPILOT_LANGFLOW_PLAN_FLOW_ID"):
            settings.langflow_plan_flow_id = os.getenv("GITPILOT_LANGFLOW_PLAN_FLOW_ID")

        # Sandbox runtime — env always wins (same precedence the runtime
        # resolution in :mod:`gitpilot.sandbox` already enforces), so an
        # operator can pin the backend without editing settings.json.
        if os.getenv("GITPILOT_SANDBOX"):
            settings.sandbox.backend = os.environ["GITPILOT_SANDBOX"]
        if os.getenv("GITPILOT_MATRIXLAB_URL"):
            settings.sandbox.matrixlab_url = os.environ["GITPILOT_MATRIXLAB_URL"]
        if os.getenv("GITPILOT_MATRIXLAB_TOKEN"):
            settings.sandbox.matrixlab_token = os.environ["GITPILOT_MATRIXLAB_TOKEN"]
        if os.getenv("GITPILOT_MATRIXLAB_IMAGE"):
            settings.sandbox.matrixlab_image = os.environ["GITPILOT_MATRIXLAB_IMAGE"]

        # Lite mode may be intentionally controlled by env in CI or deployments.
        env_lite = os.getenv("GITPILOT_LITE_MODE", "").strip().lower()
        if env_lite in ("1", "true", "yes", "on"):
            settings.lite_mode = True
        elif env_lite in ("0", "false", "no", "off"):
            settings.lite_mode = False

        # ---------------------------------------------------------------------
        # First-run bootstrap defaults from environment.
        # These only apply when no persisted config exists yet.
        # This allows `.env` to help initial setup, while later VS Code changes
        # remain persistent across restarts.
        # ---------------------------------------------------------------------
        if not config_exists:
            env_provider = os.getenv("GITPILOT_PROVIDER")
            if env_provider:
                with contextlib.suppress(ValueError):
                    settings.provider = LLMProvider(env_provider.lower())

            if os.getenv("GITPILOT_OPENAI_MODEL"):
                settings.openai.model = os.getenv("GITPILOT_OPENAI_MODEL", settings.openai.model)

            if os.getenv("GITPILOT_CLAUDE_MODEL"):
                settings.claude.model = os.getenv("GITPILOT_CLAUDE_MODEL", settings.claude.model)

            if os.getenv("GITPILOT_WATSONX_MODEL"):
                settings.watsonx.model_id = os.getenv(
                    "GITPILOT_WATSONX_MODEL", settings.watsonx.model_id
                )

            if os.getenv("GITPILOT_OLLAMA_MODEL"):
                settings.ollama.model = os.getenv("GITPILOT_OLLAMA_MODEL", settings.ollama.model)

            if os.getenv("GITPILOT_OLLABRIDGE_MODEL"):
                settings.ollabridge.model = os.getenv(
                    "GITPILOT_OLLABRIDGE_MODEL", settings.ollabridge.model
                )

        return settings

    def save(self) -> None:
        """
        Save settings to disk.

        Skipped on Vercel/serverless deployments where the filesystem is ephemeral.
        """
        if os.getenv("GITPILOT_VERCEL_DEPLOYMENT") or os.getenv("VERCEL"):
            logger.warning(
                "Settings persistence disabled on Vercel. "
                "Use environment variables for configuration."
            )
            return

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(self.model_dump_json(indent=2), "utf-8")

    # ── Provider introspection helpers ─────────────────────────────────────

    def is_provider_configured(self) -> bool:
        """Return True if the active provider has the required configuration."""
        p = self.provider
        if p == LLMProvider.openai:
            return bool(self.openai.api_key)
        if p == LLMProvider.claude:
            return bool(self.claude.api_key)
        if p == LLMProvider.watsonx:
            return bool(self.watsonx.api_key and self.watsonx.project_id)
        if p == LLMProvider.ollama:
            return True
        if p == LLMProvider.ollabridge:
            return True
        if p == LLMProvider.openwebui:
            # Open WebUI can be open to the local network without auth, so a
            # reachable URL is the only hard requirement.
            return bool(self.openwebui.base_url)
        if p == LLMProvider.custom:
            # A custom endpoint is only meaningful once it has somewhere to go
            # and something to ask for.
            return bool(self.custom.base_url and self.custom.model)
        return False

    def get_effective_model(self) -> str | None:
        """Return the model string for the active provider."""
        p = self.provider
        if p == LLMProvider.openai:
            return self.openai.model or None
        if p == LLMProvider.claude:
            return self.claude.model or None
        if p == LLMProvider.watsonx:
            return self.watsonx.model_id or None
        if p == LLMProvider.ollama:
            return self.ollama.model or None
        if p == LLMProvider.ollabridge:
            return self.ollabridge.model or None
        if p == LLMProvider.openwebui:
            return self.openwebui.model or None
        if p == LLMProvider.custom:
            return self.custom.model or None
        return None

    def get_provider_summary(self) -> ProviderSummary:
        """Build a ProviderSummary for the active provider."""
        p = self.provider

        # Source detection:
        # - If there is no config file yet, and matching env vars exist, this is a bootstrap/env source.
        # - Otherwise, treat user-facing settings as persistent settings, even if secrets still come from env.
        config_exists = CONFIG_FILE.exists()

        env_key_map = {
            LLMProvider.openai: "OPENAI_API_KEY",
            LLMProvider.claude: "ANTHROPIC_API_KEY",
            LLMProvider.watsonx: "WATSONX_API_KEY",
            LLMProvider.ollama: "OLLAMA_BASE_URL",
            LLMProvider.ollabridge: "OLLABRIDGE_BASE_URL",
            LLMProvider.openwebui: "OPENWEBUI_BASE_URL",
            LLMProvider.custom: "GITPILOT_CUSTOM_BASE_URL",
        }

        source: str = (
            ".env"
            if (not config_exists and os.getenv(env_key_map.get(p, "")))
            else "settings"
        )

        if p == LLMProvider.openai:
            model = self.openai.model
            base_url = self.openai.base_url or None
            conn = ProviderConnectionType.api_key
            has_key = bool(self.openai.api_key)
        elif p == LLMProvider.claude:
            model = self.claude.model
            base_url = self.claude.base_url or None
            conn = ProviderConnectionType.api_key
            has_key = bool(self.claude.api_key)
        elif p == LLMProvider.watsonx:
            model = self.watsonx.model_id
            base_url = self.watsonx.base_url or None
            conn = ProviderConnectionType.api_key
            has_key = bool(self.watsonx.api_key)
        elif p == LLMProvider.ollama:
            model = self.ollama.model
            base_url = self.ollama.base_url or None
            conn = ProviderConnectionType.local
            has_key = False
        elif p == LLMProvider.ollabridge:
            model = self.ollabridge.model
            base_url = self.ollabridge.base_url or None
            conn = ProviderConnectionType.local
            has_key = bool(self.ollabridge.api_key)
        elif p == LLMProvider.openwebui:
            model = self.openwebui.model
            base_url = self.openwebui.base_url or None
            conn = ProviderConnectionType.api_key
            has_key = bool(self.openwebui.api_key)
        elif p == LLMProvider.custom:
            model = self.custom.model
            base_url = self.custom.base_url or None
            conn = ProviderConnectionType.api_key
            has_key = bool(self.custom.api_key)
        else:
            model = None
            base_url = None
            conn = None
            has_key = False

        return ProviderSummary(
            configured=self.is_provider_configured(),
            name=ProviderName(p.value),
            source=source,
            model=model,
            base_url=base_url,
            connection_type=conn,
            has_api_key=has_key,
        )


_settings = AppSettings.from_disk()


def get_settings() -> AppSettings:
    return _settings


def runtime_environment() -> str:
    """Where GitPilot is running: ``"cloud"`` or ``"local"``.

    This drives the onboarding mental model:
      - cloud: no user-owned filesystem, so a "pick a local folder" flow is
        meaningless. GitHub (or another remote source) is the way in.
      - local: the user is on their own machine, so local folder / Git paths
        are first-class and GitHub is optional.

    Detection (first match wins):
      1. ``GITPILOT_RUNTIME`` explicit override (``cloud`` | ``local``)
      2. Known hosted platforms — Hugging Face Spaces (``SPACE_ID`` /
         ``SPACE_HOST``), Vercel (``VERCEL``), or ``GITPILOT_CLOUD``
      3. Otherwise ``local``
    """
    override = (os.getenv("GITPILOT_RUNTIME") or "").strip().lower()
    if override in {"cloud", "local"}:
        return override

    cloud_markers = (
        "GITPILOT_CLOUD",
        "SPACE_ID",        # Hugging Face Spaces
        "SPACE_HOST",      # Hugging Face Spaces
        "VERCEL",
        "GITPILOT_VERCEL_DEPLOYMENT",
    )
    if any(os.getenv(m) for m in cloud_markers):
        return "cloud"
    return "local"


def reload_settings() -> AppSettings:
    """
    Reload settings from disk/environment.

    Useful in tests and in controlled runtime refresh paths.
    """
    global _settings  # noqa: PLW0602
    _settings = AppSettings.from_disk()
    return _settings


_AUTOCONFIG_LAST_RUN_TS: float = 0.0
_AUTOCONFIG_TTL_SECONDS: float = 20.0
_AUTOCONFIG_LOCK = threading.Lock()
_AUTOCONFIG_REFRESHING = False


def autoconfigure_local_provider(force: bool = False) -> AppSettings:
    """
    Prefer a zero-config local provider when it is available.

    Never blocks the caller on the network.
    ---------------------------------------
    This probes Ollama *and* OllaBridge, each with its own socket timeout. On a
    machine where one of them is not listening and the connection hangs rather
    than being refused — WSL2 is the common case — that is ten seconds. It used
    to be spent inline, inside `async def` request handlers, which meant it
    blocked the event loop: every concurrent request to /api/health,
    /api/status and /api/settings finished in the same ~10s, and it happened
    again every time the TTL expired.

    So a stale cache now refreshes in a background thread and the caller gets
    the last known settings straight away. `force=True` still probes inline,
    for explicit bootstrap paths that genuinely need a fresh answer.
    """
    global _settings  # noqa: PLW0602
    global _AUTOCONFIG_LAST_RUN_TS  # noqa: PLW0603
    global _AUTOCONFIG_REFRESHING  # noqa: PLW0603

    import time

    now = time.time()
    if not force and (now - _AUTOCONFIG_LAST_RUN_TS) < _AUTOCONFIG_TTL_SECONDS:
        return _settings

    if not force:
        # Stale: refresh behind the request rather than in front of it.
        with _AUTOCONFIG_LOCK:
            if _AUTOCONFIG_REFRESHING:
                return _settings
            _AUTOCONFIG_REFRESHING = True

        def _refresh() -> None:
            global _AUTOCONFIG_REFRESHING  # noqa: PLW0603
            try:
                _autoconfigure_now()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Background provider autoconfiguration failed: %s", exc)
            finally:
                with _AUTOCONFIG_LOCK:
                    _AUTOCONFIG_REFRESHING = False

        threading.Thread(
            target=_refresh, name="gitpilot-autoconfig", daemon=True
        ).start()
        return _settings

    return _autoconfigure_now()


def _autoconfigure_now() -> AppSettings:
    """The probe itself. Blocking; call it off the event loop."""
    global _settings  # noqa: PLW0602
    global _AUTOCONFIG_LAST_RUN_TS  # noqa: PLW0603

    import time

    now = time.time()

    try:
        from gitpilot.model_catalog import list_models_for_provider

        available: dict[LLMProvider, list[str]] = {}
        for provider in (LLMProvider.ollama, LLMProvider.ollabridge):
            models, _error = list_models_for_provider(provider, _settings)
            if models:
                available[provider] = models

        _AUTOCONFIG_LAST_RUN_TS = now

        if not available:
            return _settings

        current = _settings.provider
        current_model = _settings.get_effective_model()
        changed = False

        # Only auto-switch among local providers.
        auto_switch_allowed = current in (
            LLMProvider.ollama,
            LLMProvider.ollabridge,
        )

        preferred = current
        if auto_switch_allowed:
            if current == LLMProvider.ollabridge and LLMProvider.ollama in available:
                preferred = LLMProvider.ollama
            elif current not in available:
                preferred = (
                    LLMProvider.ollama
                    if LLMProvider.ollama in available
                    else next(iter(available))
                )

        if preferred in available and preferred != current:
            _settings.provider = preferred
            current = preferred
            changed = True

        selected_provider = _settings.provider
        if selected_provider in available:
            models = available[selected_provider]
            if not current_model or current_model not in models:
                default_model = models[0]
                if selected_provider == LLMProvider.ollama:
                    _settings.ollama.model = default_model
                elif selected_provider == LLMProvider.ollabridge:
                    _settings.ollabridge.model = default_model
                changed = True

        if changed:
            _settings.save()

    except Exception as exc:
        logger.debug("Local provider autoconfiguration skipped: %s", exc)
        return _settings

    return _settings

def set_provider(provider: LLMProvider) -> AppSettings:
    global _settings  # noqa: PLW0602
    _settings.provider = provider
    _settings.save()
    return _settings


def _merge_model_config(
    existing: BaseModel,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """
    Merge partial provider updates into an existing config model.

    This prevents API updates like {"model": "..."} from wiping out api_key/base_url.
    """
    current = existing.model_dump()
    current.update(incoming)
    return current


def update_settings(updates: dict[str, Any]) -> AppSettings:
    """
    Update settings with partial or full configuration.

    Important behavior:
    - Partial provider updates are merged into existing configs.
    - User changes made from VS Code persist to disk.
    - Environment secrets are still layered back in on next reload.
    """
    global _settings  # noqa: PLW0602

    if "provider" in updates:
        _settings.provider = LLMProvider(updates["provider"])

    if "openai" in updates:
        merged = _merge_model_config(_settings.openai, updates["openai"])
        _settings.openai = OpenAIConfig(**merged)

    if "claude" in updates:
        merged = _merge_model_config(_settings.claude, updates["claude"])
        _settings.claude = ClaudeConfig(**merged)

    if "watsonx" in updates:
        merged = _merge_model_config(_settings.watsonx, updates["watsonx"])
        _settings.watsonx = WatsonxConfig(**merged)

    if "ollama" in updates:
        merged = _merge_model_config(_settings.ollama, updates["ollama"])
        _settings.ollama = OllamaConfig(**merged)

    if "ollabridge" in updates:
        merged = _merge_model_config(_settings.ollabridge, updates["ollabridge"])
        if points_at_gitpilot_server(merged.get("base_url", "")):
            raise ValueError(
                f"OllaBridge base URL {merged.get('base_url')!r} is GitPilot's own "
                f"server address. Point it at your OllaBridge gateway instead "
                f"(default: {OLLABRIDGE_DEFAULT_BASE_URL})."
            )
        _settings.ollabridge = OllaBridgeConfig(**merged)

    if "openwebui" in updates:
        merged = _merge_model_config(_settings.openwebui, updates["openwebui"])
        _settings.openwebui = OpenWebUIConfig(**merged)

    if "custom" in updates:
        merged = _merge_model_config(_settings.custom, updates["custom"])
        _settings.custom = CustomEndpointConfig(**merged)

    if "sandbox" in updates:
        merged = _merge_model_config(_settings.sandbox, updates["sandbox"])
        _settings.sandbox = SandboxSettings(**merged)

    if "lite_mode" in updates:
        _settings.lite_mode = bool(updates["lite_mode"])

    if "langflow_url" in updates:
        _settings.langflow_url = updates["langflow_url"] or _settings.langflow_url

    if "langflow_api_key" in updates:
        _settings.langflow_api_key = updates["langflow_api_key"]

    if "langflow_plan_flow_id" in updates:
        _settings.langflow_plan_flow_id = updates["langflow_plan_flow_id"]

    _settings.save()
    return _settings