"""Minimal OpenAI-compatible chat client.

Talks to any ``/v1/chat/completions`` endpoint (OllaBridge, vLLM, llama.cpp,
OpenAI itself, …) using ``OPENAI_BASE_URL`` + ``OPENAI_API_KEY``.

Key properties
--------------
* **Offline-safe.** When ``GITPILOT_DEMO_MODE=true`` *or* no endpoint is
  reachable/configured, :meth:`OpenAICompatibleClient.chat` returns a
  deterministic STUB assistant message so dry-runs work fully offline.
* **Generic.** No Hugging Face, no ``HF_TOKEN``.  Only OpenAI-compatible env.
* **Lazy deps.** ``httpx`` is imported lazily; demo mode needs no network deps.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def demo_mode_enabled() -> bool:
    """Return True when GitPilot should run in deterministic offline demo mode."""
    return _truthy(os.getenv("GITPILOT_DEMO_MODE"))


@dataclass
class StubResponse:
    """A deterministic, network-free assistant response.

    Used in demo mode or when the endpoint is unreachable.  The content is
    keyed off the model alias so the stubbed coder produces a plausible diff
    while the reviewer / fast models produce short text.
    """

    content: str
    model: str = "stub"
    usage: dict[str, int] = field(default_factory=lambda: {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    })

    def as_openai(self) -> dict[str, Any]:
        return {
            "id": "stub-completion",
            "object": "chat.completion",
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": self.content},
                    "finish_reason": "stop",
                }
            ],
            "usage": self.usage,
        }


# --------------------------------------------------------------------------- #
# Deterministic stub content
# --------------------------------------------------------------------------- #

_STUB_DIFF = """\
--- /dev/null
+++ b/tests/test_health.py
@@ -0,0 +1,9 @@
+\"\"\"Auto-generated smoke test (GitPilot demo stub).\"\"\"
+
+
+def test_health_ok():
+    \"\"\"Placeholder health check added by the GitPilot repair flow.\"\"\"
+    assert True
+
+
+# Generated in GITPILOT_DEMO_MODE / offline dry-run.
"""


def _stub_for(model: str, messages: list[dict[str, Any]]) -> StubResponse:
    """Return a deterministic stub keyed by model alias / role."""
    alias = (model or "").lower()
    # Coder alias -> emit a small, plausible unified diff.
    if "coder" in alias or "code-coder" in alias:
        return StubResponse(content=_STUB_DIFF, model=model or "code-coder")
    if "review" in alias:
        return StubResponse(
            content=(
                "Review: change is small, additive and constrained to the "
                "allowed test path. No security or secret exposure detected. "
                "Risk: low."
            ),
            model=model or "code-reviewer",
        )
    # Fast / inspector alias -> short context summary.
    last = ""
    for m in reversed(messages or []):
        if m.get("role") == "user":
            last = str(m.get("content", ""))[:200]
            break
    return StubResponse(
        content=(
            "Context inspected (demo stub). The repository appears to lack a "
            "basic health test; a minimal additive test can satisfy the issue. "
            f"Prompt echo: {last}"
        ),
        model=model or "code-fast",
    )


class OpenAICompatibleClient:
    """Tiny chat client for OpenAI-compatible servers.

    Parameters
    ----------
    base_url:
        Overrides ``OPENAI_BASE_URL``.
    api_key:
        Overrides ``OPENAI_API_KEY``.
    timeout:
        Request timeout in seconds.
    demo_mode:
        Force demo/stub mode regardless of env.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
        demo_mode: bool | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or ""
        self.timeout = timeout
        self._forced_demo = demo_mode

    # ------------------------------------------------------------------ #
    # Mode detection
    # ------------------------------------------------------------------ #
    def is_demo(self) -> bool:
        if self._forced_demo is not None:
            return self._forced_demo
        return demo_mode_enabled()

    def is_configured(self) -> bool:
        """True when an endpoint is configured (base_url present)."""
        return bool(self.base_url)

    # ------------------------------------------------------------------ #
    # Chat
    # ------------------------------------------------------------------ #
    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **opts: Any,
    ) -> str:
        """Return the assistant message content.

        Degrades to a deterministic stub when in demo mode, when no endpoint
        is configured, or when the endpoint is unreachable.
        """
        return self.chat_completion(model, messages, **opts)["choices"][0]["message"][
            "content"
        ]

    def chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **opts: Any,
    ) -> dict[str, Any]:
        """Return the full OpenAI-shaped response dict (stub or real)."""
        if self.is_demo() or not self.is_configured():
            return _stub_for(model, messages).as_openai()

        try:
            import httpx  # lazy import
        except Exception:  # pragma: no cover - httpx is a declared dep
            return _stub_for(model, messages).as_openai()

        url = f"{self.base_url}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body: dict[str, Any] = {"model": model, "messages": messages}
        for key in ("temperature", "max_tokens", "top_p", "stop", "seed"):
            if key in opts and opts[key] is not None:
                body[key] = opts[key]

        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            # Unreachable / error -> fail soft to stub so dry-run never breaks.
            return _stub_for(model, messages).as_openai()

        # Validate minimal shape; fall back to stub if malformed.
        if not isinstance(data, dict) or not data.get("choices"):
            return _stub_for(model, messages).as_openai()
        return data

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"OpenAICompatibleClient(base_url={self.base_url!r}, "
            f"configured={self.is_configured()}, demo={self.is_demo()})"
        )


def messages_from(system: str | None, user: str) -> list[dict[str, Any]]:
    """Convenience helper to build a chat message list."""
    msgs: list[dict[str, Any]] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": user})
    return msgs


def dumps(obj: Any) -> str:  # pragma: no cover - trivial
    return json.dumps(obj, indent=2, sort_keys=True)
