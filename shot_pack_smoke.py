#!/usr/bin/env python3
"""Smoke test: SCRIPT -> SHOT PACK -> OFFLINE RUNWAY BATCH."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from video_planning.shot_pack_builder import ShotPackBuilder
from video_providers.runway_adapter import RunwayAdapter


def _sample_script() -> dict[str, str]:
    return {
        "title": "the whisper behind the door",
        "hook": "I heard my name on the other side of my locked door.",
        "narration": (
            "I heard my name on the other side of my locked door. "
            "The hallway was empty when I checked the peephole. "
            "A folded note was waiting under the door with tonight's date. "
            "Then the latch moved once, slowly, without anyone touching it. "
            "I backed away and called my brother, but he said he was already inside my apartment. "
            "I left before sunrise and never opened that note again."
        ),
        "series": "dark_curiosity",
        "niche": "mystery",
        "tone": "cinematic suspense",
    }


def main() -> int:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = Path("generated/runway/shot_pack_smoke") / timestamp

    builder = ShotPackBuilder(ratio="720:1280", duration=4)
    script = _sample_script()
    planned_shots = builder.build_from_script(script)
    runway_shots = builder.to_runway_shots(planned_shots)

    adapter = RunwayAdapter(mode="offline", output_root=str(output_root))
    results = adapter.render_batch(runway_shots)

    rendered_ok = sum(1 for row in results if row.status == "mocked")
    failed = len(results) - rendered_ok

    print(f"shots planned: {len(planned_shots)}")
    print(f"shots rendered offline: {rendered_ok}")
    print(f"output folder: {output_root}")
    print(f"manifest path: {adapter.last_manifest_path or output_root / 'batch_manifest.json'}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

