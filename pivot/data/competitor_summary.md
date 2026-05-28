# Phase 1 — Competitor Intelligence Summary

**Collected:** 2026-05-23 · **Window:** last 30 days · **Source:** YouTube Data API v3
(channels + search + videos). Raw records in `competitor_data.json`.

## Data-quality caveats (read first)
- **`shorts/30d` is capped at 50.** The search call pulls one page (50 max), no
  pagination. Channels at exactly 50 (goobyclips, historryexposed, imemeonline)
  post **≥50 in 30d** — true cadence is higher, treat as ">=1.6/day".
- **Median is unreliable at low n.** hoodclips13 (n=2), monarchprime (n=2),
  jestlore (n=1) have medians that are basically single data points. Flagged below.
- **Format / "AI vs human" is INFERRED**, not observed. YouTube playback is blocked
  in this environment, so format calls come from title + duration + thumbnail +
  hashtags only. Anything marked *(inferred)* needs a human eyeball before we bet on it.
- **quantumphysics-0 is effectively dead** (1,470 subs, 0 shorts in 30d). Excluded.

## The numbers (sorted by median Shorts views, 30d)

| Channel | Subs | Shorts/30d | Median views | Max views | n caveat |
|---|--:|--:|--:|--:|---|
| hoodclips13 | 726k | 2 | 9,009,975 | 9.0M | n=2, unreliable |
| jestlore | 15.2k | 1 | 258,491 | 258k | n=1, unreliable |
| blindspotdot | 108k | 6 | 234,036 | 27.6M | huge variance |
| shauryaautomotive | 28.1k | 9 | 228,326 | 20.7M | |
| reyshortss | 214k | 15 | 68,510 | 1.3M | |
| goobyclips | 4.6k | ≥50 | 66,754 | 3.8M | cadence capped |
| monarchprime | 187k | 2 | 50,115 | 50k | n=2, unreliable |
| historryexposed | 215k | ≥50 | 49,845 | 14.6M | cadence capped |
| elijahhnfl | 27.3k | 29 | 31,106 | 3.4M | |
| fdggh009 | 16.3k | 11 | 13,786 | 1.8M | |
| imemeonline | 6.9k | ≥50 | 12,304 | 137k | cadence capped |

## 5 forcing questions

### 1. What is actually winning — original faceless content, or clip aggregation?
**Clip aggregation, overwhelmingly.** Every top performer is **repurposed existing
footage** with a text hook overlaid: old Hollywood film clips (historryexposed),
movie scenes (fdggh009 = *Karate Kid*), streamer/celeb clips (reyshortss = IShowSpeed),
sports broadcast clips (elijahhnfl NFL, shauryaautomotive motorsport), TV show clips
(monarchprime = Shark Tank), and reaction/bait compilations (blindspotdot, hoodclips13).
**None of the 12 appear to be AI-generated original content** *(inferred)*. This is the
single most important finding — see Strategic Implications.

### 2. Volume or selectivity — which cadence wins?
**Selectivity beats spray-and-pray.** The three highest *consistent* medians come from
**low-volume curators**: blindspotdot (6 posts, 234k median), shauryaautomotive
(9 posts, 228k median), reyshortss (15 posts, 68k median). The ≥50/30d high-volume
channels split hard: historryexposed wins (50k median, 14.6M max) but imemeonline
floors out (12k median). Volume only pays if every clip is a curated banger; otherwise
it just dilutes. **A new channel should optimize hit-rate per clip, not raw count.**

### 3. Are subscribers the right KPI? (No.)
**Views are fully decoupled from subs on Shorts.** goobyclips (4.6k subs) pulls a
3.8M-view video; shauryaautomotive (28k subs) hit 20.7M. Meanwhile monarchprime (187k
subs) medians 50k. Shorts distribution is algorithm/hook-driven, not audience-driven.
**KPI = views-per-video and hook retention, not subscriber count.**

### 4. What format/length is the sweet spot? *(inferred)*
Two winning clusters:
- **15–24s curated clips** — the biggest *outliers* live here (blindspot 27.6M,
  shaurya 20.7M, historry 14.6M, hoodclips 9M). Tight, single-moment payoff.
- **30–60s narrative clips** — steadier mid-tier (fdggh009, reyshortss, elijahhnfl).
- **5s "tag-dump" clips** (goobyclips/imemeonline, hashtag-only titles, near-zero
  editing) are a real but **high-variance** lottery: goobyclips landed 3.8M, imemeonline
  medians 12k doing the same thing. **Recommend 15–30s curated single-moment as the
  primary format.**

### 5. What's the realistic ceiling and floor for a new channel?
Outlier ceiling is genuinely high (10M–27M is reachable on a single clip with no
subscriber base). But the **defensible, repeatable median for a competent new entrant**
looks like **~30k–70k views/short** (the band where reyshortss/elijahhnfl/goobyclips
actually live once you ignore the lottery spikes). Plan the business model on ~50k/short
median, treat anything >1M as upside, not baseline.

## Strategic implications for the pivot
1. **The market-winning playbook is clip aggregation of existing (mostly copyrighted)
   media — not AI-generated original content.** This is a fork in the road for the
   pivot and needs an explicit decision before Phase 2:
   - *Match the market* → reused/clipped content invites YouTube's **reused-content
     policy** and **copyright/Content-ID** risk (demonetization, strikes). Viable but legally fragile.
   - *Differentiate* → original (incl. AI) content avoids the legal risk but has **no
     proven winner in this sample** — higher creative risk, unproven ceiling.
2. **Optimize per-clip hit-rate, not posting volume.**
3. **Format bet: 15–30s, single curated "payoff moment," hook in the first frame** *(inferred)*.
4. **KPI = views/short (target ~50k median), not subs.**

## Open items needing human verification (playback blocked here)
- Confirm the 15–30s "single-moment" format read by actually watching 2–3 top clips each
  from blindspotdot, historryexposed, shauryaautomotive.
- Confirm none are AI-generated (vs. e.g. AI voiceover on real footage).
- Decide the legal/strategy fork in implication #1 — this gates Phase 2.
