"""pivot.pipeline.batch - orchestrate N Shorts and write a manifest.

Flow per item: script -> images -> per-segment TTS -> render -> manifest entry
(status=awaiting_captions; Submagic captioning + upload are separate downstream steps).

Stage producers are injectable so the whole pipeline can be verified end-to-end with
synthetic stand-ins (no paid keys).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import images as images_mod
from . import render_stills, script, tts
from .config import OUTPUT_ROOT, Config


def slugify(text: str, maxlen: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:maxlen].rstrip("-") or "short"


def write_transcript(out_path: Path, data: dict) -> Path:
    """Write the full narration beside the MP4 for Submagic caption alignment.

    Line 1 is the title plus the hook (the promise the Short opens on). Then one
    block per segment, segment-broken and labelled with index+role, so the spoken
    order maps 1:1 onto the caption track downstream.
    """
    segments = data.get("segments", [])
    meta = data.get("meta", {})
    title = meta.get("title") or data.get("topic", "")
    hook = segments[0]["narration"] if segments else ""
    lines = [f"{title} | HOOK: {hook}", ""]
    for s in segments:
        lines.append(f"[{s['index']:02d} · {s['role']}] {s['narration']}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


@dataclass
class Producers:
    script_fn: Callable[[str, Config], dict] = None
    images_fn: Callable[[list, Path, Config], list] = None
    tts_fn: Callable[[str, Path, int, Config], tuple] = None
    render_fn: Callable[[list, list, Path, Config], dict] = None

    def resolved(self) -> "Producers":
        return Producers(
            script_fn=self.script_fn or script.generate_script,
            images_fn=self.images_fn or images_mod.generate_segment_images,
            tts_fn=self.tts_fn or tts.generate_segment_audio,
            render_fn=self.render_fn or render_stills.render_video,
        )


def run_one(topic: str, batch_dir: Path, cfg: Config, prod: Producers,
            script_attempts: int = 3) -> dict:
    # Script generation validates word count / structure and raises on failure.
    # LLM word-counting is fuzzy, so retry up to script_attempts times before
    # giving up. If all attempts fail we raise; run_batch catches it and records
    # the topic as status=error, so one bad topic never crashes the batch.
    data = None
    last_err: Exception | None = None
    for attempt in range(1, script_attempts + 1):
        try:
            data = prod.script_fn(topic, cfg)
            break
        except Exception as exc:
            last_err = exc
            print(f"[run_one] script attempt {attempt}/{script_attempts} "
                  f"failed for {topic!r}: {exc}")
    if data is None:
        raise RuntimeError(
            f"script generation failed after {script_attempts} attempts: {last_err}")
    segments = data["segments"]
    item_dir = batch_dir / slugify(topic)
    item_dir.mkdir(parents=True, exist_ok=True)

    image_paths = prod.images_fn(segments, item_dir, cfg)
    audio_paths = []
    for s in segments:
        seg_dir = item_dir / f"seg_{s['index']:02d}"
        ap, _dur = prod.tts_fn(s["narration"], seg_dir, s["index"], cfg)
        audio_paths.append(ap)

    mp4 = item_dir / f"{slugify(topic)}.mp4"
    info = prod.render_fn(image_paths, audio_paths, mp4, cfg)

    transcript = write_transcript(mp4.with_suffix(".txt"), data)

    meta = data.get("meta", {})
    return {
        "slug": slugify(topic), "topic": topic, "mp4": str(mp4),
        "transcript": str(transcript),
        "title": meta.get("title", topic), "description": meta.get("description", ""),
        "tags": meta.get("tags", []), "duration_s": round(info["duration_s"], 2),
        "segments": info["segments"], "status": "awaiting_captions",
    }


def run_batch(topics: list[str], cfg: Config | None = None,
              producers: Producers | None = None) -> dict:
    cfg = cfg or Config.load()
    prod = (producers or Producers()).resolved()
    batch_name = f"batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    batch_dir = OUTPUT_ROOT / batch_name
    batch_dir.mkdir(parents=True, exist_ok=True)

    items = []
    for t in topics:
        try:
            items.append(run_one(t, batch_dir, cfg, prod))
        except Exception as exc:
            items.append({"topic": t, "status": "error", "error": str(exc)})

    manifest = {
        "batch": batch_name, "created": datetime.now(timezone.utc).isoformat(),
        "profile": "premium", "items": items,
    }
    (batch_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def _synthetic_producers() -> Producers:
    """Stubs that exercise the real renderer with synthetic script/audio/images."""
    import subprocess

    def fake_script(topic, cfg):
        import copy
        d = copy.deepcopy(script.SAMPLE)
        d["topic"] = topic
        return script.apply_style(d, cfg)

    def fake_tts(text, seg_dir, index, cfg, engine=None):
        seg_dir.mkdir(parents=True, exist_ok=True)
        ap = seg_dir / f"seg_{index:02d}.mp3"
        dur = 2.0 + (index % 3) * 0.6
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency={200+index*30}:duration={dur}", "-q:a", "9", str(ap)],
                       capture_output=True, check=True)
        return ap, dur

    def fake_images(segments, batch_dir, cfg, provider=None):
        return images_mod.generate_segment_images(
            segments, batch_dir, cfg, provider=images_mod._StubProvider())

    return Producers(script_fn=fake_script, images_fn=fake_images, tts_fn=fake_tts)


if __name__ == "__main__":
    cfg = Config.load()
    if cfg.anthropic_key and cfg.openai_key and cfg.elevenlabs_key:
        m = run_batch(["why people remember how you made them feel"], cfg)
    else:
        print("Missing live keys -> synthetic end-to-end batch.")
        t0 = time.time()
        m = run_batch(["why people suddenly go quiet", "how to read confidence"],
                      cfg, producers=_synthetic_producers())
        print(f"batch built in {time.time()-t0:.1f}s")
    for it in m["items"]:
        print(f"  [{it['status']}] {it.get('slug','?')} "
              f"dur={it.get('duration_s','?')}s segs={it.get('segments','?')} "
              f"-> {it.get('mp4','-')}")
    print("manifest:", OUTPUT_ROOT / m["batch"] / "manifest.json")
