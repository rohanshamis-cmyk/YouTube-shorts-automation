# Phase 4 — Build Spec (implement in Phase 5)

Architecture = **Candidate B** (standalone still-image renderer). Decisions locked:
**Premium providers** (ElevenLabs male + DALL-E 3), **per-segment audio**, **Submagic
manual** (pipeline ends at a clean caption-free MP4). This spec is precise enough to
implement directly. **Still no code until Phase 5.**

## Module layout (all new code lives under `pivot/`, frozen repo untouched)
```
pivot/pipeline/
  config.py          # load .env + defaults (reuses existing keys)
  script.py          # psychology-listicle generator (reuses script_generator routing)
  tts.py             # thin per-segment wrapper over voiceover.VoiceoverEngine
  images.py          # thin per-segment wrapper over image_providers (DALL-E 3)
  render_stills.py   # NEW CORE: zoompan + concat + mux, caption-free
  batch.py           # orchestrate N videos, write manifest, stage for Submagic
  smoke.py           # ffprobe-based acceptance checks (mirrors repo *_smoke.py)
pivot/output/batch_<UTC>/   # rendered MP4s + manifest.json
```

## Reuse contracts (verified signatures)
- `voiceover.VoiceoverEngine.generate(text, output_dir, voice_id) -> Path` (.mp3, -14 LUFS, silence-trimmed)
- `image_providers.openai_image_provider.OpenAIImageProvider.generate_image(shot_id, prompt, output_path) -> GeneratedImageResult`
- Script: route via existing `script_generator` / Anthropic SDK pattern (`claude-sonnet-4-6`)
- Env already present: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`

## 1. Script — `script.py`
One LLM call per video emits the full structured script **including per-segment image
prompts** (no separate planner needed). Output JSON contract:
```json
{
  "topic": "string",
  "voice_id": "string",
  "segments": [
    {"index": 0, "role": "hook",    "narration": "If someone keeps staring at you...", "image_prompt": "..."},
    {"index": 1, "role": "framing", "narration": "...", "image_prompt": "..."},
    {"index": 2, "role": "step",    "narration": "...", "image_prompt": "..."},
    {"index": 9, "role": "reframe", "narration": "...", "image_prompt": "..."}
  ],
  "meta": {"title": "string", "description": "string", "tags": ["string"]}
}
```
Prompt rules (enforced + validated):
- `segments[0].role == "hook"` and `narration` starts with `"If "`.
- 6–9 segments total; each `narration` one sentence ≈ 10–14 words (≈3–5s @ 2.9 wps);
  whole script ≈ 80–95 words (≈30s). Structure order: hook → framing → 3–5 steps → reframe.
- Each `image_prompt` is a literal, faceless, no-text scene + a fixed **style suffix**
  (consistent palette/aesthetic) appended in `config.py` for brand consistency.
- `meta.title` ≤ 80 chars; `tags` psychology/self-improvement.

## 2. TTS — `tts.py` (per-segment, decided)
For each segment: `VoiceoverEngine.generate(narration, seg_dir, voice_id)` →
`seg_{index}.mp3`. Probe duration with `ffprobe` → `dur_s`. That `dur_s` sets the
segment's video length (tight image↔narration sync). Voice = male
(`ELEVENLABS_VOICE_ID` or VOICE_OPTIONS Arnold/Adam).

## 3. Images — `images.py`
For each segment: `generate_image(f"seg{index}", image_prompt, seg_dir/img_{index}.png)`,
`IMAGE_SIZE=1024x1536` (closest vertical; renderer up-scales/crops to 1080×1920).

## 4. Renderer — `render_stills.py` (the net-new core)
Per segment, build a video+audio clip, then concat. **No captions. No Pexels.**

Per-segment ffmpeg (image → motion clip, duration = its audio `dur_s`, frames = `round(dur_s*30)`):
```
ffmpeg -y -loop 1 -i img_{i}.png -i seg_{i}.mp3 -filter_complex \
 "[0:v]scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,\
  zoompan=z='min(zoom+0.0015,1.15)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':\
  s=1080x1920:fps=30,format=yuv420p[v]" \
 -map "[v]" -map 1:a -c:v libx264 -preset veryfast -crf 20 -c:a aac -b:a 192k \
 -t {dur_s} -movflags +faststart seg_{i}.mp4
```
- **Up-scale to 2160×3840 before `zoompan`** — avoids the well-known `zoompan` integer
  jitter on stills.
- Alternate zoom-in (`zoom+0.0015`) / zoom-out (start 1.15, `zoom-0.0015`) per odd/even
  segment for visual variety.
- Concat via demuxer: `ffmpeg -f concat -safe 0 -i list.txt -c copy <slug>.mp4`.
- **Music:** OFF by default (spec = voiceover only); optional `--music` reuses the
  legacy sidechain-duck approach if ever wanted.
- Output: `pivot/output/batch_<UTC>/<slug>.mp4` — 1080×1920, 30fps, audio baked,
  **zero subtitle/caption streams**.

## 5. Batch + manifest — `batch.py`
Input: list of topics (or N + a topic seed). Per item run script→tts→images→render.
Write `manifest.json`:
```json
{"batch": "batch_<UTC>", "created": "<iso>", "profile": "premium",
 "items": [{"slug": "...", "topic": "...", "mp4": "path", "title": "...",
            "description": "...", "tags": ["..."], "duration_s": 0.0,
            "status": "awaiting_captions"}]}
```
`status: "awaiting_captions"` = staged for Submagic. Upload is a **separate** step run
after Submagic re-import (reuse `uploader.py` later); NOT chained here.

## 6. Config — `config.py` (new .env keys, append to .env.template)
```
PIVOT_VOICE_ID=            # male voice; falls back to VOICE_OPTIONS
PIVOT_IMAGE_SIZE=1024x1536
PIVOT_ZOOM_RATE=0.0015
PIVOT_ZOOM_MAX=1.15
PIVOT_MUSIC=off
PIVOT_SCRIPT_MODEL=claude-sonnet-4-6
```

## 7. Acceptance criteria — `smoke.py` (ffprobe-based, gates Phase 5 "done")
1. **script:** valid JSON vs contract; 6–9 segments; `segments[0]` hook starts `"If "`;
   total words 70–100.
2. **tts:** one mp3/segment; each duration 2–6s.
3. **images:** one PNG/segment; 1024×1536.
4. **render:** output is `1080x1920`, `30` fps; total duration == Σ segment audio
   durations ± 0.2s; has exactly **1 video + 1 audio stream, 0 subtitle streams**;
   `+faststart` present.
5. **manifest:** every item `status=awaiting_captions` with a real `mp4` path.
6. **cost guard:** log per-video API spend; assert ≤ ~$1.00/video (Premium).

## Cost recap (Premium, 30/mo): ~$24/mo. Submagic ~$16–25/mo separate.

## Phase 5 build order (so each piece is testable before the next)
`config.py` → `script.py` (+schema validation) → `tts.py` → `images.py` →
`render_stills.py` (the risk center — build/verify a single segment first, then concat)
→ `batch.py` → `smoke.py`. Verify one full video end-to-end before any batch run.
