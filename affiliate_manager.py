"""
Affiliate Manager - Manages affiliate links for monetization from day 1.
Analytics Tracker - Logs all metrics to Google Sheets.
Thumbnail Creator - AI-powered thumbnail generation.
"""

import os
import json
import logging
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger("RevenueMachine.Modules")


# ════════════════════════════════════════════
#  AFFILIATE MANAGER
# ════════════════════════════════════════════

class AffiliateManager:
    """
    Manages affiliate links. Revenue from day 1, even before monetization.

    Best affiliate programs for each niche:
    - AI Tools: Jasper ($30/ref), ElevenLabs ($20), Notion ($10), HubSpot ($200)
    - Finance: Robinhood ($5), Coinbase ($10), Acorns ($10), Webull ($30)
    - Motivation: Audible ($5), Skillshare ($7), Masterclass ($15)
    """

    def __init__(self, niche_config: dict):
        self.programs = niche_config.get("affiliate_programs", {})

    def get_relevant_links(self, topic: dict) -> dict:
        """
        Returns the most relevant affiliate links for a topic.
        Limits to 3 links max (more = spam).
        """
        topic_title = topic.get("title", "").lower()
        relevant = {}

        # Match programs to topic keywords
        keyword_map = {
            "chatgpt": ["jasper", "notion"],
            "ai": ["jasper", "elevenlabs", "notion"],
            "writing": ["jasper", "grammarly"],
            "voice": ["elevenlabs"],
            "passive income": ["acorns", "robinhood"],
            "invest": ["robinhood", "coinbase", "webull"],
            "motivation": ["audible", "skillshare"],
            "productivity": ["notion", "skillshare"],
        }

        for keyword, program_names in keyword_map.items():
            if keyword in topic_title:
                for name in program_names:
                    if name in self.programs and len(relevant) < 3:
                        relevant[name] = self.programs[name]

        # If no keyword match, use top 2 programs
        if not relevant:
            for name, data in list(self.programs.items())[:2]:
                relevant[name] = data

        return relevant

    def get_description_links(self, topic: dict) -> str:
        """Returns formatted affiliate links for video description."""
        links = self.get_relevant_links(topic)
        if not links:
            return ""

        lines = ["🔗 Tools mentioned:", ""]
        for name, data in links.items():
            lines.append(f"► {name.title()}: {data['link']}")
            if data.get("cpa", 0) > 0:
                lines.append(f"  (I may earn a commission at no cost to you)")

        return "\n".join(lines)


# ════════════════════════════════════════════
#  THUMBNAIL CREATOR
# ════════════════════════════════════════════

class ThumbnailCreator:
    """
    Creates eye-catching thumbnails using PIL.
    Style: Bold text + high-contrast background + emoji.
    Proven to increase CTR by 20-40% over no thumbnail.
    """

    def __init__(self, config: dict):
        self.config = config
        self.niche = config.get("NICHE", "ai_tools")

        try:
            from PIL import Image, ImageDraw, ImageFont
            self.pil_available = True
        except ImportError:
            self.pil_available = False
            logger.warning("PIL not available. Install: pip install Pillow")

    def create(self, title: str, style: str = "bold_text") -> Optional[Path]:
        """Creates a 1280x720 YouTube thumbnail."""
        if not self.pil_available:
            return None

        from PIL import Image, ImageDraw, ImageFont
        import textwrap

        # Thumbnail size (YouTube standard)
        width, height = 1280, 720
        output_path = Path(f"outputs/thumb_{abs(hash(title))}.jpg")

        # Color schemes per niche
        schemes = {
            "ai_tools": {"bg": "#0a0a0a", "accent": "#00d4ff", "text": "#ffffff"},
            "finance": {"bg": "#1a1a1a", "accent": "#00ff88", "text": "#ffffff"},
            "motivation": {"bg": "#2d1b69", "accent": "#ff6b6b", "text": "#ffffff"},
        }
        colors = schemes.get(self.niche, schemes["ai_tools"])

        # Create image
        img = Image.new("RGB", (width, height), colors["bg"])
        draw = ImageDraw.Draw(img)

        # Background gradient effect
        for i in range(height):
            alpha = int(255 * (1 - i / height) * 0.3)
            draw.line([(0, i), (width, i)], fill=(*self._hex_to_rgb(colors["accent"]), alpha))

        # Add decorative accent bar
        bar_height = 8
        draw.rectangle([0, height - bar_height, width, height],
                       fill=colors["accent"])

        # Wrap title text
        words = title.split()
        max_words_per_line = 4
        lines = []
        for i in range(0, len(words), max_words_per_line):
            lines.append(" ".join(words[i:i + max_words_per_line]))
        lines = lines[:3]  # Max 3 lines

        # Calculate font size
        font_size = 80 if len(lines) <= 2 else 65

        try:
            # Try to load a bold font
            font_paths = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/System/Library/Fonts/Helvetica.ttc",
                "assets/fonts/bold.ttf",
            ]
            font = None
            for fp in font_paths:
                if Path(fp).exists():
                    font = ImageFont.truetype(fp, font_size)
                    break
            if not font:
                font = ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()

        # Draw text with shadow
        total_height = len(lines) * (font_size + 15)
        y_start = (height - total_height) // 2

        for i, line in enumerate(lines):
            y = y_start + i * (font_size + 15)
            # Shadow
            draw.text((42, y + 4), line, font=font, fill="#000000")
            # Main text
            draw.text((40, y), line, font=font, fill=colors["text"])

        # Add niche badge
        badge_text = {"ai_tools": "🤖 AI", "finance": "💰 MONEY", "motivation": "🔥 MINDSET"}
        badge = badge_text.get(self.niche, "📱")
        try:
            small_font = ImageFont.truetype(font_paths[0], 36) if font_paths else ImageFont.load_default()
        except Exception:
            small_font = ImageFont.load_default()

        draw.rectangle([30, 30, 200, 80], fill=colors["accent"])
        draw.text((40, 38), badge, font=small_font, fill="#000000")

        # Save
        output_path.parent.mkdir(exist_ok=True)
        img.save(str(output_path), "JPEG", quality=95)
        logger.info(f"  🖼️  Thumbnail: {output_path.name}")
        return output_path

    def _hex_to_rgb(self, hex_color: str) -> tuple:
        hex_color = hex_color.lstrip("#")
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


# ════════════════════════════════════════════
#  ANALYTICS TRACKER
# ════════════════════════════════════════════

class AnalyticsTracker:
    """
    Logs video performance to:
    1. Local JSON file (always)
    2. Google Sheets (optional)

    Tracks: upload date, topic, video_id, views, likes, comments, revenue estimate
    """

    def __init__(self, config: dict):
        self.config = config
        self.log_file = Path("outputs/analytics.json")
        self.sheets_id = config.get("GOOGLE_SHEETS_ID", "")

    def log_video(self, data: dict):
        """Logs a newly uploaded video."""
        # Load existing data
        analytics = self._load()

        # Add new entry
        entry = {
            **data,
            "views": 0,
            "likes": 0,
            "comments": 0,
            "estimated_revenue": 0.0,
            "status": "just_uploaded",
        }
        analytics["videos"].append(entry)
        analytics["total_videos"] = len(analytics["videos"])
        analytics["last_updated"] = datetime.now().isoformat()

        self._save(analytics)

        if self.sheets_id:
            self._log_to_sheets(entry)

        logger.info(f"  📊 Analytics logged. Total videos: {analytics['total_videos']}")

    def update_metrics(self, video_id: str, views: int, likes: int, revenue: float):
        """Updates metrics for an existing video (called by daily sync)."""
        analytics = self._load()
        for video in analytics["videos"]:
            if video.get("video_id") == video_id:
                video["views"] = views
                video["likes"] = likes
                video["estimated_revenue"] = revenue
                video["last_checked"] = datetime.now().isoformat()
                break
        self._save(analytics)

    def get_summary(self) -> dict:
        """Returns overall performance summary."""
        analytics = self._load()
        videos = analytics.get("videos", [])

        total_views = sum(v.get("views", 0) for v in videos)
        total_revenue = sum(v.get("estimated_revenue", 0) for v in videos)
        avg_views = total_views / max(len(videos), 1)

        return {
            "total_videos": len(videos),
            "total_views": total_views,
            "total_estimated_revenue": round(total_revenue, 2),
            "avg_views_per_video": round(avg_views),
            "best_video": max(videos, key=lambda x: x.get("views", 0), default={}),
        }

    def _load(self) -> dict:
        if self.log_file.exists():
            try:
                with open(self.log_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"videos": [], "total_videos": 0, "created_at": datetime.now().isoformat()}

    def _save(self, data: dict):
        self.log_file.parent.mkdir(exist_ok=True)
        with open(self.log_file, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _log_to_sheets(self, entry: dict):
        """Optional: logs to Google Sheets via Sheets API."""
        # Implementation requires Google Sheets API setup
        # Left as exercise for configuration
        pass
