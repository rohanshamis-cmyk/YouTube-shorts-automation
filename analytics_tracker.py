"""
analytics_tracker.py — Standalone module (split from affiliate_manager.py).
Logs all video performance to a local JSON file (always) and optionally Google Sheets.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("RevenueMachine.AnalyticsTracker")


class AnalyticsTracker:
    """
    Tracks every uploaded video and its performance over time.

    Storage:
      - Always: outputs/analytics.json  (no setup required)
      - Optional: Google Sheets (set GOOGLE_SHEETS_ID in .env)

    Metrics tracked per video:
      upload date, topic, video_id, niche, duration,
      views, likes, comments, estimated_revenue
    """

    ANALYTICS_FILE = Path("outputs/analytics.json")

    def __init__(self, config: dict):
        self.config = config
        self.sheets_id = config.get("GOOGLE_SHEETS_ID", "")
        self.ANALYTICS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # ── Public API ──────────────────────────────────────────────

    def log_video(self, data: dict) -> None:
        """Call immediately after a successful upload."""
        analytics = self._load()

        entry = {
            **data,
            "views": 0,
            "likes": 0,
            "comments": 0,
            "estimated_revenue": 0.0,
            "status": "just_uploaded",
            "logged_at": datetime.now().isoformat(),
        }

        analytics["videos"].append(entry)
        analytics["total_videos"] = len(analytics["videos"])
        analytics["last_updated"] = datetime.now().isoformat()
        self._save(analytics)

        if self.sheets_id:
            self._append_to_sheets(entry)

        logger.info(f"  📊 Analytics logged — total videos: {analytics['total_videos']}")

    def update_metrics(self, video_id: str, views: int, likes: int, revenue: float) -> None:
        """Update a previously logged video with fresh performance data."""
        analytics = self._load()
        for video in analytics["videos"]:
            if video.get("video_id") == video_id:
                video.update({
                    "views": views,
                    "likes": likes,
                    "estimated_revenue": revenue,
                    "last_checked": datetime.now().isoformat(),
                    "status": "active",
                })
                break
        self._save(analytics)

    def get_summary(self) -> dict:
        """Returns a high-level summary of all videos."""
        analytics = self._load()
        videos = analytics.get("videos", [])
        if not videos:
            return {"total_videos": 0, "total_views": 0, "total_estimated_revenue": 0.0}

        total_views   = sum(v.get("views", 0) for v in videos)
        total_revenue = sum(v.get("estimated_revenue", 0.0) for v in videos)
        best_video    = max(videos, key=lambda v: v.get("views", 0))

        return {
            "total_videos": len(videos),
            "total_views": total_views,
            "avg_views_per_video": round(total_views / len(videos)),
            "total_estimated_revenue": round(total_revenue, 2),
            "best_video_id": best_video.get("video_id", ""),
            "best_video_views": best_video.get("views", 0),
        }

    # ── Internal helpers ────────────────────────────────────────

    def _load(self) -> dict:
        if self.ANALYTICS_FILE.exists():
            try:
                with open(self.ANALYTICS_FILE) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                logger.warning("analytics.json corrupted — starting fresh.")
        return {
            "videos": [],
            "total_videos": 0,
            "created_at": datetime.now().isoformat(),
        }

    def _save(self, data: dict) -> None:
        with open(self.ANALYTICS_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _append_to_sheets(self, entry: dict) -> None:
        """
        Appends one row to a Google Sheet.
        Requires: GOOGLE_SHEETS_ID in .env + google-api-python-client installed.
        Silently skips if not configured.
        """
        try:
            import pickle
            from pathlib import Path
            from googleapiclient.discovery import build
            from google.auth.transport.requests import Request

            token_path = Path("sheets_token.pickle")
            if not token_path.exists():
                logger.debug("Sheets token not found — skipping Sheets sync.")
                return

            with open(token_path, "rb") as f:
                creds = pickle.load(f)

            if creds.expired and creds.refresh_token:
                creds.refresh(Request())

            service = build("sheets", "v4", credentials=creds)
            row = [
                entry.get("logged_at", ""),
                entry.get("topic", ""),
                entry.get("video_id", ""),
                entry.get("niche", ""),
                entry.get("estimated_duration", ""),
                entry.get("views", 0),
                entry.get("estimated_revenue", 0.0),
            ]
            service.spreadsheets().values().append(
                spreadsheetId=self.sheets_id,
                range="Analytics!A:G",
                valueInputOption="USER_ENTERED",
                body={"values": [row]},
            ).execute()
            logger.info("  📈 Logged to Google Sheets")

        except Exception as e:
            logger.warning(f"  Google Sheets sync failed (non-critical): {e}")
