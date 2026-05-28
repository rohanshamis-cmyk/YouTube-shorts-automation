#!/usr/bin/env python3
"""
pivot/data/reference_pull.py - Phase 1.5 metric pull on the chosen original/AI
faceless reference channels. Same metric definitions as fetch_competitors.py
(median Shorts views last 30d, cadence) but resolves by channelId (from
original_candidates.json) rather than handle.

Writes pivot/data/original_reference_data.json.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_competitors import (  # reuse identical metric logic
    API, SHORT_MAX_SECONDS, WINDOW_DAYS, _get, _iso_duration_to_seconds, _load_key,
)

CHOSEN_TITLES = [
    "Cracked Stories", "AITAH Confessions", "Decoded Logic",
    "Inner Mind", "History Bypass", "Couple of horrorz",
]


def fetch_by_id(key: str, cid: str, niche: str) -> dict:
    print(f"[{cid}] {niche} fetching...", file=sys.stderr)
    ch = _get(key, "channels", {"part": "snippet,statistics", "id": cid})
    items = ch.get("items", [])
    if not items:
        return {"channel_id": cid, "error": "not found"}
    c = items[0]
    stats = c.get("statistics", {})
    after = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    search = _get(key, "search", {
        "part": "id", "channelId": cid, "type": "video",
        "videoDuration": "short", "order": "date",
        "publishedAfter": after, "maxResults": 50,
    })
    vid_ids = [it["id"]["videoId"] for it in search.get("items", [])
               if it.get("id", {}).get("videoId")]
    videos = []
    for i in range(0, len(vid_ids), 50):
        vresp = _get(key, "videos", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(vid_ids[i:i + 50])})
        for v in vresp.get("items", []):
            dur = _iso_duration_to_seconds(v.get("contentDetails", {}).get("duration", ""))
            if dur > SHORT_MAX_SECONDS:
                continue
            vs = v.get("statistics", {})
            sn = v.get("snippet", {})
            videos.append({
                "video_id": v["id"], "title": sn.get("title", ""),
                "published_at": sn.get("publishedAt", ""), "duration_s": dur,
                "views": int(vs.get("viewCount", 0) or 0),
                "likes": int(vs.get("likeCount", 0) or 0),
            })
    views = sorted(v["views"] for v in videos)
    median = views[len(views) // 2] if views else 0
    return {
        "channel_id": cid, "niche": niche,
        "title": c.get("snippet", {}).get("title", ""),
        "subscribers": int(stats.get("subscriberCount", 0) or 0),
        "total_videos": int(stats.get("videoCount", 0) or 0),
        "shorts_last_30d": len(videos),
        "median_short_views": median,
        "max_short_views": views[-1] if views else 0,
        "min_short_views": views[0] if views else 0,
        "videos": videos,
    }


def main() -> int:
    key = _load_key()
    cands = json.loads(Path("pivot/data/original_candidates.json").read_text())
    by_title = {c["title"]: c for c in cands}
    out = {"collected_at": datetime.now(timezone.utc).isoformat(),
           "window_days": WINDOW_DAYS, "channels": []}
    for title in CHOSEN_TITLES:
        c = by_title.get(title)
        if not c:
            print(f"  MISSING candidate: {title}", file=sys.stderr)
            continue
        niche = c["niches"][0] if c["niches"] else "?"
        try:
            rec = fetch_by_id(key, c["channel_id"], niche)
            rec["display_title"] = title
            out["channels"].append(rec)
        except Exception as exc:
            out["channels"].append({"display_title": title, "error": str(exc)})
    Path("pivot/data/original_reference_data.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False))
    print(f"wrote pivot/data/original_reference_data.json ({len(out['channels'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
