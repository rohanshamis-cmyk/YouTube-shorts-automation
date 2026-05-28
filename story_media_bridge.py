#!/usr/bin/env python3
"""
story_media_bridge.py - Bridge: story-pipeline script -> Revenue Machine media stack.

Path 1 (audio-first):
    script["narration"]
        -> VoiceoverEngine (TTS)              -> narration .mp3
        -> VideoGenerator.fetch_stock_footage -> Pexels/Pixabay clips
        -> VideoGenerator.assemble (FFmpeg)   -> single 1080x1920 .mp4

Closes gap-map blockers 1 (no audio), 2 (visual-unit mismatch), 3 (no assembly),
plus gaps 6 (mystery niche) and 7 (media config). Blocker 4 (upload metadata) is
intentionally out of scope - upload the MP4 manually for v1.

No Runway, no OpenAI image generation, no keyframe path. Cheapest working video.

Run from the repo root (video_generator writes cache/ and assets/ relative to cwd):
    python story_media_bridge.py <script_manifest.json | run_dir> [--output-dir DIR] [--niche mystery]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from voiceover import VoiceoverEngine
from video_generator import VideoGenerator

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency fallback
    load_dotenv = None

__all__ = [
    "build_media_config",
    "derive_footage_queries",
    "load_script",
    "bridge_script_to_video",
]

logger = logging.getLogger("RevenueMachine.StoryMediaBridge")

# Niches the media stack understands - voiceover.VOICE_OPTIONS and
# video_generator.BACKGROUND_MUSIC. "mystery" was added for the story pipeline.
_MEDIA_NICHES = {"ai_tools", "finance", "motivation", "mystery"}
_DEFAULT_NICHE = "mystery"

# Mood b-roll for suspense/horror shorts. The story script carries no
# `visual_queries` field, so these stand in (gap-map blocker 2).
_MYSTERY_BROLL = [
    "dark empty hallway at night",
    "abandoned house interior dim light",
    "foggy forest path cinematic",
    "flickering ceiling light eerie",
    "rain on window at night moody",
    "empty street at night streetlight",
    "shadowy staircase low light",
    "closed door dark room",
    "dim apartment corridor night",
    "candle flame in darkness",
    "silhouette in doorway backlit",
    "old building at dusk eerie",
    "moonlight through curtains",
    "dark parking garage night",
]

# Mood b-roll for AI-tools/news shorts (the two-host dialogue niche).
_TECH_BROLL = [
    "data center servers glowing",
    "code on screen scrolling",
    "circuit board macro closeup",
    "person typing laptop dark room",
    "abstract ai neural network animation",
    "robot arm factory automation",
    "city skyline night data lights",
    "futuristic interface hologram",
    "smartphone app screen closeup",
    "office team looking at screens",
    "glowing fiber optic cables",
    "developer workspace multiple monitors",
]

_NICHE_BROLL = {
    "ai_tools": _TECH_BROLL,
    "mystery": _MYSTERY_BROLL,
}

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at",
    "my", "your", "his", "her", "their", "what", "who", "when", "after",
    "before", "with", "for", "that", "this", "from", "into", "was", "were",
}


def build_media_config(niche: str = _DEFAULT_NICHE) -> dict[str, Any]:
    """Build the config dict the Revenue Machine media modules expect (gap 7).

    The story pipeline builds only a narrow script/provider config; the media
    stack needs TTS and stock-footage keys plus a NICHE the modules recognise.
    """
    if niche not in _MEDIA_NICHES:
        logger.warning("Unknown media niche '%s'; falling back to '%s'.", niche, _DEFAULT_NICHE)
        niche = _DEFAULT_NICHE

    def _flt(name: str, default: str) -> float:
        try:
            return float(os.getenv(name, default))
        except (TypeError, ValueError):
            return float(default)

    return {
        "ELEVENLABS_API_KEY": os.getenv("ELEVENLABS_API_KEY", "").strip(),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", "").strip(),
        "PEXELS_API_KEY": os.getenv("PEXELS_API_KEY", "").strip(),
        "PIXABAY_API_KEY": os.getenv("PIXABAY_API_KEY", "").strip(),
        "NICHE": niche,
        "VOICE_PROVIDER": os.getenv("VOICE_PROVIDER", "").strip(),
        "VOICE_ID": os.getenv("VOICE_ID", "").strip(),
        "ELEVENLABS_VOICE_ID": os.getenv("ELEVENLABS_VOICE_ID", "").strip(),
        "VOICE_STABILITY": _flt("VOICE_STABILITY", "0.5"),
        "VOICE_SIMILARITY": _flt("VOICE_SIMILARITY", "0.75"),
    }


def derive_footage_queries(
    script: dict[str, Any], count: int = 6, niche: str = _DEFAULT_NICHE
) -> list[str]:
    """Turn a script into stock-footage search queries (blocker 2).

    video_generator expects `visual_queries`, which the script lacks. We
    synthesise: one title-derived query plus a niche-appropriate b-roll set
    (tech footage for ai_tools, suspense footage for mystery).
    """
    broll = _NICHE_BROLL.get(niche, _MYSTERY_BROLL)
    style = "tech" if niche == "ai_tools" else "dark cinematic"
    queries: list[str] = []

    title = str(script.get("title", "") or "")
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", title)]
    keywords = [w for w in words if len(w) >= 4 and w not in _STOPWORDS]
    if keywords:
        queries.append(" ".join(keywords[:2]) + f" {style}")

    # Rotate the b-roll pool by a title-seeded offset so different scripts do
    # not all open with the same clips.
    offset = (sum(ord(c) for c in title) % len(broll)) if title else 0
    rotated = broll[offset:] + broll[:offset]
    for query in rotated:
        if len(queries) >= count:
            break
        if query not in queries:
            queries.append(query)

    # Guarantee at least 3 clips so assemble() has scenes to distribute.
    idx = 0
    while len(queries) < 3:
        queries.append(broll[idx % len(broll)])
        idx += 1

    return queries[:count]


def load_script(source: str | Path) -> dict[str, Any]:
    """Load a story-pipeline script dict from a manifest path or run directory.

    Accepts: a story_pipeline run directory, a script_manifest.json path, or a
    raw script .json. Raises if there is no usable narration text.
    """
    path = Path(source)

    if path.is_dir():
        candidates = [path / "script" / "script_manifest.json", path / "script_manifest.json"]
        manifest = next((c for c in candidates if c.is_file()), None)
        if manifest is None:
            raise FileNotFoundError(
                f"No script_manifest.json found under '{path}'. "
                "Pass the manifest file or a story_pipeline run directory."
            )
        path = manifest

    if not path.is_file():
        raise FileNotFoundError(f"Script source not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    # script_manifest.json wraps the script under a "script" key; a raw script
    # dict is also accepted.
    script = data.get("script") if isinstance(data, dict) and "script" in data else data
    if not isinstance(script, dict):
        raise ValueError(f"Could not read a script object from {path}")

    narration = str(script.get("narration", "") or "").strip()
    if not narration:
        raise ValueError(f"Script from {path} has no 'narration' text - cannot build audio.")
    return script


def _preflight(config: dict[str, Any]) -> None:
    """Fail early with actionable messages instead of deep inside the media stack."""
    if not (config.get("ELEVENLABS_API_KEY") or config.get("OPENAI_API_KEY")):
        raise RuntimeError(
            "No TTS provider available. Set ELEVENLABS_API_KEY or OPENAI_API_KEY "
            "in .env - voiceover needs one to generate narration audio."
        )
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError(
            "ffmpeg/ffprobe not found on PATH. Install ffmpeg - video_generator "
            "needs it to assemble the MP4."
        )
    if not (config.get("PEXELS_API_KEY") or config.get("PIXABAY_API_KEY")):
        logger.warning(
            "No PEXELS_API_KEY/PIXABAY_API_KEY set - video will use solid-colour "
            "placeholder backgrounds instead of stock footage (still a valid MP4)."
        )


def _generate_dialogue_audio(
    script: dict[str, Any], output_dir: Path, config: dict[str, Any]
) -> Path:
    """Voice each dialogue turn with its speaker's voice, concat into one track.

    Each turn is read with its persona's ElevenLabs voice (dialogue_writer.
    PERSONAS), so the two hosts sound like two distinct people. Per-turn clips
    are concatenated via the ffmpeg concat demuxer into a single narration MP3.
    """
    from dialogue_writer import PERSONAS

    turns = script.get("turns", [])
    voice = VoiceoverEngine(config)
    clip_paths: list[Path] = []
    for idx, turn in enumerate(turns):
        speaker = str(turn.get("speaker", "")).strip().lower()
        text = str(turn.get("text", "") or "").strip()
        if not text:
            continue
        persona = PERSONAS.get(speaker)
        if persona is None:
            logger.warning("Unknown speaker '%s' in turn %d; skipping.", speaker, idx)
            continue
        clip = voice.generate(text=text, output_dir=output_dir, voice_id=persona["voice_id"])
        clip_paths.append(Path(clip))

    if not clip_paths:
        raise ValueError("Dialogue produced no voiceable turns.")
    if len(clip_paths) == 1:
        return clip_paths[0]

    # Re-encode on concat: per-clip MP3 framing varies, so a stream copy can
    # desync; libmp3lame yields one clean continuous track.
    list_file = output_dir / "_dialogue_clips.txt"
    list_file.write_text(
        "".join(f"file '{p.resolve()}'\n" for p in clip_paths), encoding="utf-8"
    )
    combined = output_dir / "dialogue_narration.mp3"
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c:a", "libmp3lame", "-q:a", "2", str(combined)],
        capture_output=True,
    )
    list_file.unlink(missing_ok=True)
    if result.returncode != 0 or not combined.is_file():
        raise RuntimeError(
            "Failed to concatenate dialogue audio: "
            + result.stderr.decode("utf-8", "ignore")[-300:]
        )
    logger.info("Dialogue audio: %d turns -> %s", len(clip_paths), combined)
    return combined


def bridge_script_to_video(
    script: dict[str, Any],
    output_dir: str | Path,
    niche: str = _DEFAULT_NICHE,
    config: dict[str, Any] | None = None,
) -> Path:
    """Audio-first bridge: story script -> narration audio -> footage -> one MP4.

    Closes gap-map blockers 1 (audio), 2 (footage), 3 (assembly). Returns the
    path to the final assembled video.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = config or build_media_config(niche)

    _preflight(config)

    narration = str(script.get("narration", "") or "").strip()
    if not narration:
        raise ValueError("Script has no 'narration' text - cannot build audio.")
    logger.info("Bridge start | niche=%s | narration=%d chars", config["NICHE"], len(narration))

    # Blocker 1: the script carries no audio. Generate it now. A two-host
    # dialogue (`turns`) is voiced per turn with alternating voices; a plain
    # single-narration script uses one voice.
    turns = script.get("turns")
    if isinstance(turns, list) and turns:
        audio_path = _generate_dialogue_audio(script, output_dir, config)
    else:
        voice = VoiceoverEngine(config)
        audio_path = voice.generate(text=narration, output_dir=output_dir)
    logger.info("Narration audio: %s", audio_path)

    # Blocker 2: the script carries no footage. Synthesise queries and
    # fetch stock clips in the shape video_generator expects.
    queries = derive_footage_queries(script, niche=config["NICHE"])
    logger.info("Footage queries: %s", queries)
    video = VideoGenerator(config)
    footage = video.fetch_stock_footage(queries, count=len(queries))

    # Blocker 3: nothing assembled clips + audio + captions into one file.
    video_path = video.assemble(
        audio=audio_path,
        footage=footage,
        script=script,
        output_dir=output_dir,
        add_captions=True,
        add_background_music=True,
    )
    logger.info("Assembled video: %s", video_path)
    return video_path


def _slugify(value: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return (slug[:max_len].strip("-") or "story") if slug else "story"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if load_dotenv is not None:
        load_dotenv()

    parser = argparse.ArgumentParser(
        description="Bridge a story-pipeline script into a single assembled MP4."
    )
    parser.add_argument(
        "source",
        help="Path to script_manifest.json, or a story_pipeline run directory.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Output folder (default: generated/bridge_videos/<timestamp>_<slug>).",
    )
    parser.add_argument(
        "--niche",
        default=_DEFAULT_NICHE,
        help=f"Media niche for voice/music selection (default: {_DEFAULT_NICHE}).",
    )
    args = parser.parse_args(argv)

    try:
        script = load_script(args.source)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"story media bridge failed: {exc}", file=sys.stderr)
        return 1

    output_dir = args.output_dir.strip()
    if not output_dir:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = f"generated/bridge_videos/{stamp}_{_slugify(script.get('title', ''))}"

    try:
        video_path = bridge_script_to_video(script, output_dir=output_dir, niche=args.niche)
    except (RuntimeError, ValueError) as exc:
        print(f"story media bridge failed: {exc}", file=sys.stderr)
        return 1

    print(f"niche: {args.niche}")
    print(f"script title: {script.get('title', '')}")
    print(f"output video: {video_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
