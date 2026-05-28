"""Hybrid keyframe renderer with graceful real->placeholder fallback."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from image_planning.keyframe_builder import KeyframeSpec
from image_providers.base import GeneratedImageResult
from image_providers.openai_image_provider import OpenAIImageProvider


FALLBACK_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6pG8kAAAAASUVORK5CYII="
)


def summarize_results(results: list[GeneratedImageResult]) -> dict[str, int]:
    """Summarize hybrid rendering results for logging and manifests."""
    total = len(results)
    real_success_count = sum(
        1
        for result in results
        if result.status == "succeeded"
        and str((result.metadata or {}).get("render_mode", "")).startswith("real_")
    )
    fallback_count = sum(1 for result in results if result.status == "fallback")
    hard_fail_count = sum(1 for result in results if result.status == "error")
    return {
        "total": total,
        "real_success_count": real_success_count,
        "fallback_count": fallback_count,
        "hard_fail_count": hard_fail_count,
    }


class HybridKeyframeRenderer:
    """Render keyframes with real provider first, then local fallback if needed."""

    def __init__(self, prefer_real: bool = True, output_root: str = "generated/hybrid_keyframes"):
        self.prefer_real = bool(prefer_real)
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._real_provider: OpenAIImageProvider | None = None
        self.last_manifest_path: str | None = None

    def render_keyframes(self, keyframes: list[KeyframeSpec]) -> list[GeneratedImageResult]:
        results: list[GeneratedImageResult] = []
        for keyframe in keyframes:
            real_result = self._render_real(keyframe) if self.prefer_real else None

            if real_result and real_result.status == "succeeded":
                real_result.metadata = dict(real_result.metadata or {})
                real_result.metadata["render_mode"] = "real_openai"
                results.append(real_result)
                continue

            fallback_reason = None
            if real_result and real_result.error:
                fallback_reason = real_result.error
            elif not self.prefer_real:
                fallback_reason = "prefer_real disabled"
            else:
                fallback_reason = "real provider unavailable"

            fallback_result = self._render_placeholder(keyframe=keyframe, fallback_reason=fallback_reason)
            results.append(fallback_result)
        return results

    def _render_real(self, keyframe: KeyframeSpec) -> GeneratedImageResult:
        try:
            provider = self._get_real_provider()
            return provider.generate_image(
                shot_id=keyframe.shot_id,
                prompt=keyframe.image_prompt,
                output_path=keyframe.output_image_path,
            )
        except Exception as exc:  # pragma: no cover - defensive wrapper
            return GeneratedImageResult(
                shot_id=keyframe.shot_id,
                status="error",
                prompt=keyframe.image_prompt,
                revised_prompt=None,
                output_path=keyframe.output_image_path,
                width=None,
                height=None,
                provider="openai",
                error=f"Real provider error: {exc}",
                metadata={"render_mode": "real_openai"},
            )

    def _render_placeholder(self, keyframe: KeyframeSpec, fallback_reason: str | None) -> GeneratedImageResult:
        output_path = Path(keyframe.output_image_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            used_pillow, width, height = self._write_placeholder_png(output_path, keyframe.shot_id)
            metadata = dict(keyframe.metadata or {})
            metadata.update(
                {
                    "render_mode": "placeholder_fallback",
                    "fallback_reason": fallback_reason or "unknown",
                    "placeholder_writer": "pillow" if used_pillow else "embedded_base64_png",
                }
            )
            return GeneratedImageResult(
                shot_id=keyframe.shot_id,
                status="fallback",
                prompt=keyframe.image_prompt,
                revised_prompt=None,
                output_path=str(output_path),
                width=width,
                height=height,
                provider="placeholder_fallback",
                error=None,
                metadata=metadata,
            )
        except Exception as exc:
            metadata = dict(keyframe.metadata or {})
            metadata.update(
                {
                    "render_mode": "placeholder_fallback",
                    "fallback_reason": fallback_reason or "unknown",
                }
            )
            return GeneratedImageResult(
                shot_id=keyframe.shot_id,
                status="error",
                prompt=keyframe.image_prompt,
                revised_prompt=None,
                output_path=str(output_path),
                width=None,
                height=None,
                provider="placeholder_fallback",
                error=f"Fallback generation failed: {exc}",
                metadata=metadata,
            )

    def write_manifest(
        self,
        results: list[GeneratedImageResult],
        output_dir: str | Path | None = None,
    ) -> str:
        out_dir = Path(output_dir) if output_dir else self.output_root
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = out_dir / "hybrid_keyframe_manifest.json"

        summary = summarize_results(results)
        rows: list[dict[str, Any]] = []
        for result in results:
            render_mode = str((result.metadata or {}).get("render_mode", "")).strip()
            if not render_mode:
                render_mode = "real_openai" if result.status == "succeeded" else "placeholder_fallback"
            rows.append(
                {
                    "shot_id": result.shot_id,
                    "status": result.status,
                    "render_mode": render_mode,
                    "provider": result.provider,
                    "output_path": result.output_path,
                    "error": result.error,
                    "metadata": result.metadata,
                }
            )

        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "total": summary["total"],
            "real_success_count": summary["real_success_count"],
            "fallback_count": summary["fallback_count"],
            "hard_fail_count": summary["hard_fail_count"],
            "results": rows,
        }
        manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        self.last_manifest_path = str(manifest_path)
        return str(manifest_path)

    def _get_real_provider(self) -> OpenAIImageProvider:
        if self._real_provider is None:
            self._real_provider = OpenAIImageProvider()
        return self._real_provider

    def _write_placeholder_png(self, path: Path, shot_id: str) -> tuple[bool, int, int]:
        try:
            from PIL import Image, ImageDraw  # type: ignore

            idx = self._shot_index(shot_id)
            base_palette = [(24, 28, 36), (33, 38, 49), (20, 22, 31)]
            accent_palette = [(128, 145, 172), (160, 138, 126), (122, 151, 150)]
            img = Image.new("RGB", (96, 96), base_palette[idx % len(base_palette)])
            draw = ImageDraw.Draw(img)
            draw.rectangle((12, 12, 84, 84), outline=accent_palette[idx % len(accent_palette)], width=2)
            draw.line((16, 74, 80, 24), fill=accent_palette[(idx + 1) % len(accent_palette)], width=1)
            img.save(path, format="PNG")
            return True, 96, 96
        except Exception:
            path.write_bytes(base64.b64decode(FALLBACK_PNG_BASE64))
            return False, 1, 1

    def _shot_index(self, shot_id: str) -> int:
        digits = "".join(ch for ch in shot_id if ch.isdigit())
        if not digits:
            return 0
        return max(0, int(digits) - 1)

