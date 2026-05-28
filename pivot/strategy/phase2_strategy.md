# Phase 2 — Pivot Strategy

**Decision (locked):** Option 2 — **original / AI faceless content**. Grounded in data,
not preference: Phase 1.5 shows original content medians (Decoded Logic 403k, Couple of
horrorz 91k, Cracked 84k) match or beat every *reliable* clip-aggregator median from
Phase 1, with **zero copyright/Content-ID exposure**.

All figures below trace to `pivot/data/competitor_summary.md` and
`pivot/data/phase1.5_summary.md`. Format/voice details marked *(inferred)* still need a
human eyeball (playback blocked in this environment).

---

## 1. Niche selection

**Primary: AI psychology / behavioral explainer.**
Rationale — highest proven median in the entire study (403k), fully original, evergreen,
cheapest to produce (script + TTS + simple text/visual), no sourcing or legal risk.

**Secondary (validate in parallel, smaller budget): scary/horror micro-narration.**
91k proven median, low legal risk if stories are original or attributed.

**Deprioritized:** reddit narration (84k median but YouTube is actively demoting
low-effort Reddit TTS — platform risk) and historical factoid (24k median, crowded,
incumbent-dominated by History Bypass).

## 2. Positioning & differentiation

The winning psychology format (Decoded Logic) is a **"If [relatable social scenario]🤫"
curiosity-gap** template. It works but is commoditized and shallow. Our wedge:

- **Same hook mechanics, deeper payoff.** Keep the irresistible "If X happens to you…"
  open, but resolve with one *genuinely* useful, citable behavioral insight rather than
  a vague tease. Higher rewatch + share + save (the signals Shorts ranks on).
- **A recognizable visual/voice identity** (consistent palette, caption style, voice)
  so the channel compounds brand recall — most competitors are visually generic.
- **Series structure** ("Dark Psychology 101", "Why You Do That") to drive multi-video
  sessions, which the algorithm rewards.

## 3. Format spec (the product Phase 5 will build to)

| Attribute | Primary (psychology) | Secondary (horror) |
|---|---|---|
| Length | **28–38s** | **20–60s** |
| Hook (0–2s) | "If [scenario]🤫" curiosity gap | cold-open dread line |
| Body | 1 insight, 2–3 beats, on-screen captions | single escalating payoff |
| Voice *(inferred)* | TTS, calm/confident | TTS, low/atmospheric |
| Visuals | text-forward + simple b-roll/AI stills | atmospheric stills/loop |
| CTA | soft series tease ("Part 2…") | "follow for nightly story" |

## 4. Cadence & KPI

- **Cadence: ~1 short/day (≈20–30/mo).** Matches Decoded Logic's selective 20-post
  rhythm — the data is unambiguous that **selectivity beats volume** (Decoded 20→403k vs
  AITAH 50→7k). Do NOT chase 50+/mo.
- **Primary KPI: median views/short.** NOT subscribers (views are decoupled from subs).
- **Secondary KPIs:** save rate + rewatch (Shorts ranking signals), then watch-through.

## 5. Targets & success gates

Benchmarked against the reference set, conservative for a 0-subscriber start:

| Horizon | Median views/short | Read |
|---|--:|---|
| Day 30 | ≥ 5k | format/pipeline functioning |
| Day 60 | ≥ 25k | hook is landing; in clip-channel range |
| Day 90 | ≥ 50k | competitive; matches Cracked/established tier |
| Upside | 400k+ | Decoded-Logic tier — treat as ceiling, not plan |

Kill/pivot gate: if Day-60 median < 5k across 40+ posts, the hook/niche is wrong —
revisit niche before scaling spend.

## 6. Content pipeline (outline only — built in Phase 5, NOT now)

Idea bank → script (hook + insight + CTA) → TTS voice → caption/visual assembly →
render vertical 1080×1920 → human QA → schedule/upload. Phase 3 designs this pipeline;
Phase 4 specs the tooling; Phase 5 implements. **No product code is written until then.**

## 7. Monetization

- **Primary:** YouTube Shorts ad revenue (RPM is low but original content is fully
  monetizable — the whole point of avoiding clips).
- **Secondary:** affiliate/lead-gen into psychology/self-improvement products; the repo
  already has `affiliate_manager.py` and `cross_poster.py` to repurpose.
- **Tertiary:** cross-post identical assets to TikTok/Reels/IG (existing `cross_poster.py`).

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| YouTube "inauthentic/mass-produced content" policy on AI Shorts | original scripts + real insight + human QA gate; avoid templated spam volume |
| Format commoditized (many psychology clones) | differentiation in §2 (depth + brand identity) |
| AI-voice fatigue / detection *(inferred risk)* | invest in one high-quality consistent voice; consider light human VO if median stalls |
| Single-niche dependency | horror secondary track de-risks |
| "AI vs human" assumption wrong | **verify by watching reference channels before Phase 3** |

## 9. Immediate next steps

1. **Human verification (blocks Phase 3):** watch 2–3 Decoded Logic + Couple of horrorz
   shorts — confirm AI-narration, capture exact hook/visual structure.
2. Lock the psychology format template from that observation.
3. Proceed to **Phase 3 (pipeline design)**.
