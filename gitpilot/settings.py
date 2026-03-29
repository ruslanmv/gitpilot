from __future__ import annotations

import contextlib
import enum
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from gitpilot.models import (
    ProviderConnectionType,
    ProviderName,
    ProviderSummary,
)

# Load .env file if it exists (from project root or current directory)
load_dotenv()

CONFIG_DIR = Path.home() / ".gitpilot"
CONFIG_FILE = CONFIG_DIR / "settings.json"


class LLMProvider(enum.StrEnum):
    openai = "openai"
    claude = "claude"
    watsonx = "watsonx"
    ollama = "ollama"
    ollabridge = "ollabridge"


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
    base_url: str = Field(default="http://localhost:8000")
    model: str = Field(default="qwen2.5:1.5b")
    api_key: str = Field(default="")  # Optional: for authenticated endpoints


class AppSettings(BaseModel):
    provider: LLMProvider = Field(default=LLMProvider.ollabridge)

    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    watsonx: WatsonxConfig = Field(default_factory=WatsonxConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    ollabridge: OllaBridgeConfig = Field(default_factory=OllaBridgeConfig)

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
        """Load settings from disk and merge with environment variables.

        On Vercel or serverless environments, relies entirely on environment variables
        since the filesystem is ephemeral.
        """
        # Start with defaults or saved settings
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text("utf-8"))
            settings = cls.model_validate(data)
        else:
            settings = cls()

        # Override with environment variables (they take precedence)
        env_provider = os.getenv("GITPILOT_PROVIDER")
        if env_provider:
            with contextlib.suppress(ValueError):
                settings.provider = LLMProvider(env_provider.lower())

        # Merge environment variables into provider configs
        # OpenAI
        if os.getenv("OPENAI_API_KEY"):
            settings.openai.api_key = os.getenv("OPENAI_API_KEY")
        if os.getenv("GITPILOT_OPENAI_MODEL"):
            settings.openai.model = os.getenv("GITPILOT_OPENAI_MODEL")
        if os.getenv("OPENAI_BASE_URL"):
            settings.openai.base_url = os.getenv("OPENAI_BASE_URL")

        # Claude
        if os.getenv("ANTHROPIC_API_KEY"):
            settings.claude.api_key = os.getenv("ANTHROPIC_API_KEY")
        if os.getenv("GITPILOT_CLAUDE_MODEL"):
            settings.claude.model = os.getenv("GITPILOT_CLAUDE_MODEL")
        if os.getenv("ANTHROPIC_BASE_URL"):
            settings.claude.base_url = os.getenv("ANTHROPIC_BASE_URL")

        # Watsonx
        if os.getenv("WATSONX_API_KEY"):
            settings.watsonx.api_key = os.getenv("WATSONX_API_KEY")
        if os.getenv("WATSONX_PROJECT_ID") or os.getenv("PROJECT_ID"):
            settings.watsonx.project_id = os.getenv(
                "WATSONX_PROJECT_ID", os.getenv("PROJECT_ID", "")
            )
        if os.getenv("GITPILOT_WATSONX_MODEL"):
            settings.watsonx.model_id = os.getenv("GITPILOT_WATSONX_MODEL")
        if os.getenv("WATSONX_BASE_URL"):
            settings.watsonx.base_url = os.getenv("WATSONX_BASE_URL")

        # Ollama
        if os.getenv("OLLAMA_BASE_URL"):
            settings.ollama.base_url = os.getenv("OLLAMA_BASE_URL")
        if os.getenv("GITPILOT_OLLAMA_MODEL"):
            settings.ollama.model = os.getenv("GITPILOT_OLLAMA_MODEL")

        # OllaBridge / OllaBridge Cloud
        if os.getenv("OLLABRIDGE_BASE_URL"):
            settings.ollabridge.base_url = os.getenv("OLLABRIDGE_BASE_URL")
        if os.getenv("GITPILOT_OLLABRIDGE_MODEL"):
            settings.ollabridge.model = os.getenv("GITPILOT_OLLABRIDGE_MODEL")
        if os.getenv("OLLABRIDGE_API_KEY"):
            settings.ollabridge.api_key = os.getenv("OLLABRIDGE_API_KEY")

        # Lite Mode
        env_lite = os.getenv("GITPILOT_LITE_MODE", "").lower()
        if env_lite in ("1", "true", "yes", "on"):
            settings.lite_mode = True
        elif env_lite in ("0", "false", "no", "off"):
            settings.lite_mode = False

        # LangFlow (optional)
        if os.getenv("GITPILOT_LANGFLOW_URL"):
            settings.langflow_url = os.getenv("GITPILOT_LANGFLOW_URL")
        if os.getenv("GITPILOT_LANGFLOW_API_KEY"):
            settings.langflow_api_key = os.getenv("GITPILOT_LANGFLOW_API_KEY")
        if os.getenv("GITPILOT_LANGFLOW_PLAN_FLOW_ID"):
            settings.langflow_plan_flow_id = os.getenv("GITPILOT_LANGFLOW_PLAN_FLOW_ID")

        return settings

    def save(self) -> None:
        """Save settings to disk. Skipped on Vercel (ephemeral filesystem)."""
        # Skip saving on Vercel - filesystem is ephemeral
        if os.getenv("GITPILOT_VERCEL_DEPLOYMENT") or os.getenv("VERCEL"):
            logging.warning(
                "Settings persistence disabled on Vercel. "
                "Use environment variables for configuration."
            )
            return

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(self.model_dump_json(indent=2), "utf-8")

    # ── Provider introspection helpers ────────────────────

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
        return p == LLMProvider.ollabridge

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
        return None

    def get_provider_summary(self) -> ProviderSummary:
        """Build a :class:`ProviderSummary` for the active provider."""
        p = self.provider

        # --- source detection (.env vs settings) ---
        env_key_map = {
            LLMProvider.openai: "OPENAI_API_KEY",
            LLMProvider.claude: "ANTHROPIC_API_KEY",
            LLMProvider.watsonx: "WATSONX_API_KEY",
            LLMProvider.ollama: "OLLAMA_BASE_URL",
            LLMProvider.ollabridge: "OLLABRIDGE_BASE_URL",
        }
        source: str = (
            ".env" if os.getenv(env_key_map.get(p, "")) else "settings"
        )

        # --- per-provider fields ---
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


def set_provider(provider: LLMProvider) -> AppSettings:
    _settings.provider = provider
    _settings.save()
    return _settings


def update_settings(updates: dict) -> AppSettings:
    """Update settings with partial or full configuration."""
    global _settings  # noqa: PLW0602

    # Update provider if present
    if "provider" in updates:
        _settings.provider = LLMProvider(updates["provider"])

    # Update provider-specific configs
    if "openai" in updates:
        _settings.openai = OpenAIConfig(**updates["openai"])
    if "claude" in updates:
        _settings.claude = ClaudeConfig(**updates["claude"])
    if "watsonx" in updates:
        _settings.watsonx = WatsonxConfig(**updates["watsonx"])
    if "ollama" in updates:
        _settings.ollama = OllamaConfig(**updates["ollama"])
    if "ollabridge" in updates:
        _settings.ollabridge = OllaBridgeConfig(**updates["ollabridge"])

    # Lite mode toggle
    if "lite_mode" in updates:
        _settings.lite_mode = bool(updates["lite_mode"])

    _settings.save()
    return _settings
