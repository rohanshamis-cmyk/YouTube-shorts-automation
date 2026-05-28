#!/usr/bin/env python3
"""Offline smoke test for the Runway adapter."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from video_providers.runway_adapter import RunwayAdapter, ShotSpec


def _sample_shots() -> list[ShotSpec]:
    image_url = "https://images.unsplash.com/photo-1477346611705-65d1883cee1e"
    return [
        ShotSpec(
            shot_id="shot_01",
            prompt_text="Dark apartment hallway at midnight, flickering light, slow camera push, unease rising.",
            prompt_image=image_url,
            ratio="720:1280",
            duration=4,
            metadata={"beat": "setup"},
        ),
        ShotSpec(
            shot_id="shot_02",
            prompt_text="Close-up on a closed door as a shadow slides past the frame, subtle handheld tension.",
            prompt_image=image_url,
            ratio="720:1280",
            duration=4,
            metadata={"beat": "reveal"},
        ),
        ShotSpec(
            shot_id="shot_03",
            prompt_text="Hard final image of an empty hallway with a swinging light and distant siren glow.",
            prompt_image=image_url,
            ratio="720:1280",
            duration=4,
            metadata={"beat": "landing"},
        ),
    ]


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = Path("generated/runway/smoke_test") / stamp

    adapter = RunwayAdapter(mode="offline", output_root=str(output_root))
    shots = _sample_shots()
    results = adapter.render_batch(shots)

    pass_count = sum(1 for result in results if result.status in {"mocked", "submitted"})
    fail_count = len(results) - pass_count
    manifest_path = adapter.last_manifest_path or str(output_root / "batch_manifest.json")

    print(f"shots processed: {len(results)}")
    print(f"output folder: {output_root}")
    print(f"manifest path: {manifest_path}")
    print(f"pass/fail: {pass_count}/{fail_count}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

