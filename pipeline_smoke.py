#!/usr/bin/env python3
"""End-to-end smoke pipeline for script -> hybrid keyframes -> runway payloads."""

from __future__ import annotations

import base64
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from asset_bridge.runway_asset_bridge import RunwayAssetBridge, apply_assets_to_planned_shots
from image_planning.keyframe_builder import KeyframeBuilder, KeyframeSpec
from image_providers.hybrid_keyframe_renderer import HybridKeyframeRenderer, summarize_results
from video_planning.shot_pack_builder import PlannedShot, ShotPackBuilder
from video_providers.runway_adapter import RunwayAdapter


FALLBACK_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6pG8kAAAAASUVORK5CYII="
)


def sample_script() -> dict[str, str]:
    """One grounded dark-curiosity script sample for pipeline smoke."""
    return {
        "title": "the sentence that made everyone stop moving",
        "hook": "I said one sentence, and the whole room went silent.",
        "narration": (
            "I said one sentence, and the whole room went silent. "
            "The vent hum suddenly sounded louder than anyone breathing. "
            "A glass on the table cracked even though no one touched it. "
            "Then every face turned toward the same corner behind my chair. "
            "I backed toward the door and realized the latch had shifted from the hallway side. "
            "I left my bag, walked outside, and called security before sunrise."
        ),
        "series": "dark_curiosity",
        "niche": "mystery",
        "tone": "grounded cinematic suspense",
    }


def write_planned_shots_manifest(planned_shots: list[PlannedShot], output_dir: Path) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "planned_shots_manifest.json"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "count": len(planned_shots),
        "planned_shots": [asdict(shot) for shot in planned_shots],
    }
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(manifest_path)


def ensure_keyframe_placeholder_files(keyframes: list[KeyframeSpec]) -> None:
    """Create tiny placeholder PNG files for each keyframe output path."""
    for index, keyframe in enumerate(keyframes):
        target = Path(keyframe.output_image_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            continue
        if not _write_png_with_pillow(target, index):
            _write_png_fallback(target)


def _write_png_with_pillow(path: Path, idx: int) -> bool:
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except Exception:
        return False

    base = [(26, 30, 38), (34, 38, 49), (22, 24, 31)]
    accent = [(140, 146, 168), (165, 143, 129), (120, 152, 148)]
    img = Image.new("RGB", (96, 96), base[idx % len(base)])
    draw = ImageDraw.Draw(img)
    draw.rectangle((12, 12, 84, 84), outline=accent[idx % len(accent)], width=2)
    draw.line((14, 72, 82, 22), fill=accent[(idx + 1) % len(accent)], width=1)
    img.save(path, format="PNG")
    return True


def _write_png_fallback(path: Path) -> None:
    path.write_bytes(base64.b64decode(FALLBACK_PNG_BASE64))


def _assert_runway_payloads_are_data_uris(runway_output_dir: Path) -> None:
    shots_dir = runway_output_dir / "shots"
    payload_files = sorted(shots_dir.glob("*.payload.json"))
    if not payload_files:
        raise RuntimeError(f"No payload files found in {shots_dir}")

    for payload_file in payload_files:
        payload = json.loads(payload_file.read_text(encoding="utf-8"))
        prompt_image = str(payload.get("promptImage", ""))
        if not prompt_image.startswith("data:image/"):
            raise RuntimeError(
                f"Payload {payload_file.name} has non-data URI promptImage: {prompt_image[:80]}"
            )


def write_pipeline_manifest(
    root_dir: Path,
    script: dict[str, Any],
    planned_manifest_path: str,
    keyframe_manifest_path: str,
    hybrid_keyframe_manifest_path: str,
    asset_manifest_path: str,
    runway_manifest_path: str,
    real_keyframe_successes: int,
    fallback_keyframes: int,
    hard_keyframe_failures: int,
    counts: dict[str, int],
) -> str:
    manifest_path = root_dir / "pipeline_manifest.json"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "script": {
            "title": str(script.get("title", "")),
            "hook": str(script.get("hook", "")),
            "narration_chars": len(str(script.get("narration", ""))),
        },
        "planned_shots_manifest": planned_manifest_path,
        "keyframe_manifest": keyframe_manifest_path,
        "hybrid_keyframe_manifest": hybrid_keyframe_manifest_path,
        "asset_manifest": asset_manifest_path,
        "runway_manifest": runway_manifest_path,
        "real_keyframe_successes": real_keyframe_successes,
        "fallback_keyframes": fallback_keyframes,
        "hard_keyframe_failures": hard_keyframe_failures,
        "counts": counts,
    }
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(manifest_path)


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root_dir = Path("generated/pipeline_smoke") / stamp
    planned_dir = root_dir / "planned_shots"
    keyframes_dir = root_dir / "keyframes"
    assets_dir = root_dir / "asset_bridge"
    runway_dir = root_dir / "runway_batch"

    try:
        script = sample_script()

        shot_builder = ShotPackBuilder(ratio="720:1280", duration=4)
        planned_shots = shot_builder.build_from_script(script)
        planned_manifest_path = write_planned_shots_manifest(planned_shots, planned_dir)

        keyframe_builder = KeyframeBuilder(output_root=str(keyframes_dir))
        keyframes = keyframe_builder.build_from_planned_shots(planned_shots)

        hybrid_renderer = HybridKeyframeRenderer(prefer_real=True, output_root=str(keyframes_dir))
        hybrid_results = hybrid_renderer.render_keyframes(keyframes)
        hybrid_summary = summarize_results(hybrid_results)
        hybrid_keyframe_manifest_path = hybrid_renderer.write_manifest(hybrid_results, output_dir=keyframes_dir)

        real_keyframe_successes = hybrid_summary["real_success_count"]
        fallback_keyframes = hybrid_summary["fallback_count"]
        hard_keyframe_failures = hybrid_summary["hard_fail_count"]
        if hard_keyframe_failures > 0:
            raise RuntimeError(
                "Hybrid keyframe rendering had hard failures; stopping before asset bridge. "
                f"real={real_keyframe_successes}, fallback={fallback_keyframes}, hard_fail={hard_keyframe_failures}"
            )

        missing_keyframes = [
            keyframe.output_image_path
            for keyframe in keyframes
            if not Path(keyframe.output_image_path).exists()
        ]
        if missing_keyframes:
            raise RuntimeError(
                "Hybrid keyframe rendering did not create all expected keyframe files: "
                + ", ".join(missing_keyframes[:3])
            )

        keyframe_manifest_path = keyframe_builder.write_manifest(keyframes, keyframes_dir)

        planned_with_keyframe_paths = keyframe_builder.attach_keyframes_to_shots(planned_shots, keyframes)

        bridge = RunwayAssetBridge(mode="data_uri", output_root=str(assets_dir))
        asset_inputs = [(shot.shot_id, shot.prompt_image) for shot in planned_with_keyframe_paths]
        assets = bridge.build_assets(asset_inputs)
        asset_manifest_path = bridge.write_manifest(assets, assets_dir)

        planned_with_data_uri = apply_assets_to_planned_shots(planned_with_keyframe_paths, assets)
        runway_shots = shot_builder.to_runway_shots(planned_with_data_uri)

        adapter = RunwayAdapter(mode="offline", output_root=str(runway_dir))
        runway_results = adapter.render_batch(runway_shots)
        runway_manifest_path = adapter.last_manifest_path or str(runway_dir / "batch_manifest.json")

        rendered_offline = sum(1 for result in runway_results if result.status == "mocked")
        if rendered_offline != len(runway_shots):
            raise RuntimeError(
                f"Offline render mismatch: expected {len(runway_shots)} mocked results, got {rendered_offline}."
            )

        _assert_runway_payloads_are_data_uris(runway_dir)

        pipeline_manifest_path = write_pipeline_manifest(
            root_dir=root_dir,
            script=script,
            planned_manifest_path=planned_manifest_path,
            keyframe_manifest_path=keyframe_manifest_path,
            hybrid_keyframe_manifest_path=hybrid_keyframe_manifest_path,
            asset_manifest_path=asset_manifest_path,
            runway_manifest_path=runway_manifest_path,
            real_keyframe_successes=real_keyframe_successes,
            fallback_keyframes=fallback_keyframes,
            hard_keyframe_failures=hard_keyframe_failures,
            counts={
                "planned_shots": len(planned_shots),
                "keyframes": len(keyframes),
                "real_keyframe_successes": real_keyframe_successes,
                "fallback_keyframes": fallback_keyframes,
                "hard_keyframe_failures": hard_keyframe_failures,
                "assets": len(assets),
                "runway_shots": len(runway_shots),
                "runway_rendered_offline": rendered_offline,
            },
        )

    except Exception as exc:
        print(f"pipeline smoke failed: {exc}", file=sys.stderr)
        return 1

    print(f"script title: {script.get('title', '')}")
    print(f"planned shots: {len(planned_shots)}")
    print(f"keyframes: {len(keyframes)}")
    print(f"real keyframe successes: {real_keyframe_successes}")
    print(f"fallback keyframe count: {fallback_keyframes}")
    print(f"hard keyframe failures: {hard_keyframe_failures}")
    print(f"assets: {len(assets)}")
    print(f"final runway shots rendered offline: {rendered_offline}")
    print(f"root output folder: {root_dir}")
    print(f"planned shots manifest: {planned_manifest_path}")
    print(f"keyframe manifest: {keyframe_manifest_path}")
    print(f"hybrid keyframe manifest: {hybrid_keyframe_manifest_path}")
    print(f"asset manifest: {asset_manifest_path}")
    print(f"runway manifest: {runway_manifest_path}")
    print(f"pipeline manifest: {pipeline_manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
