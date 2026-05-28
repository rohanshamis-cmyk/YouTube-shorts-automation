# FROZEN — do not touch this session

The previous AI-news two-host dialogue pipeline stays as a fallback. The hard
rollback anchor is the git tag `pre-pivot-freeze` (commit 6f7f0d8).

## Do NOT edit these files

- `dialogue_writer.py`        — two-host Max/Iris dialogue writer
- `topic_fetcher.py`          — AI-news topic source wrapper
- `run_dialogue_pipeline.py`  — end-to-end AI-news pipeline entrypoint

## Also leave alone (wired to the frozen path)

- `story_media_bridge.py`     — bridges dialogue script -> media stack

## Reuse WITHOUT modifying (shared media stack)

- `voiceover.py`              — VoiceoverEngine (use single-voice path)
- `video_generator.py`        — assemble(); call with add_captions=False
- `topic_finder.py`           — RSS/Reddit dedup (if new niche needs feeds)
- `uploader.py`               — YouTube upload (assess before reuse in Phase 4)

## Rollback

    git checkout pre-pivot-freeze

All new work lives under `pivot/`.
