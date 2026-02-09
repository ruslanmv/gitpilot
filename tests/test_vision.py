"""Tests for gitpilot.vision module."""
from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gitpilot.vision import (
    MAX_IMAGE_SIZE_BYTES,
    SUPPORTED_FORMATS,
    ImageAnalysisResult,
    VisionAnalyzer,
    _encode_image,
)


# ---------------------------------------------------------------------------
# Helper: create a small test image file
# ---------------------------------------------------------------------------


def _make_image(path: Path, size: int = 64) -> Path:
    """Create a tiny fake image file at the given path."""
    path.write_bytes(b"\x89PNG" + b"\x00" * size)
    return path


# ---------------------------------------------------------------------------
# _encode_image
# ---------------------------------------------------------------------------


class TestEncodeImage:
    def test_encodes_png(self, tmp_path):
        img = _make_image(tmp_path / "test.png")
        b64, mime = _encode_image(img)
        # Verify the base64 decodes back to original bytes
        assert base64.b64decode(b64) == img.read_bytes()
        assert "image" in mime

    def test_encodes_jpg(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
        b64, mime = _encode_image(img)
        assert len(b64) > 0
        assert "jpeg" in mime or "jpg" in mime

    def test_file_not_found_raises(self, tmp_path):
        missing = tmp_path / "nonexistent.png"
        with pytest.raises(FileNotFoundError, match="Image not found"):
            _encode_image(missing)

    def test_unsupported_format_raises(self, tmp_path):
        bad = tmp_path / "data.pdf"
        bad.write_bytes(b"fake pdf content")
        with pytest.raises(ValueError, match="Unsupported image format"):
            _encode_image(bad)

    def test_image_too_large_raises(self, tmp_path):
        huge = tmp_path / "huge.png"
        huge.write_bytes(b"\x00" * (MAX_IMAGE_SIZE_BYTES + 1))
        with pytest.raises(ValueError, match="Image too large"):
            _encode_image(huge)

    def test_all_supported_formats(self, tmp_path):
        for ext in SUPPORTED_FORMATS:
            img = tmp_path / f"test{ext}"
            img.write_bytes(b"\x00" * 16)
            b64, mime = _encode_image(img)
            assert len(b64) > 0

    def test_encoding_is_valid_base64(self, tmp_path):
        img = _make_image(tmp_path / "valid.png", size=128)
        b64, _ = _encode_image(img)
        # Should not raise
        decoded = base64.b64decode(b64)
        assert decoded == img.read_bytes()


# ---------------------------------------------------------------------------
# ImageAnalysisResult
# ---------------------------------------------------------------------------


class TestImageAnalysisResult:
    def test_defaults(self):
        result = ImageAnalysisResult(description="A cat")
        assert result.description == "A cat"
        assert result.confidence == "high"
        assert result.metadata == {}

    def test_to_dict(self):
        result = ImageAnalysisResult(
            description="UI screenshot",
            confidence="medium",
            metadata={"model": "gpt-4o"},
        )
        d = result.to_dict()
        assert d["description"] == "UI screenshot"
        assert d["confidence"] == "medium"
        assert d["metadata"]["model"] == "gpt-4o"

    def test_metadata_defaults_to_empty_dict(self):
        result = ImageAnalysisResult(description="test")
        assert result.metadata == {}
        assert isinstance(result.metadata, dict)


# ---------------------------------------------------------------------------
# VisionAnalyzer — initialization
# ---------------------------------------------------------------------------


class TestVisionAnalyzerInit:
    def test_default_openai_model(self):
        analyzer = VisionAnalyzer(provider="openai", api_key="sk-test")
        assert analyzer.model == "gpt-4o"

    def test_default_claude_model(self):
        analyzer = VisionAnalyzer(provider="claude", api_key="key")
        assert analyzer.model == "claude-sonnet-4-5-20250929"

    def test_default_ollama_model(self):
        analyzer = VisionAnalyzer(provider="ollama")
        assert analyzer.model == "llava"

    def test_custom_model_overrides_default(self):
        analyzer = VisionAnalyzer(provider="openai", model="gpt-4o-mini", api_key="k")
        assert analyzer.model == "gpt-4o-mini"

    def test_unknown_provider_default_model(self):
        analyzer = VisionAnalyzer(provider="unknown")
        assert analyzer.model == "gpt-4o"

    def test_provider_case_insensitive(self):
        analyzer = VisionAnalyzer(provider="OpenAI", api_key="k")
        assert analyzer.provider == "openai"


# ---------------------------------------------------------------------------
# VisionAnalyzer — analyze_image
# ---------------------------------------------------------------------------


class TestAnalyzeImage:
    async def test_analyze_image_openai(self, tmp_path):
        img = _make_image(tmp_path / "screenshot.png")
        analyzer = VisionAnalyzer(provider="openai", api_key="sk-test")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "A login page with two input fields."}}],
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await analyzer.analyze_image(img, "Describe this UI")

        assert result.description == "A login page with two input fields."
        assert result.metadata["model"] == "gpt-4o"
        assert result.metadata["image"] == str(img)

    async def test_analyze_image_anthropic(self, tmp_path):
        img = _make_image(tmp_path / "shot.png")
        analyzer = VisionAnalyzer(provider="claude", api_key="sk-ant")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"text": "An error dialog box."}],
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await analyzer.analyze_image(img, "What is this?")

        assert result.description == "An error dialog box."

    async def test_analyze_image_ollama(self, tmp_path):
        img = _make_image(tmp_path / "local.png")
        analyzer = VisionAnalyzer(provider="ollama")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "A dashboard with charts."}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await analyzer.analyze_image(img)

        assert result.description == "A dashboard with charts."

    async def test_analyze_image_unsupported_provider(self, tmp_path):
        img = _make_image(tmp_path / "test.png")
        analyzer = VisionAnalyzer(provider="gemini", api_key="k")

        with pytest.raises(ValueError, match="Unsupported vision provider"):
            await analyzer.analyze_image(img)

    async def test_analyze_image_file_not_found(self, tmp_path):
        analyzer = VisionAnalyzer(provider="openai", api_key="k")
        with pytest.raises(FileNotFoundError):
            await analyzer.analyze_image(tmp_path / "missing.png")

    async def test_analyze_image_unsupported_format(self, tmp_path):
        bad = tmp_path / "data.tiff"
        bad.write_bytes(b"\x00" * 16)
        analyzer = VisionAnalyzer(provider="openai", api_key="k")
        with pytest.raises(ValueError, match="Unsupported image format"):
            await analyzer.analyze_image(bad)

    async def test_analyze_image_custom_base_url(self, tmp_path):
        img = _make_image(tmp_path / "custom.png")
        analyzer = VisionAnalyzer(
            provider="openai", api_key="k",
            base_url="https://my-proxy.com/v1",
        )

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "proxied result"}}],
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await analyzer.analyze_image(img, "desc")

        # Verify the custom URL was used in the POST call
        call_args = mock_client.post.call_args
        assert "my-proxy.com" in call_args[0][0]
        assert result.description == "proxied result"


# ---------------------------------------------------------------------------
# VisionAnalyzer — compare_screenshots
# ---------------------------------------------------------------------------


class TestCompareScreenshots:
    async def test_compare_openai(self, tmp_path):
        before = _make_image(tmp_path / "before.png")
        after = _make_image(tmp_path / "after.png")
        analyzer = VisionAnalyzer(provider="openai", api_key="k")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Button color changed from blue to red."}}],
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await analyzer.compare_screenshots(before, after)

        assert "Button color changed" in result.description
        assert result.metadata["before"] == str(before)
        assert result.metadata["after"] == str(after)

    async def test_compare_anthropic(self, tmp_path):
        before = _make_image(tmp_path / "b.png")
        after = _make_image(tmp_path / "a.png")
        analyzer = VisionAnalyzer(provider="anthropic", api_key="k")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"text": "Layout shifted right."}],
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await analyzer.compare_screenshots(before, after)

        assert result.description == "Layout shifted right."

    async def test_compare_fallback_provider(self, tmp_path):
        """Ollama falls back to individual analysis of each image."""
        before = _make_image(tmp_path / "b.png")
        after = _make_image(tmp_path / "a.png")
        analyzer = VisionAnalyzer(provider="ollama")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "A page."}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await analyzer.compare_screenshots(before, after)

        assert "Before:" in result.description
        assert "After:" in result.description


# ---------------------------------------------------------------------------
# VisionAnalyzer — extract_text
# ---------------------------------------------------------------------------


class TestExtractText:
    async def test_extract_text_delegates_to_analyze(self, tmp_path):
        img = _make_image(tmp_path / "ocr.png")
        analyzer = VisionAnalyzer(provider="openai", api_key="k")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Error: NullPointerException at line 42"}}],
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            text = await analyzer.extract_text(img)

        assert "NullPointerException" in text

    async def test_extract_text_sends_ocr_prompt(self, tmp_path):
        img = _make_image(tmp_path / "text.png")
        analyzer = VisionAnalyzer(provider="openai", api_key="k")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Hello World"}}],
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await analyzer.extract_text(img)

        # Verify the OCR prompt was sent in the request
        call_kwargs = mock_client.post.call_args
        request_json = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        prompt_text = request_json["messages"][0]["content"][0]["text"]
        assert "Extract ALL text" in prompt_text
