"""pivot.pipeline.images - per-segment image generation.

Thin wrapper over image_providers.OpenAIImageProvider (DALL-E 3 / gpt-image-1, the
Premium-profile image source). One image per segment; the renderer up-scales/crops
each to 1080x1920 and applies zoompan motion.

The provider is injectable so orchestration can be verified with synthetic images
(no OpenAI key required).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .config import Config


def make_provider(cfg: Config):
    """Instantiate the real DALL-E 3 provider (lazy import)."""
    from image_providers.openai_image_provider import OpenAIImageProvider  # lazy
    return OpenAIImageProvider(size=cfg.image_size, api_key=cfg.openai_key)


def generate_segment_images(segments: list[dict], batch_dir: Path, cfg: Config,
                            provider=None) -> list[Path]:
    """Generate one image per segment, return ordered list of PNG paths."""
    provider = provider or make_provider(cfg)
    items: list[tuple[str, str, str]] = []
    paths: list[Path] = []
    for s in segments:
        seg_dir = batch_dir / f"seg_{s['index']:02d}"
        seg_dir.mkdir(parents=True, exist_ok=True)
        out = seg_dir / f"img_{s['index']:02d}.png"
        items.append((f"seg{s['index']}", s["image_prompt"], str(out)))
        paths.append(out)

    results = provider.generate_batch(items)
    for r in results:
        status = (getattr(r, "status", "") or "").lower()
        op = getattr(r, "output_path", None)
        if "error" in status or not op or not Path(op).exists():
            raise RuntimeError(
                f"image gen failed for {getattr(r, 'shot_id', '?')}: "
                f"status={status} err={getattr(r, 'error', None)}")
    return paths


# --- key-free verification: stub provider that writes real PNGs via ffmpeg ---
class _StubResult:
    def __init__(self, shot_id, output_path):
        self.shot_id = shot_id
        self.status = "stub_ok"
        self.output_path = output_path
        self.error = None
        self.width, self.height = 1024, 1536


class _StubProvider:
    def generate_batch(self, items):
        out = []
        for shot_id, _prompt, output_path in items:
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi",
                 "-i", "color=c=0x14283c:s=1024x1536", "-frames:v", "1", output_path],
                capture_output=True, check=True)
            out.append(_StubResult(shot_id, output_path))
        return out


if __name__ == "__main__":
    import tempfile
    cfg = Config.load()
    segs = [{"index": i, "image_prompt": f"scene {i}"} for i in range(3)]
    with tempfile.TemporaryDirectory() as td:
        paths = generate_segment_images(segs, Path(td), cfg, provider=_StubProvider())
        assert len(paths) == 3 and all(p.exists() for p in paths), "missing images"
        print(f"OK: generated {len(paths)} stub images, sizes:",
              [p.stat().st_size for p in paths])
    print("orchestration verified (live images need OPENAI_API_KEY)")
