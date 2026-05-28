"""pivot.pipeline.smoke - acceptance harness (Phase 4 spec, section 7).

Runs the synthetic end-to-end batch (no paid keys) and asserts every acceptance
criterion via ffprobe. This is the "done" gate for the offline-verifiable pipeline;
live API stages still need their keys to be exercised against real providers.

Run:  python3 -m pivot.pipeline.smoke
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from . import script
from .batch import _synthetic_producers, run_batch
from .config import OUTPUT_ROOT, Config
from .render_stills import stream_info
from .tts import probe_duration

PASS, FAIL = "PASS", "FAIL"


def _est_cost(n_segments: int, total_chars: int) -> float:
    """Premium-profile marginal cost estimate per video (rough guard)."""
    images = n_segments * 0.08          # DALL-E 3 1024x1536
    voice = total_chars * 0.00003       # ElevenLabs turbo, approx per-char
    llm = 0.01                          # Claude Sonnet, tiny output
    return round(images + voice + llm, 3)


def main() -> int:
    cfg = Config.load()
    results: list[tuple[str, bool, str]] = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    # 1. script contract
    try:
        script.validate_script(script.SAMPLE, cfg)
        segs = script.SAMPLE["segments"]
        words = sum(len(s["narration"].split()) for s in segs)
        hook_ok = segs[0]["narration"].lower().startswith("if ")
        check("script.segment_count", cfg.min_segments <= len(segs) <= cfg.max_segments,
              f"{len(segs)} segments")
        check("script.hook_if", hook_ok, segs[0]["narration"][:24])
        check("script.word_count", 70 <= words <= 100, f"{words} words")
    except Exception as e:
        check("script.validate", False, str(e))

    # run synthetic batch
    manifest = run_batch(["why people suddenly go quiet"], cfg,
                         producers=_synthetic_producers())
    batch_dir = OUTPUT_ROOT / manifest["batch"]
    try:
        item = manifest["items"][0]
        check("batch.item_ok", item.get("status") == "awaiting_captions", item.get("status"))
        mp4 = Path(item["mp4"])
        check("batch.mp4_exists", mp4.exists(), str(mp4))

        item_dir = mp4.parent
        seg_audios = sorted(item_dir.glob("seg_*/seg_*.mp3"))
        # 2. tts durations
        durs = [probe_duration(a) for a in seg_audios]
        check("tts.per_segment_audio", len(seg_audios) == item["segments"],
              f"{len(seg_audios)} audios")
        check("tts.durations_2_6s", all(2.0 <= d <= 6.0 for d in durs), f"{[round(x,1) for x in durs]}")

        # 3. images
        imgs = sorted(item_dir.glob("seg_*/img_*.png"))
        check("images.per_segment", len(imgs) == item["segments"], f"{len(imgs)} images")

        # 4. render acceptance
        si = stream_info(mp4)
        expected = sum(durs)
        check("render.resolution", si["width"] == 1080 and si["height"] == 1920,
              f"{si['width']}x{si['height']}")
        check("render.fps_30", si["fps"] == 30.0, str(si["fps"]))
        check("render.streams", si["video_streams"] == 1 and si["audio_streams"] == 1
              and si["subtitle_streams"] == 0,
              f"v{si['video_streams']}/a{si['audio_streams']}/s{si['subtitle_streams']}")
        check("render.duration_matches", abs(si["duration_s"] - expected) < 0.5,
              f"got {si['duration_s']:.2f}s vs {expected:.2f}s")

        # 5. manifest
        mpath = batch_dir / "manifest.json"
        mdata = json.loads(mpath.read_text())
        check("manifest.exists", mpath.exists(), str(mpath))
        check("manifest.all_awaiting", all(
            i.get("status") == "awaiting_captions" for i in mdata["items"]),
            "statuses ok")

        # 6. cost guard (estimate)
        total_chars = sum(len(s["narration"]) for s in script.SAMPLE["segments"])
        est = _est_cost(item["segments"], total_chars)
        check("cost.under_1usd", est <= 1.0, f"~${est}/video premium")
    finally:
        shutil.rmtree(batch_dir, ignore_errors=True)

    print("\n=== Phase 5 acceptance ===")
    allok = True
    for name, ok, detail in results:
        allok &= ok
        print(f"  [{PASS if ok else FAIL}] {name:28} {detail}")
    print("\nRESULT:", "ALL CHECKS PASS" if allok else "FAILURES PRESENT")
    print("(note: live script/TTS/image stages require their API keys to exercise "
          "real providers; this harness verifies structure + render offline.)")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
