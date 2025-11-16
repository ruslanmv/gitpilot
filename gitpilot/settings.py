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


class GitHubAuthMode(str, enum.Enum):
    """GitHub authentication mode."""
    pat = "pat"  # Personal Access Token
    oauth = "oauth"  # User OAuth
    app = "app"  # GitHub App
    hybrid = "hybrid"  # OAuth + App (recommended for enterprise)


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


class GitHubOAuthConfig(BaseModel):
    """GitHub OAuth configuration for user authentication."""
    client_id: str = Field(default="")
    client_secret: str = Field(default="")


class GitHubAppConfig(BaseModel):
    """GitHub App configuration for repository access."""
    app_id: str = Field(default="")
    installation_id: str = Field(default="")
    private_key_base64: str = Field(default="")  # Base64-encoded private key
    client_id: str = Field(default="")  # For OAuth flow
    client_secret: str = Field(default="")  # For OAuth flow
    slug: str = Field(default="")  # App slug for installation URL


class GitHubConfig(BaseModel):
    """GitHub authentication configuration."""
    auth_mode: GitHubAuthMode = Field(default=GitHubAuthMode.hybrid)
    personal_token: str = Field(default="")  # For PAT mode (fallback)
    oauth: GitHubOAuthConfig = Field(default_factory=GitHubOAuthConfig)
    app: GitHubAppConfig = Field(default_factory=GitHubAppConfig)


class AppSettings(BaseModel):
    provider: LLMProvider = Field(default=LLMProvider.openai)

    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    watsonx: WatsonxConfig = Field(default_factory=WatsonxConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)

    # GitHub configuration
    github: GitHubConfig = Field(default_factory=GitHubConfig)

    # Setup state
    setup_completed: bool = Field(default=False)
    welcome_shown: bool = Field(default=False)

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

        # GitHub authentication
        if os.getenv("GITPILOT_GITHUB_AUTH_MODE"):
            try:
                settings.github.auth_mode = GitHubAuthMode(os.getenv("GITPILOT_GITHUB_AUTH_MODE"))
            except ValueError:
                pass

        # GitHub OAuth
        if os.getenv("GITPILOT_OAUTH_CLIENT_ID"):
            settings.github.oauth.client_id = os.getenv("GITPILOT_OAUTH_CLIENT_ID")
        if os.getenv("GITPILOT_OAUTH_CLIENT_SECRET"):
            settings.github.oauth.client_secret = os.getenv("GITPILOT_OAUTH_CLIENT_SECRET")

        # GitHub App
        if os.getenv("GITPILOT_GH_APP_ID"):
            settings.github.app.app_id = os.getenv("GITPILOT_GH_APP_ID")
        if os.getenv("GITPILOT_GH_APP_INSTALLATION_ID"):
            settings.github.app.installation_id = os.getenv("GITPILOT_GH_APP_INSTALLATION_ID")
        if os.getenv("GITPILOT_GH_APP_PRIVATE_KEY_BASE64"):
            settings.github.app.private_key_base64 = os.getenv("GITPILOT_GH_APP_PRIVATE_KEY_BASE64")
        if os.getenv("GITPILOT_GH_APP_CLIENT_ID"):
            settings.github.app.client_id = os.getenv("GITPILOT_GH_APP_CLIENT_ID")
        if os.getenv("GITPILOT_GH_APP_CLIENT_SECRET"):
            settings.github.app.client_secret = os.getenv("GITPILOT_GH_APP_CLIENT_SECRET")
        if os.getenv("GITPILOT_GH_APP_SLUG"):
            settings.github.app.slug = os.getenv("GITPILOT_GH_APP_SLUG")

        # GitHub PAT (fallback)
        if os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN"):
            settings.github.personal_token = (
                os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN") or ""
            )

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

    # Update GitHub config
    if "github" in updates:
        _settings.github = GitHubConfig(**updates["github"])

    # Update setup state
    if "setup_completed" in updates:
        _settings.setup_completed = updates["setup_completed"]
    if "welcome_shown" in updates:
        _settings.welcome_shown = updates["welcome_shown"]

    _settings.save()
    return _settings


def get_github_oauth_client_id() -> Optional[str]:
    """Get GitHub OAuth client ID from settings or environment."""
    settings = get_settings()
    return settings.github.oauth.client_id or os.getenv("GITPILOT_OAUTH_CLIENT_ID")
