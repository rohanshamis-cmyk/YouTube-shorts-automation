# Confirmed Format Spec — Build Target (human-verified)

Source: human watched 3–4 Decoded Logic shorts (2026-05-23). This supersedes all
*(inferred)* format notes in earlier phase docs. Phase 3 pipeline designs build to THIS.

## Niche
- **Primary:** AI psychology / behavioral explainer.
- **Secondary:** horror micro-narration.

## Hook (0–2s) — the load-bearing element
Pattern: **conditional + second person + primal emotional trigger.**
`"If [emotionally charged second-person scenario]..."`
- "If someone starts following you"
- "If your crush is near you"
- "If your girlfriend is cheating"
Triggers to mine: fear, social anxiety, betrayal, status.

## Structure (listicle psychology — forces watch-through)
1. **"If [scenario]" hook**
2. **Problem framing** — why this matters to you
3. **3–5 numbered steps / tips / signs**
4. **Closing reframe**

## Voice
- High-quality AI TTS, **confident male, moderate pace**.
- Reference quality: ElevenLabs or equivalent.

## Visuals — OUR realistic approach (NOT Decoded Logic's)
- Decoded Logic uses 3D faceless characters + anime fragments + stock, beat-synced.
  **This is out of scope at our $30–100/mo budget — do not attempt to match it.**
- **Our approach:** AI-generated image per scene segment, **1 image / 3–5s**
  (Flux or DALL-E), with **Ken Burns motion (zoom/pan) via ffmpeg**.
- Bet: hook + script carry the video; visuals are supporting, not the differentiator.

## Captions
- **Word-by-word highlight, fast, no delay.**
- **Added externally in Submagic — DO NOT render captions into the video.**
- Pipeline output must be a clean (caption-free) vertical video.

## Output format
- Vertical **1080×1920**, caption-free, audio baked in.

## Budget constraint
- **$30–100/mo** all-in (TTS + image gen + any LLM). Every Phase 3 design must price
  against this ceiling.
