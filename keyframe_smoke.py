#!/usr/bin/env python3
"""Smoke test: SCRIPT -> SHOT PACK -> KEYFRAME CONTRACT -> OFFLINE RUNWAY."""

from __future__ import annotations

from pathlib import Path

from image_planning.keyframe_builder import KeyframeBuilder
from video_planning.shot_pack_builder import ShotPackBuilder
from video_providers.runway_adapter import RunwayAdapter


def _sample_script() -> dict[str, str]:
    return {
        "title": "the room went quiet after one sentence",
        "hook": "The room went quiet after one sentence, and nobody looked at me.",
        "narration": (
            "The room went quiet after one sentence, and nobody looked at me. "
            "The vent hum got louder than my own breathing. "
            "A glass on the table cracked without a hand near it. "
            "Then every face turned to the same corner behind my chair. "
            "I backed toward the door and realized it was locked from the hallway. "
            "I left my bag, ran outside, and called security before sunrise."
        ),
        "series": "dark_curiosity",
        "niche": "mystery",
        "tone": "cinematic suspense",
    }


def main() -> int:
    script = _sample_script()

    shot_builder = ShotPackBuilder(ratio="720:1280", duration=4)
    planned_shots = shot_builder.build_from_script(script)

    keyframe_builder = KeyframeBuilder(output_root="generated/keyframes_smoke")
    keyframes = keyframe_builder.build_from_planned_shots(planned_shots)

    keyframe_output_dir = keyframe_builder.last_output_dir or "generated/keyframes_smoke"
    keyframe_manifest_path = keyframe_builder.write_manifest(keyframes, keyframe_output_dir)

    updated_planned_shots = keyframe_builder.attach_keyframes_to_shots(planned_shots, keyframes)
    runway_shots = shot_builder.to_runway_shots(updated_planned_shots)

    run_id = keyframe_builder.last_run_id or "latest"
    runway_output_dir = Path("generated/runway/keyframe_smoke") / run_id
    adapter = RunwayAdapter(mode="offline", output_root=str(runway_output_dir))
    results = adapter.render_batch(runway_shots)

    rendered_count = sum(1 for result in results if result.status == "mocked")

    print(f"planned shots: {len(planned_shots)}")
    print(f"keyframes created: {len(keyframes)}")
    print(f"updated shots rendered offline: {rendered_count}")
    print(f"keyframe manifest path: {keyframe_manifest_path}")
    print(f"runway manifest path: {adapter.last_manifest_path or runway_output_dir / 'batch_manifest.json'}")

    return 0 if rendered_count == len(updated_planned_shots) else 1


if __name__ == "__main__":
    raise SystemExit(main())

