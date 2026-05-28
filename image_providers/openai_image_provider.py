"""OpenAI image provider for real keyframe PNG generation."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import requests

from .base import GeneratedImageResult, ImageProvider


DEFAULT_IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1").strip() or "gpt-image-1"
DEFAULT_IMAGE_SIZE = os.getenv("IMAGE_SIZE", "1024x1536").strip() or "1024x1536"
SUPPORTED_IMAGE_SIZES = {"1024x1024", "1024x1536", "1536x1024"}


class OpenAIImageProvider(ImageProvider):
    """Generate local PNG images from prompts using OpenAI Images API."""

    provider_name = "openai"

    def __init__(
        self,
        model: str | None = None,
        size: str | None = None,
        api_key: str | None = None,
    ):
        self.model = (model or DEFAULT_IMAGE_MODEL).strip()
        self.size = self._normalize_size(size or DEFAULT_IMAGE_SIZE)
        self.api_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        self._client: Any | None = None

    def generate_image(self, shot_id: str, prompt: str, output_path: str) -> GeneratedImageResult:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        width, height = self._parse_size(self.size)

        if not prompt or not prompt.strip():
            return self._error_result(
                shot_id=shot_id,
                prompt=prompt,
                output_path=str(output),
                error="Prompt is empty.",
                width=width,
                height=height,
            )

        if not self.api_key:
            return self._error_result(
                shot_id=shot_id,
                prompt=prompt,
                output_path=str(output),
                error="OPENAI_API_KEY is not set.",
                width=width,
                height=height,
            )

        try:
            client = self._ensure_client()
        except Exception as exc:
            return self._error_result(
                shot_id=shot_id,
                prompt=prompt,
                output_path=str(output),
                error=str(exc),
                width=width,
                height=height,
            )

        try:
            response = client.images.generate(
                model=self.model,
                prompt=prompt,
                size=self.size,
            )
            image_bytes, revised_prompt = self._extract_image_bytes(response)
            output.write_bytes(image_bytes)
            return GeneratedImageResult(
                shot_id=shot_id,
                status="succeeded",
                prompt=prompt,
                revised_prompt=revised_prompt,
                output_path=str(output),
                width=width,
                height=height,
                provider=self.provider_name,
                error=None,
                metadata={
                    "model": self.model,
                    "size": self.size,
                },
            )
        except Exception as exc:
            return self._error_result(
                shot_id=shot_id,
                prompt=prompt,
                output_path=str(output),
                error=f"OpenAI image generation failed: {exc}",
                width=width,
                height=height,
            )

    def generate_batch(self, items: list[tuple[str, str, str]]) -> list[GeneratedImageResult]:
        results: list[GeneratedImageResult] = []
        for shot_id, prompt, output_path in items:
            results.append(self.generate_image(shot_id=shot_id, prompt=prompt, output_path=output_path))
        return results

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except Exception as exc:
            raise RuntimeError(
                "OpenAI client package is unavailable. Install/upgrade `openai` to use IMAGE_PROVIDER=openai."
            ) from exc
        self._client = OpenAI(api_key=self.api_key)
        return self._client

    def _normalize_size(self, requested: str) -> str:
        value = str(requested or "").strip().lower()
        if value in SUPPORTED_IMAGE_SIZES:
            return value

        # If unsupported, choose nearest documented vertical/horizontal size.
        width, height = self._parse_size(value)
        if width is None or height is None:
            return "1024x1536"
        if height >= width:
            return "1024x1536"
        return "1536x1024"

    def _parse_size(self, value: str) -> tuple[int | None, int | None]:
        text = str(value or "").strip().lower()
        if "x" not in text:
            return None, None
        left, right = text.split("x", 1)
        try:
            return int(left), int(right)
        except ValueError:
            return None, None

    def _extract_image_bytes(self, response: Any) -> tuple[bytes, str | None]:
        data = getattr(response, "data", None)
        if not data and isinstance(response, dict):
            data = response.get("data")
        if not data:
            raise RuntimeError("Images API returned no data entries.")

        first = data[0]
        b64_json = self._get_field(first, "b64_json")
        revised_prompt = self._get_field(first, "revised_prompt")
        if b64_json:
            return base64.b64decode(b64_json), revised_prompt

        url = self._get_field(first, "url")
        if url:
            resp = requests.get(url, timeout=45)
            resp.raise_for_status()
            return resp.content, revised_prompt

        raise RuntimeError("Images API response missing both b64_json and url.")

    def _get_field(self, item: Any, field: str) -> Any:
        if isinstance(item, dict):
            return item.get(field)
        return getattr(item, field, None)

    def _error_result(
        self,
        shot_id: str,
        prompt: str,
        output_path: str | None,
        error: str,
        width: int | None,
        height: int | None,
    ) -> GeneratedImageResult:
        return GeneratedImageResult(
            shot_id=shot_id,
            status="error",
            prompt=prompt,
            revised_prompt=None,
            output_path=output_path,
            width=width,
            height=height,
            provider=self.provider_name,
            error=error,
            metadata={"model": self.model, "size": self.size},
        )

