# Phase 3 — Candidate Pipeline Designs

Build target = `pivot/strategy/confirmed_format_spec.md` (human-verified). Reuse map =
the repo inventory. **No product code is written until Phase 5** — this doc chooses the
architecture and prices it.

## Reuse map (verified against the repo)

| Stage | Existing asset | Verdict for our spec |
|---|---|---|
| Script | `script_generator.py` (Claude/OpenAI, provider-routed) | Reuse engine, **new prompt** (psychology listicle, not AI-news dialogue) |
| TTS | `voiceover.py` — ElevenLabs + OpenAI fallback, -14 LUFS, silence-trim | **Direct reuse**; male voice (Arnold/Adam or OpenAI `onyx`) |
| Images | `image_providers/openai_image_provider.py` (DALL-E 3 only) | Reuse; optionally add Flux for cost |
| Scene planning | `image_planning/keyframe_builder.py` | Partial reuse for beat→image-prompt mapping |
| Render | `render_batch.py` (ffmpeg 1080×1920) | **Two gaps** — see below |
| Batch/manifest | `batch_generate.py`, `*.sh`, manifest pattern | Reuse pattern |
| Upload | `uploader.py` (YouTube OAuth, resumable) | Reuse, but see Submagic seam |

### The two render gaps (confirmed by code inspection)
1. **No still-image Ken Burns.** `render_batch.py` has no `zoompan`; its motion is
   crop+scale on **Pexels video clips**. Our spec needs `zoompan` zoom/pan on **AI
   still images** (1 image / 3–5s). Net-new.
2. **Captions are baked in.** The renderer hard-codes caption panels/fonts into every
   segment. Our spec requires **caption-free** output (Submagic adds captions later).
   Need a caption-free render path.

### The Submagic seam (affects orchestration in every design)
Captions are added **externally in Submagic**, so the flow cannot be one-shot
render→upload. It is: **generate → render caption-free MP4 → [MANUAL: Submagic captioning]
→ upload.** All designs below output a clean MP4 staged for Submagic; upload is a
separate downstream step, not chained. (Submagic is also a separate ~$16–25/mo cost
outside our pipeline budget.)

---

## Candidate A — Graft onto the legacy renderer

Add a `stills` motion mode (`zoompan`) and a `--no-captions` flag to `render_batch.py`;
reuse everything else in place.

- **New code:** ~2 features inside the 1500-line `render_batch.py`.
- **Pros:** maximal reuse; batch/upload already wired to it.
- **Cons:** `render_batch.py` is large and clip/caption-centric; grafting risks
  regressions in the **frozen** legacy product, and couples our pivot to it. Hard to
  test in isolation.

## Candidate B — New standalone still-image renderer  ★ recommended

A small purpose-built renderer under `pivot/` that does exactly our spec and nothing
else: input = script segments + one AI image per segment + `voice.mp3` →
per-segment `ffmpeg zoompan` → concat → mux voiceover → **caption-free** 1080×1920.
Reuses `script_generator` (new prompt), `voiceover.py`, the image provider, and the
manifest/batch pattern. **Leaves the frozen `render_batch.py` untouched.**

- **New code:** one focused renderer module + a psychology script template + a beat→
  image-prompt planner. Each small and independently testable.
- **Pros:** clean, decoupled, no risk to frozen code, purpose-fit, easy to verify.
- **Cons:** some duplication of ffmpeg concat/mux logic already in the legacy renderer.

## Candidate C — Hosted / animated render (Runway I2V)

Reuse the existing `asset_bridge/runway_asset_bridge.py` to animate each still via
Runway image-to-video (`gen4_turbo`) instead of ffmpeg `zoompan`.

- **Pros:** richer motion, closer to Decoded Logic's feel.
- **Cons:** **breaks the budget.** Runway per-second I2V on ~8 segments/video × 30/mo
  is well above $100/mo. Reserve as a future upgrade if a video proves a hit; not the
  baseline.

---

## Cost (1 video/day = 30/mo, ~30s, ~8 images each) — applies to A & B

| Profile | TTS | Images | LLM | **Total/mo** |
|---|---|---|---|--:|
| **Premium** (ElevenLabs + DALL-E 3) | ~$5 (Starter, 30k chars) | ~$19 (240 × $0.08) | ~$0.15 | **~$24** |
| **Mid** (ElevenLabs + Flux dev) | ~$5 | ~$6 (240 × $0.025) | ~$0.15 | **~$11** |
| **Floor** (OpenAI TTS + Flux schnell) | ~$0.50 | ~$0.72 | ~$0.15 | **~$1.50** |

All profiles sit **under the $30–100/mo ceiling** even at daily cadence — leaving room
to scale to 2–3/day or add the horror track. (Submagic ~$16–25/mo is separate.)
Note: Flux is **not yet in the repo** — choosing Mid/Floor adds a small Flux provider
to build; Premium ships on the existing DALL-E 3 provider with zero new image code.

---

## Recommendation

**Candidate B, Premium profile to start** (ElevenLabs male + DALL-E 3): zero risk to
frozen code, cleanest to build/test, and ships on existing providers so the only new
image work is deferred. At ~$24/mo it's comfortably in budget; drop to Flux (Mid, ~$11)
later purely as a cost optimization once volume justifies building the Flux provider.

### New code Phase 5 will build (B) — for Phase 4 to spec, NOT now
1. Psychology-listicle **script template/prompt** → structured JSON (hook, framing,
   steps[], reframe) + per-segment **image prompts** + segment timing.
2. **Beat→image-prompt planner** (extend `image_planning/keyframe_builder.py`).
3. **Still-image `zoompan` renderer** (the core new piece) — caption-free, 1080×1920,
   voiceover muxed, optional bed music.
4. Thin **batch wrapper + manifest** (reuse pattern), staging clean MP4s for Submagic.

## Open decisions for you
1. **Provider profile:** start Premium (~$24, no new image code) vs Mid (~$11, build Flux first)?
2. **TTS timing model:** one audio for the whole script with segments timed by word-count,
   vs per-segment audio files (tighter image sync, slightly more TTS calls)?
3. **Submagic seam:** confirm captions stay manual in Submagic (pipeline ends at clean
   MP4), or should we eventually pull captioning in-house to re-enable full automation?
