#!/usr/bin/env python3
"""
run_dialogue_pipeline.py - end-to-end AI-news two-host Shorts pipeline.

    topic_fetcher.fetch_fresh_topic()   -> one fresh AI-news item
    dialogue_writer.write_dialogue()    -> Max/Iris two-host dialogue script
        -> script_manifest.json (record + bridge input)
    story_media_bridge.bridge_script_to_video()  -> final 1080x1920 MP4

This is the AI-tools/news replacement for run_story_pipeline.py's horror path.
Run from the repo root:
    python run_dialogue_pipeline.py [--output-root DIR] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from topic_fetcher import fetch_fresh_topic
from dialogue_writer import write_dialogue
from story_media_bridge import bridge_script_to_video

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None

logger = logging.getLogger("RevenueMachine.DialoguePipeline")

NICHE = "ai_tools"


def _slugify(value: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return (slug[:max_len].strip("-") or "ai-news") if slug else "ai-news"


def run(output_root: str = "generated/dialogue_pipeline", dry_run: bool = False) -> int:
    """Fetch -> write dialogue -> manifest -> (optionally) render. Returns exit code."""
    topic = fetch_fresh_topic(mark_seen=not dry_run)
    if not topic:
        print("No fresh AI-news topic available right now. Try again later.", file=sys.stderr)
        return 1
    logger.info("Topic: %s", topic.get("title", ""))

    script = write_dialogue(topic)
    logger.info(
        "Dialogue: %d turns, %d words, mode=%s",
        len(script.get("turns", [])),
        script.get("words", 0),
        script.get("live_generation_mode", ""),
    )
    if script.get("accuracy_warning"):
        logger.warning("Accuracy warning recorded: %s", script["accuracy_warning"])

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(output_root) / f"{stamp}_{_slugify(script.get('title', ''))}"
    script_dir = run_dir / "script"
    script_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "created_at": datetime.now().isoformat(),
        "niche": NICHE,
        "topic": topic,
        "script": script,
    }
    manifest_path = script_dir / "script_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Script manifest: %s", manifest_path)

    if dry_run:
        print("DRY RUN - script manifest written, video render skipped.")
        print("run dir:", run_dir)
        print("title:", script.get("title", ""))
        return 0

    video_path = bridge_script_to_video(script, output_dir=run_dir / "video", niche=NICHE)
    print("topic:", topic.get("title", ""))
    print("dialogue turns:", len(script.get("turns", [])))
    print("manifest:", manifest_path)
    print("output video:", video_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if load_dotenv is not None:
        load_dotenv()

    parser = argparse.ArgumentParser(description="AI-news two-host Shorts pipeline.")
    parser.add_argument("--output-root", default="generated/dialogue_pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the script manifest but skip the (slow) video render.",
    )
    args = parser.parse_args(argv)

    try:
        return run(output_root=args.output_root, dry_run=args.dry_run)
    except (RuntimeError, ValueError) as exc:
        print(f"dialogue pipeline failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
