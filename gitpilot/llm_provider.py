from __future__ import annotations

import os

from crewai import LLM

from .settings import LLMProvider, get_settings


def build_llm() -> LLM:
    """Return an initialized CrewAI LLM using the active provider."""
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

        return LLM(
            model=model,
            api_key=api_key,
            base_url=base_url if base_url else None,
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
                "Configure it in Admin / LLM Settings or set ANTHROPIC_API_KEY environment variable."
            )

        # CRITICAL: Set API key as environment variable (required by CrewAI's native Anthropic provider)
        # CrewAI's Anthropic integration checks for this env var internally
        os.environ["ANTHROPIC_API_KEY"] = api_key
        
        # Optional: Set base URL as environment variable if provided
        if base_url:
            os.environ["ANTHROPIC_BASE_URL"] = base_url

        # Ensure model has provider prefix for CrewAI
        if not model.startswith("anthropic/"):
            model = f"anthropic/{model}"

        return LLM(
            model=model,
            api_key=api_key,
            base_url=base_url if base_url else None,
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
                "Configure it in Admin / LLM Settings or set WATSONX_PROJECT_ID environment variable."
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
        return LLM(
            model=model,
            api_key=api_key,
            base_url=base_url,
            project_id=project_id,  # ← CRITICAL: This was missing!
            temperature=0.3,  # Default temperature
            max_tokens=1024,  # Default max tokens
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

        return LLM(model=model, base_url=base_url)

    raise ValueError(f"Unsupported provider: {provider}")