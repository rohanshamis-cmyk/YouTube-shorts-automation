#!/usr/bin/env python3
"""Smoke test for hybrid keyframe rendering with graceful fallback."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from image_planning.keyframe_builder import KeyframeSpec
from image_providers.hybrid_keyframe_renderer import HybridKeyframeRenderer, summarize_results


def _sample_keyframes(output_dir: Path) -> list[KeyframeSpec]:
    prompts = [
        "Narrow apartment hallway, off-center frame, cold practical light, a note half under a door, quiet dread.",
        "Meeting room table close-up, shallow depth, side lighting, hairline crack crossing a glass, rising pressure.",
        "Exterior sidewalk before sunrise, medium-wide frame, sodium-vapor glow, abandoned bag by entry, cold aftermath.",
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
                visual_role="hybrid_smoke",
                metadata={"smoke": True},
            )
        )
    return keyframes


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root_dir = Path("generated/hybrid_keyframe_smoke") / stamp
    images_dir = root_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    keyframes = _sample_keyframes(images_dir)
    renderer = HybridKeyframeRenderer(prefer_real=True, output_root=str(root_dir))
    results = renderer.render_keyframes(keyframes)
    manifest_path = renderer.write_manifest(results, output_dir=root_dir)
    summary = summarize_results(results)

    print(f"attempted: {summary['total']}")
    print(f"real successes: {summary['real_success_count']}")
    print(f"fallbacks: {summary['fallback_count']}")
    print(f"hard fails: {summary['hard_fail_count']}")
    print(f"output folder: {root_dir}")
    print(f"manifest path: {manifest_path}")

    return 0 if summary["hard_fail_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

