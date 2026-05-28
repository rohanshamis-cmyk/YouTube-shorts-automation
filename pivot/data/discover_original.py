#!/usr/bin/env python3
"""
pivot/data/discover_original.py - Phase 1.5 candidate discovery.

ANALYSIS TOOLING. Finds channels WINNING in original/AI faceless niches (counter
to the clip-aggregator sample of Phase 1). Strategy: search top-viewed Shorts per
niche, map back to the channels that made them, then pull channel-level stats +
descriptions so a human can filter for genuinely original/AI/faceless content
before the full metric pull.

Writes pivot/data/original_candidates.json and prints a ranked table.
Reuses the API key + helpers conceptually from fetch_competitors.py.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://www.googleapis.com/youtube/v3"

# query sets per niche; multiple phrasings widen coverage
NICHES = {
    "reddit_narration": [
        "reddit aita story", "askreddit story", "reddit relationship drama story",
    ],
    "historical_factoid": [
        "historical facts you didnt know", "history shorts scary facts",
        "did you know history",
    ],
    "ai_visual_explainer": [
        "psychology facts shorts", "science explained shorts", "did you know science",
    ],
    "scary_story": [
        "scary story narration", "creepypasta shorts", "true scary story",
    ],
}
LOOKBACK_DAYS = 120  # favor currently-active channels


def load_key() -> str:
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key:
        for line in Path(".env").read_text().splitlines():
            if line.strip().startswith("YOUTUBE_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("YOUTUBE_API_KEY not set.")
    return key


def get(key: str, endpoint: str, params: dict) -> dict:
    params = {**params, "key": key}
    url = f"{API}/{endpoint}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            print(f"  API {e.code}: {e.read().decode('utf-8','replace')[:200]}", file=sys.stderr)
            raise
        except Exception as exc:
            print(f"  retry {attempt}: {exc}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed {endpoint}")


def main() -> int:
    key = load_key()
    after = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")

    # channelId -> {niches, hits, sample_titles}
    cand: dict[str, dict] = defaultdict(
        lambda: {"niches": set(), "hits": 0, "titles": []})
    for niche, queries in NICHES.items():
        for q in queries:
            print(f"[{niche}] search: {q!r}", file=sys.stderr)
            resp = get(key, "search", {
                "part": "snippet", "q": q, "type": "video",
                "videoDuration": "short", "order": "viewCount",
                "publishedAfter": after, "maxResults": 50,
            })
            for it in resp.get("items", []):
                cid = it["snippet"]["channelId"]
                c = cand[cid]
                c["niches"].add(niche)
                c["hits"] += 1
                if len(c["titles"]) < 3:
                    c["titles"].append(it["snippet"]["title"][:70])

    # rank, keep channels appearing >=2 times (signal, not one-off)
    ranked = sorted(cand.items(), key=lambda kv: -kv[1]["hits"])
    top_ids = [cid for cid, c in ranked if c["hits"] >= 2][:40]
    print(f"\n{len(cand)} unique channels; {len(top_ids)} with >=2 hits\n", file=sys.stderr)

    # hydrate channel stats + descriptions in batches of 50
    details: dict[str, dict] = {}
    for i in range(0, len(top_ids), 50):
        chunk = top_ids[i:i + 50]
        resp = get(key, "channels", {
            "part": "snippet,statistics", "id": ",".join(chunk)})
        for c in resp.get("items", []):
            details[c["id"]] = c

    out = []
    for cid in top_ids:
        c = details.get(cid)
        if not c:
            continue
        st = c.get("statistics", {})
        sn = c.get("snippet", {})
        out.append({
            "channel_id": cid,
            "title": sn.get("title", ""),
            "handle": sn.get("customUrl", ""),
            "subscribers": int(st.get("subscriberCount", 0) or 0),
            "total_videos": int(st.get("videoCount", 0) or 0),
            "total_views": int(st.get("viewCount", 0) or 0),
            "description": (sn.get("description", "") or "").replace("\n", " ")[:200],
            "niches": sorted(cand[cid]["niches"]),
            "search_hits": cand[cid]["hits"],
            "sample_titles": cand[cid]["titles"],
        })
    out.sort(key=lambda x: -x["search_hits"])

    Path("pivot/data/original_candidates.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\n{'title':30} {'subs':>10} {'vids':>6} {'hits':>4}  niches")
    for x in out:
        print(f"{x['title'][:30]:30} {x['subscribers']:>10} {x['total_videos']:>6} "
              f"{x['search_hits']:>4}  {','.join(n[:8] for n in x['niches'])}")
    print(f"\nwrote pivot/data/original_candidates.json ({len(out)} candidates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
