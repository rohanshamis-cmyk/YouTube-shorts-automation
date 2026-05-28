#!/usr/bin/env python3
"""Smoke test for real keyframe image provider plumbing."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from image_planning.keyframe_builder import KeyframeSpec
from image_providers.base import render_keyframes_with_provider
from image_providers.openai_image_provider import OpenAIImageProvider


def _sample_keyframes(output_dir: Path) -> list[KeyframeSpec]:
    prompts = [
        "Dim apartment hallway, off-center composition, cold practical light, a note half under a closed door, quiet dread.",
        "Small meeting room table in foreground, side-lit frame, hairline crack spreading through a glass, rising tension.",
        "Exterior sidewalk before sunrise, medium-wide framing, sodium-vapor street glow, abandoned bag by doorway, cold aftermath.",
    ]
    keyframes: list[KeyframeSpec] = []
    for idx, prompt in enumerate(prompts, start=1):
        keyframes.append(
            KeyframeSpec(
                shot_id=f"shot_{idx:02d}",
                image_prompt=prompt,
                negative_prompt="blurry, watermark, text, bad anatomy, extra limbs, low detail",
                reference_image=None,
                output_image_path=str(output_dir / f"shot_{idx:02d}.png"),
                visual_role="smoke_test",
                metadata={"smoke": True},
            )
        )
    return keyframes


def main() -> int:
    provider_name = os.getenv("IMAGE_PROVIDER", "openai").strip().lower()
    if provider_name != "openai":
        print(f"provider: {provider_name}")
        print("images attempted: 0")
        print("images succeeded: 0")
        print("output folder: (not created)")
        print("error: only IMAGE_PROVIDER=openai is currently implemented")
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path("generated/image_generation_smoke") / stamp
    output_dir.mkdir(parents=True, exist_ok=True)

    keyframes = _sample_keyframes(output_dir)
    provider = OpenAIImageProvider()
    results = render_keyframes_with_provider(provider=provider, keyframes=keyframes)

    attempted = len(results)
    succeeded = sum(1 for result in results if result.status == "succeeded")

    print(f"provider: {provider.provider_name}")
    print(f"images attempted: {attempted}")
    print(f"images succeeded: {succeeded}")
    print(f"output folder: {output_dir}")

    if succeeded != attempted:
        first_error = next((result.error for result in results if result.error), "unknown error")
        print(f"error: {first_error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

