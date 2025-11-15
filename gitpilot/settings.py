from __future__ import annotations

import enum
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env file if it exists (from project root or current directory)
load_dotenv()

CONFIG_DIR = Path.home() / ".gitpilot"
CONFIG_FILE = CONFIG_DIR / "settings.json"


class LLMProvider(str, enum.Enum):
    openai = "openai"
    claude = "claude"
    watsonx = "watsonx"
    ollama = "ollama"


class OpenAIConfig(BaseModel):
    api_key: str = Field(default="")
    model: str = Field(default="gpt-4o-mini")
    base_url: str = Field(default="")  # Optional: for Azure OpenAI or proxies


class ClaudeConfig(BaseModel):
    api_key: str = Field(default="")
    model: str = Field(default="claude-3-5-sonnet-20241022")
    base_url: str = Field(default="")  # Optional: for proxies


class WatsonxConfig(BaseModel):
    api_key: str = Field(default="")
    project_id: str = Field(default="")
    model_id: str = Field(default="meta-llama/llama-3-1-70b-instruct")
    base_url: str = Field(default="https://api.watsonx.ai/v1")


class OllamaConfig(BaseModel):
    base_url: str = Field(default="http://localhost:11434")
    model: str = Field(default="llama3")


class AppSettings(BaseModel):
    provider: LLMProvider = Field(default=LLMProvider.openai)

    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    watsonx: WatsonxConfig = Field(default_factory=WatsonxConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)

    langflow_url: str = Field(default="http://localhost:7860")
    langflow_api_key: Optional[str] = None
    langflow_plan_flow_id: Optional[str] = None

    @classmethod
    def from_disk(cls) -> "AppSettings":
        """Load settings from disk and merge with environment variables."""
        # Start with defaults or saved settings
        if CONFIG_FILE.exists():
            import json

            data = json.loads(CONFIG_FILE.read_text("utf-8"))
            settings = cls.model_validate(data)
        else:
            settings = cls()

        # Override with environment variables (they take precedence)
        env_provider = os.getenv("GITPILOT_PROVIDER")
        if env_provider:
            try:
                settings.provider = LLMProvider(env_provider.lower())
            except ValueError:
                pass  # Invalid provider, keep existing

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
        if os.getenv("WATSONX_PROJECT_ID"):
            settings.watsonx.project_id = os.getenv("WATSONX_PROJECT_ID")
        if os.getenv("GITPILOT_WATSONX_MODEL"):
            settings.watsonx.model_id = os.getenv("GITPILOT_WATSONX_MODEL")
        if os.getenv("WATSONX_BASE_URL"):
            settings.watsonx.base_url = os.getenv("WATSONX_BASE_URL")

        # Ollama
        if os.getenv("OLLAMA_BASE_URL"):
            settings.ollama.base_url = os.getenv("OLLAMA_BASE_URL")
        if os.getenv("GITPILOT_OLLAMA_MODEL"):
            settings.ollama.model = os.getenv("GITPILOT_OLLAMA_MODEL")

        # LangFlow (optional)
        if os.getenv("GITPILOT_LANGFLOW_URL"):
            settings.langflow_url = os.getenv("GITPILOT_LANGFLOW_URL")
        if os.getenv("GITPILOT_LANGFLOW_API_KEY"):
            settings.langflow_api_key = os.getenv("GITPILOT_LANGFLOW_API_KEY")
        if os.getenv("GITPILOT_LANGFLOW_PLAN_FLOW_ID"):
            settings.langflow_plan_flow_id = os.getenv("GITPILOT_LANGFLOW_PLAN_FLOW_ID")

        return settings

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(self.model_dump_json(indent=2), "utf-8")


_settings = AppSettings.from_disk()


def get_settings() -> AppSettings:
    return _settings


def set_provider(provider: LLMProvider) -> AppSettings:
    _settings.provider = provider
    _settings.save()
    return _settings


def update_settings(updates: dict) -> AppSettings:
    """Update settings with partial or full configuration."""
    global _settings

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

    _settings.save()
    return _settings
