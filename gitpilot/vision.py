# gitpilot/vision.py
"""Vision & image analysis for GitPilot.

Uses multimodal LLM capabilities to analyse screenshots, architecture
diagrams, error images, and design mockups.  Supports multiple providers:

- **OpenAI** (GPT-4o, GPT-4o-mini) — via base64 image in messages
- **Anthropic** (Claude) — via base64 image in messages
- **Ollama** (LLaVA, etc.) — local multimodal models

The module reads images, encodes them, and sends them alongside a
text prompt to the configured LLM provider.
"""
from __future__ import annotations

import base64
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB
SUPPORTED_FORMATS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


@dataclass
class ImageAnalysisResult:
    """Result of an image analysis."""

    description: str
    confidence: str = "high"  # high | medium | low
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


def _encode_image(image_path: Path) -> tuple:
    """Read and base64-encode an image.  Returns (base64_str, mime_type)."""
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    suffix = image_path.suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported image format: {suffix}")

    size = image_path.stat().st_size
    if size > MAX_IMAGE_SIZE_BYTES:
        raise ValueError(f"Image too large: {size} bytes (max {MAX_IMAGE_SIZE_BYTES})")

    mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
    data = image_path.read_bytes()
    return base64.b64encode(data).decode("utf-8"), mime


class VisionAnalyzer:
    """Analyze images using multimodal LLM capabilities.

    Usage::

        analyzer = VisionAnalyzer(provider="openai", api_key="sk-...")
        result = await analyzer.analyze_image(
            Path("screenshot.png"),
            prompt="Describe this UI and identify any bugs",
        )
    """

    def __init__(
        self,
        provider: str = "openai",
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.provider = provider.lower()
        self.api_key = api_key
        self.model = model or self._default_model()
        self.base_url = base_url

    def _default_model(self) -> str:
        defaults = {
            "openai": "gpt-4o",
            "claude": "claude-sonnet-4-5-20250929",
            "ollama": "llava",
        }
        return defaults.get(self.provider, "gpt-4o")

    async def analyze_image(
        self,
        image_path: Path,
        prompt: str = "Describe this image in detail.",
    ) -> ImageAnalysisResult:
        """Analyze a single image with a text prompt."""
        b64, mime = _encode_image(image_path)

        if self.provider == "openai":
            text = await self._call_openai(b64, mime, prompt)
        elif self.provider in ("claude", "anthropic"):
            text = await self._call_anthropic(b64, mime, prompt)
        elif self.provider == "ollama":
            text = await self._call_ollama(b64, prompt)
        else:
            raise ValueError(f"Unsupported vision provider: {self.provider}")

        return ImageAnalysisResult(
            description=text,
            metadata={"model": self.model, "image": str(image_path)},
        )

    async def compare_screenshots(
        self,
        before: Path,
        after: Path,
        prompt: str = "Compare these two screenshots and describe the differences.",
    ) -> ImageAnalysisResult:
        """Compare two screenshots and describe differences."""
        b64_before, mime_before = _encode_image(before)
        b64_after, mime_after = _encode_image(after)

        combined_prompt = (
            f"{prompt}\n\n"
            "The first image is the 'before' state and the second is the 'after' state."
        )

        if self.provider == "openai":
            text = await self._call_openai_multi(
                [(b64_before, mime_before), (b64_after, mime_after)],
                combined_prompt,
            )
        elif self.provider in ("claude", "anthropic"):
            text = await self._call_anthropic_multi(
                [(b64_before, mime_before), (b64_after, mime_after)],
                combined_prompt,
            )
        else:
            # Fallback: analyze each separately
            r1 = await self.analyze_image(before, "Describe this screenshot.")
            r2 = await self.analyze_image(after, "Describe this screenshot.")
            text = f"Before: {r1.description}\n\nAfter: {r2.description}"

        return ImageAnalysisResult(
            description=text,
            metadata={
                "model": self.model,
                "before": str(before),
                "after": str(after),
            },
        )

    async def extract_text(self, image_path: Path) -> str:
        """Extract text from an image (OCR via multimodal LLM)."""
        result = await self.analyze_image(
            image_path,
            prompt=(
                "Extract ALL text visible in this image. "
                "Return only the extracted text, preserving layout where possible. "
                "If there are code snippets, preserve formatting and indentation."
            ),
        )
        return result.description

    # ------------------------------------------------------------------
    # Provider implementations
    # ------------------------------------------------------------------

    async def _call_openai(self, b64: str, mime: str, prompt: str) -> str:
        url = self.base_url or "https://api.openai.com/v1"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {
                                "url": f"data:{mime};base64,{b64}",
                            }},
                        ],
                    }],
                    "max_tokens": 4096,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def _call_openai_multi(
        self, images: List[tuple], prompt: str,
    ) -> str:
        url = self.base_url or "https://api.openai.com/v1"
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for b64, mime in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": content}],
                    "max_tokens": 4096,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def _call_anthropic(self, b64: str, mime: str, prompt: str) -> str:
        url = self.base_url or "https://api.anthropic.com/v1"
        media_type = mime or "image/png"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{url}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 4096,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            }},
                            {"type": "text", "text": prompt},
                        ],
                    }],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"]

    async def _call_anthropic_multi(
        self, images: List[tuple], prompt: str,
    ) -> str:
        url = self.base_url or "https://api.anthropic.com/v1"
        content: List[Dict[str, Any]] = []
        for b64, mime in images:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime or "image/png", "data": b64},
            })
        content.append({"type": "text", "text": prompt})
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{url}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": content}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"]

    async def _call_ollama(self, b64: str, prompt: str) -> str:
        url = self.base_url or "http://localhost:11434"
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "images": [b64],
                    "stream": False,
                },
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
