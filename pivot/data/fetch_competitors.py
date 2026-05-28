#!/usr/bin/env python3
"""
pivot/data/fetch_competitors.py - Phase 1 competitor intelligence collector.

ANALYSIS TOOLING, not product pipeline code. Pulls last-30-day Shorts data for
the 12 brief channels via the YouTube Data API v3 (the only route that works
from this environment - direct scraping / yt-dlp / WebFetch are all 403-blocked).

Reads YOUTUBE_API_KEY from .env or the environment. Writes:
  pivot/data/competitor_data.json   - raw per-channel + per-video records
The human-readable summary (competitor_summary.md) is written separately after
inspecting this JSON.

Quota: channels.list=1u, search.list=100u/channel, videos.list=1u/50ids.
12 channels ~= 1300 units, well under the 10k/day free quota.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://www.googleapis.com/youtube/v3"
HANDLES = [
    "quantumphysics-0", "fdggh009", "monarchprime", "reyshortss",
    "goobyclips", "historryexposed", "hoodclips13", "shauryaautomotive",
    "jestlore", "blindspotdot", "imemeonline", "elijahhnfl",
]
SHORT_MAX_SECONDS = 180  # YouTube treats <=3min vertical as Shorts-eligible
WINDOW_DAYS = 30


def _load_key() -> str:
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key:
        env = Path(".env")
        if env.exists():
            for line in env.read_text().splitlines():
                if line.strip().startswith("YOUTUBE_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("YOUTUBE_API_KEY not set (env or .env). Aborting.")
    return key


def _get(key: str, endpoint: str, params: dict) -> dict:
    params = {**params, "key": key}
    url = f"{API}/{endpoint}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (403, 400):  # surface quota/key errors immediately
                print(f"  API {e.code}: {body[:300]}", file=sys.stderr)
                raise
            time.sleep(2 ** attempt)
        except Exception as exc:  # transient network
            print(f"  retry {attempt}: {exc}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed: {endpoint} {params}")


def _iso_duration_to_seconds(iso: str) -> int:
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def fetch_channel(key: str, handle: str) -> dict:
    print(f"[{handle}] resolving channel...", file=sys.stderr)
    ch = _get(key, "channels", {
        "part": "snippet,statistics,contentDetails", "forHandle": f"@{handle}",
    })
    items = ch.get("items", [])
    if not items:
        return {"handle": handle, "error": "channel not found"}
    c = items[0]
    cid = c["id"]
    stats = c.get("statistics", {})
    subs = int(stats.get("subscriberCount", 0) or 0)

    after = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    print(f"[{handle}] searching shorts since {after}...", file=sys.stderr)
    search = _get(key, "search", {
        "part": "id", "channelId": cid, "type": "video",
        "videoDuration": "short", "order": "date",
        "publishedAfter": after, "maxResults": 50,
    })
    vid_ids = [it["id"]["videoId"] for it in search.get("items", [])
               if it.get("id", {}).get("videoId")]

    videos = []
    for i in range(0, len(vid_ids), 50):
        chunk = vid_ids[i:i + 50]
        vresp = _get(key, "videos", {
            "part": "snippet,statistics,contentDetails", "id": ",".join(chunk),
        })
        for v in vresp.get("items", []):
            dur = _iso_duration_to_seconds(v.get("contentDetails", {}).get("duration", ""))
            vs = v.get("statistics", {})
            sn = v.get("snippet", {})
            videos.append({
                "video_id": v["id"],
                "title": sn.get("title", ""),
                "published_at": sn.get("publishedAt", ""),
                "duration_s": dur,
                "views": int(vs.get("viewCount", 0) or 0),
                "likes": int(vs.get("likeCount", 0) or 0),
                "comments": int(vs.get("commentCount", 0) or 0),
                "thumbnails": sn.get("thumbnails", {}),
                "is_short": dur <= SHORT_MAX_SECONDS,
            })

    shorts = [v for v in videos if v["is_short"]]
    views_list = sorted(v["views"] for v in shorts)
    median = views_list[len(views_list) // 2] if views_list else 0
    return {
        "handle": handle,
        "channel_id": cid,
        "title": c.get("snippet", {}).get("title", ""),
        "subscribers": subs,
        "total_views": int(stats.get("viewCount", 0) or 0),
        "total_videos": int(stats.get("videoCount", 0) or 0),
        "shorts_last_30d": len(shorts),
        "median_short_views": median,
        "max_short_views": views_list[-1] if views_list else 0,
        "videos": shorts,
    }


def main() -> int:
    key = _load_key()
    out = {"collected_at": datetime.now(timezone.utc).isoformat(),
           "window_days": WINDOW_DAYS, "channels": []}
    for h in HANDLES:
        try:
            out["channels"].append(fetch_channel(key, h))
        except Exception as exc:
            out["channels"].append({"handle": h, "error": str(exc)})
            print(f"[{h}] FAILED: {exc}", file=sys.stderr)
    dest = Path("pivot/data/competitor_data.json")
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"wrote {dest} ({len(out['channels'])} channels)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
