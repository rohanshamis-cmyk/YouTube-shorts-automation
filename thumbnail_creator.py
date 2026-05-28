"""
thumbnail_creator.py — Standalone module (split from affiliate_manager.py).
Creates eye-catching 1280x720 YouTube thumbnails using PIL.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("RevenueMachine.ThumbnailCreator")


class ThumbnailCreator:
    """
    Creates eye-catching thumbnails using PIL.
    Style: Bold text + high-contrast background + niche badge.
    Falls back gracefully if PIL is not installed.
    """

    def __init__(self, config: dict):
        self.config = config
        self.niche = config.get("NICHE", "ai_tools")

        try:
            from PIL import Image, ImageDraw, ImageFont  # noqa: F401
            self.pil_available = True
        except ImportError:
            self.pil_available = False
            logger.warning("PIL not installed — thumbnails disabled. Run: pip install Pillow")

    def create(self, title: str, style: str = "bold_text") -> Optional[Path]:
        """
        Creates a 1280x720 YouTube thumbnail.
        Returns the Path to the saved JPEG, or None if PIL unavailable.
        """
        if not self.pil_available:
            logger.warning("Skipping thumbnail — PIL not available.")
            return None

        from PIL import Image, ImageDraw, ImageFont

        width, height = 1280, 720
        output_path = Path(f"outputs/thumb_{abs(hash(title)) % 10**9}.jpg")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        schemes = {
            "ai_tools":  {"bg": "#0a0a0a", "accent": "#00d4ff", "text": "#ffffff"},
            "finance":   {"bg": "#1a1a1a", "accent": "#00ff88", "text": "#ffffff"},
            "motivation":{"bg": "#2d1b69", "accent": "#ff6b6b", "text": "#ffffff"},
        }
        colors = schemes.get(self.niche, schemes["ai_tools"])

        img = Image.new("RGB", (width, height), colors["bg"])
        draw = ImageDraw.Draw(img)

        # Subtle gradient overlay
        for i in range(height):
            alpha = int(80 * (1 - i / height))
            r, g, b = self._hex_to_rgb(colors["accent"])
            draw.line([(0, i), (width, i)], fill=(r, g, b, alpha))

        # Bottom accent bar
        draw.rectangle([0, height - 8, width, height], fill=colors["accent"])

        # Wrap title into lines of max 4 words
        words = title.split()
        lines = [" ".join(words[i:i+4]) for i in range(0, len(words), 4)][:3]
        font_size = 80 if len(lines) <= 2 else 60

        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
        font = ImageFont.load_default()
        for fp in font_paths:
            if Path(fp).exists():
                try:
                    font = ImageFont.truetype(fp, font_size)
                    break
                except Exception:
                    pass

        total_h = len(lines) * (font_size + 15)
        y = (height - total_h) // 2
        for line in lines:
            draw.text((42, y + 4), line, font=font, fill="#000000")   # shadow
            draw.text((40, y),     line, font=font, fill=colors["text"])
            y += font_size + 15

        # Niche badge (top-left)
        badge_text = {"ai_tools": "AI", "finance": "MONEY", "motivation": "MINDSET"}
        badge = badge_text.get(self.niche, "VIRAL")
        try:
            small_font = ImageFont.truetype(font_paths[0], 32) if Path(font_paths[0]).exists() else ImageFont.load_default()
        except Exception:
            small_font = ImageFont.load_default()

        draw.rectangle([20, 20, 160, 68], fill=colors["accent"])
        draw.text((30, 28), badge, font=small_font, fill="#000000")

        img.save(str(output_path), "JPEG", quality=95)
        logger.info(f"  🖼️  Thumbnail saved: {output_path.name}")
        return output_path

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> tuple:
        h = hex_color.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
