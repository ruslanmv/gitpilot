"""OllaBridge / OpenAI-compatible client tests (offline, demo mode)."""

from __future__ import annotations

from gitpilot.inference.ollabridge_client import OllaBridgeClient
from gitpilot.inference.openai_compatible_client import (
    OpenAICompatibleClient,
    messages_from,
)


def test_stub_when_demo_mode():
    client = OpenAICompatibleClient(demo_mode=True)
    out = client.chat("code-fast", messages_from("sys", "hello"))
    assert isinstance(out, str)
    assert out  # non-empty deterministic stub


def test_stub_when_no_endpoint():
    # No base_url configured and not demo => still degrades to stub.
    client = OpenAICompatibleClient(base_url="", api_key="", demo_mode=False)
    assert client.is_configured() is False
    out = client.chat("code-fast", messages_from(None, "hi"))
    assert isinstance(out, str) and out


def test_coder_returns_diff_stub():
    ob = OllaBridgeClient(demo_mode=True)
    diff = ob.code("fix it")
    assert "+++ b/tests/test_health.py" in diff
    assert diff.lstrip().startswith("---")


def test_reviewer_stub_mentions_risk():
    ob = OllaBridgeClient(demo_mode=True)
    review = ob.review("review this diff")
    assert "risk" in review.lower()


def test_fast_stub_is_text():
    ob = OllaBridgeClient(demo_mode=True)
    assert ob.is_demo() is True
    summary = ob.fast("inspect the repo")
    assert isinstance(summary, str) and summary


def test_full_response_shape():
    client = OpenAICompatibleClient(demo_mode=True)
    resp = client.chat_completion("code-coder", messages_from(None, "x"))
    assert resp["choices"][0]["message"]["role"] == "assistant"
    assert "usage" in resp
