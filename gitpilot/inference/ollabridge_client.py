"""Thin OllaBridge wrapper over the generic OpenAI-compatible client.

Exposes model-alias helpers keyed by env:

* ``GITPILOT_MODEL_FAST``     (default ``code-fast``)
* ``GITPILOT_MODEL_CODER``    (default ``code-coder``)
* ``GITPILOT_MODEL_REVIEWER`` (default ``code-reviewer``)

OllaBridge is OpenAI-compatible, so this is intentionally a very thin layer.
It NEVER reads ``HF_TOKEN`` — only ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import os
from typing import Any

from .openai_compatible_client import OpenAICompatibleClient, messages_from


def model_fast() -> str:
    return os.getenv("GITPILOT_MODEL_FAST", "code-fast")


def model_coder() -> str:
    return os.getenv("GITPILOT_MODEL_CODER", "code-coder")


def model_reviewer() -> str:
    return os.getenv("GITPILOT_MODEL_REVIEWER", "code-reviewer")


class OllaBridgeClient:
    """High-level helper exposing the three GitPilot model roles."""

    def __init__(
        self,
        client: OpenAICompatibleClient | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        demo_mode: bool | None = None,
    ) -> None:
        self.client = client or OpenAICompatibleClient(
            base_url=base_url, api_key=api_key, demo_mode=demo_mode
        )

    # ------------------------------------------------------------------ #
    # Role helpers
    # ------------------------------------------------------------------ #
    def fast(self, user: str, system: str | None = None, **opts: Any) -> str:
        """Quick context inspection via the ``code-fast`` alias."""
        return self.client.chat(model_fast(), messages_from(system, user), **opts)

    def code(self, user: str, system: str | None = None, **opts: Any) -> str:
        """Patch generation via the ``code-coder`` alias."""
        return self.client.chat(model_coder(), messages_from(system, user), **opts)

    def review(self, user: str, system: str | None = None, **opts: Any) -> str:
        """Patch review via the ``code-reviewer`` alias."""
        return self.client.chat(model_reviewer(), messages_from(system, user), **opts)

    # Generic passthrough (caller picks the model/alias).
    def chat(self, model: str, messages: list[dict[str, Any]], **opts: Any) -> str:
        return self.client.chat(model, messages, **opts)

    def is_demo(self) -> bool:
        return self.client.is_demo()

    def is_configured(self) -> bool:
        return self.client.is_configured()
