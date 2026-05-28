"""pivot.pipeline.tts - per-segment voiceover.

Thin wrapper over the existing voiceover.VoiceoverEngine (ElevenLabs primary, OpenAI
fallback). Per-segment audio (decided in Phase 3) gives tight image<->narration sync:
each segment's video length = that segment's measured audio duration.

voiceover.py is lazy-imported (it pulls heavy SDKs); ffprobe duration measurement is
dependency-free and tested without keys.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .config import Config


def probe_duration(path: Path) -> float:
    """Return media duration in seconds via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def _engine_config(cfg: Config) -> dict:
    """Build the dict VoiceoverEngine expects, from env + Config."""
    return {
        "ELEVENLABS_API_KEY": cfg.elevenlabs_key,
        "OPENAI_API_KEY": cfg.openai_key,
        "VOICE_ID": cfg.voice_id,
        "ELEVENLABS_VOICE_ID": cfg.voice_id,
        "VOICE_PROVIDER": os.environ.get("VOICE_PROVIDER", "auto"),
        "NICHE": os.environ.get("CHANNEL_NICHE", "mystery"),  # deep male default
    }


def make_engine(cfg: Config):
    """Instantiate the real VoiceoverEngine (lazy import)."""
    import voiceover  # noqa: lazy, pulls heavy deps
    return voiceover.VoiceoverEngine(_engine_config(cfg))


def generate_segment_audio(text: str, seg_dir: Path, index: int, cfg: Config,
                           engine=None) -> tuple[Path, float]:
    """Generate one segment's mp3, return (path, duration_s)."""
    seg_dir.mkdir(parents=True, exist_ok=True)
    engine = engine or make_engine(cfg)
    produced = engine.generate(text, seg_dir, cfg.voice_id or None)
    dest = seg_dir / f"seg_{index:02d}.mp3"
    if Path(produced).resolve() != dest.resolve():
        shutil.copy(Path(produced), dest)
    return dest, probe_duration(dest)


if __name__ == "__main__":
    cfg = Config.load()
    print("engine config keys:", sorted(_engine_config(cfg).keys()))
    # key-free verification: ffprobe duration on a synthetic 3.0s tone
    tmp = Path("/tmp/pivot_tts_probe.mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=3.0",
         "-q:a", "9", str(tmp)],
        capture_output=True, check=True,
    )
    d = probe_duration(tmp)
    print(f"probe_duration synthetic tone -> {d:.2f}s")
    assert abs(d - 3.0) < 0.2, f"probe off: {d}"
    print("OK: probe_duration verified (live TTS needs ELEVENLABS/OPENAI keys)")
