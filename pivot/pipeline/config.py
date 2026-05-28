"""pivot.pipeline.config - resolved settings for the Shorts pipeline.

Dependency-free: parses .env directly (no python-dotenv) so this module and the
render core import cleanly without the paid-API SDKs installed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = REPO_ROOT / "pivot" / "output"

# Appended to every image prompt for a consistent, faceless, caption-free look.
STYLE_SUFFIX = (
    "cinematic, moody dramatic lighting, shallow depth of field, muted teal-and-amber "
    "palette, photorealistic, vertical composition, no text, no watermark, no faces in "
    "focus, atmospheric"
)


def _load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    """Populate os.environ from .env without overriding already-set vars."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


@dataclass(frozen=True)
class Config:
    # providers / models (Premium profile)
    script_model: str = "claude-sonnet-4-6"
    voice_id: str = ""                    # ElevenLabs male; empty -> engine default
    image_size: str = "1024x1536"
    # motion
    zoom_rate: float = 0.0015
    zoom_max: float = 1.15
    fps: int = 30
    width: int = 1080
    height: int = 1920
    # pacing / structure
    words_per_second: float = 2.9
    min_segments: int = 6
    max_segments: int = 9
    music: bool = False
    style_suffix: str = STYLE_SUFFIX
    # keys (may be empty in non-live runs)
    anthropic_key: str = field(default="")
    openai_key: str = field(default="")
    elevenlabs_key: str = field(default="")

    @classmethod
    def load(cls) -> "Config":
        _load_dotenv()
        g = os.environ.get
        return cls(
            script_model=g("PIVOT_SCRIPT_MODEL", "claude-sonnet-4-6"),
            voice_id=g("PIVOT_VOICE_ID", g("ELEVENLABS_VOICE_ID", "")),
            image_size=g("PIVOT_IMAGE_SIZE", "1024x1536"),
            zoom_rate=float(g("PIVOT_ZOOM_RATE", "0.0015")),
            zoom_max=float(g("PIVOT_ZOOM_MAX", "1.15")),
            music=g("PIVOT_MUSIC", "off").lower() in ("on", "true", "1", "yes"),
            anthropic_key=g("ANTHROPIC_API_KEY", ""),
            openai_key=g("OPENAI_API_KEY", ""),
            elevenlabs_key=g("ELEVENLABS_API_KEY", ""),
        )

    def have_live_keys(self) -> dict[str, bool]:
        return {
            "anthropic": bool(self.anthropic_key),
            "openai": bool(self.openai_key),
            "elevenlabs": bool(self.elevenlabs_key),
        }


if __name__ == "__main__":
    c = Config.load()
    print("Config loaded:")
    for k, v in c.__dict__.items():
        if k.endswith("_key"):
            v = f"<set:{len(v)}>" if v else "<missing>"
        print(f"  {k} = {v}")
    print("live keys:", c.have_live_keys())
    print("OUTPUT_ROOT:", OUTPUT_ROOT)
