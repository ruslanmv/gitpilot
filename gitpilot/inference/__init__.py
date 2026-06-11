"""GitPilot inference clients.

GENERIC, provider-neutral access to an OpenAI-compatible chat endpoint
(OllaBridge or any ``/v1/chat/completions`` server).

This package NEVER reads ``HF_TOKEN``.  It only ever reads
``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` (plus the optional model-alias env
vars documented in ``.env.template``).
"""

from .ollabridge_client import OllaBridgeClient
from .openai_compatible_client import OpenAICompatibleClient, StubResponse

__all__ = [
    "OpenAICompatibleClient",
    "OllaBridgeClient",
    "StubResponse",
]
