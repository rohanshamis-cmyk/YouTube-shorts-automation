#!/usr/bin/env python3
"""Smoke test for local-image to Runway promptImage asset bridge."""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path

from asset_bridge.runway_asset_bridge import RunwayAssetBridge


FALLBACK_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6pG8kAAAAASUVORK5CYII="
)


def _write_png_with_pillow(path: Path, idx: int) -> bool:
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except Exception:
        return False

    color_set = [(24, 27, 34), (37, 42, 56), (18, 21, 27)]
    accent = [(130, 142, 170), (165, 134, 122), (122, 156, 148)]
    img = Image.new("RGB", (64, 64), color_set[idx % len(color_set)])
    draw = ImageDraw.Draw(img)
    draw.rectangle((10, 10, 54, 54), outline=accent[idx % len(accent)], width=2)
    draw.line((12, 46, 52, 18), fill=accent[(idx + 1) % len(accent)], width=1)
    img.save(path, format="PNG")
    return True


def _write_png_fallback(path: Path) -> None:
    path.write_bytes(base64.b64decode(FALLBACK_PNG_BASE64))


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("generated/asset_bridge_smoke") / stamp
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    items: list[tuple[str, str]] = []
    for idx in range(3):
        shot_id = f"shot_{idx + 1:02d}"
        png_path = images_dir / f"{shot_id}.png"
        if not _write_png_with_pillow(png_path, idx):
            _write_png_fallback(png_path)
        items.append((shot_id, str(png_path)))

    bridge = RunwayAssetBridge(mode="data_uri", output_root="generated/asset_bridge")
    assets = bridge.build_assets(items)
    manifest_path = bridge.write_manifest(assets=assets, output_dir=out_dir)

    preview = assets[0].prompt_image_value[:60] if assets else ""

    print(f"assets built: {len(assets)}")
    print(f"output folder: {out_dir}")
    print(f"manifest path: {manifest_path}")
    print(f"first prompt_image prefix: {preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

