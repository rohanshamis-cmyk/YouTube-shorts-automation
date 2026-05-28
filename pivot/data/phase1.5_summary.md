# Phase 1.5 — Original / AI Faceless Reference Set

**Why:** Phase 1's 12 channels were all clip-aggregators (selection bias). This set
discovers channels *winning with original/AI faceless content* — matching our chosen
direction (Option 2) — so Phase 2 has a comparable baseline. Discovery method: searched
top-viewed Shorts per niche, mapped back to the channels making them (330 channels
found, 40 with repeat signal), then curated 6 English, faceless, active channels across
12k–873k subs. Raw: `original_candidates.json`, `original_reference_data.json`.

## Same caveats as Phase 1
- `shorts/30d` capped at 50 (no pagination); "50" = ≥50.
- "AI vs human narration" is **inferred** — playback blocked, read from titles/format/
  duration only.
- Inner Mind posted **1** short in 30d (n=1) → its median is noise; flagged, not used.

## Results (30-day window)

| Channel | Niche | Subs | Shorts/30d | Median | Min–Max | Typical len |
|---|---|--:|--:|--:|--|--:|
| **Decoded Logic** | AI psychology | 110k | 20 | **403,525** | 24k–2.53M | 31–37s |
| Couple of horrorz | scary narration | 417k | 30 | 91,293 | 20k–309k | 11–89s |
| Cracked Stories | reddit narration | 12k | ≥50 | 83,759 | 5k–231k | **136–148s** |
| History Bypass | historical factoid | 873k | ≥50 | 24,455 | 5k–483k | 3–15s |
| AITAH Confessions | reddit narration | 30.6k | ≥50 | 7,244 | 3k–19k | 38–51s |
| Inner Mind | AI psychology | 82k | 1 | (n=1) | 5,986 | 36s |

## Findings

**1. Original/AI faceless content competes with — and beats — clip aggregation.**
Decoded Logic's **403k median** exceeds *every reliable clip-channel median in Phase 1*
(the clip leaders' high medians were low-n lottery spikes). Couple of horrorz (91k) and
Cracked Stories (84k) also beat most Phase 1 clip medians. **Option 2 is not a
compromise — the data supports it as competitive on views, with zero copyright risk.**

**2. The selectivity > volume law repeats, hard.**
- Decoded Logic: **20 posts → 403k median.**
- AITAH Confessions: ≥50 posts → **7k median.**
- History Bypass: ≥50 posts → 24k median (mature channel, volume-diluted).
Lower cadence + higher curation wins again. Confirmed across both samples.

**3. Subs stay decoupled from views.** Cracked Stories (12k subs) medians 84k; History
Bypass (873k subs) medians 24k. Views/short remains the only KPI that matters.

**4. Optimal length is niche-specific (inferred from duration data):**
- **AI psychology/explainer → ~30s** ("If [relatable scenario]🤫" curiosity hook).
- **Reddit narration → 130s+** (Cracked's 2.3min full-story format medians 11× AITAH's
  40s clipped format — the *long* reddit format wins).
- **Horror → 20–90s** (single creepy payoff).
- **History factoid → 3–15s** (rapid single fact).

## Niche ranking for the pivot (median × low-legal-risk × low-production-cost)

| Rank | Niche | Proven median | Legal risk | Production cost | Verdict |
|---|---|--:|---|---|---|
| **1** | **AI psychology/explainer** | **403k** | none (original) | low (TTS + simple visuals) | **primary bet** |
| 2 | Scary/horror narration | 91k | low (orig. or attributed) | medium (atmosphere/visuals) | strong secondary |
| 3 | Reddit narration | 84k | medium (Reddit-sourced, YT cracking down on low-effort TTS) | low | viable, saturated |
| 4 | Historical factoid | 24k | none | low–med | crowded, incumbent-dominated |

**Recommendation into Phase 2: lead with the AI psychology/explainer format** (best
proven median, fully original, cheapest to produce, evergreen, no copyright exposure),
with horror narration as a tested secondary.

## Needs human verification (playback blocked)
- Confirm Decoded Logic / Couple of horrorz are AI/TTS-narrated vs. human voice.
- Watch 2–3 Decoded Logic shorts to capture the exact hook/visual structure before we
  replicate it in Phase 2.
