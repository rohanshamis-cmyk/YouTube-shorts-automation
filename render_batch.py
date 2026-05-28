#!/usr/bin/env python3
"""Render a generated batch into Shorts-ready vertical videos with optional voiceover."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from PIL import Image, ImageDraw, ImageFont

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency at runtime
    load_dotenv = None


WIDTH = 1080
HEIGHT = 1920
FPS = 30
MAX_SHORT_SECONDS = 59.0

THEMES = [
    ((32, 42, 85), (54, 99, 201)),
    ((30, 68, 71), (63, 128, 120)),
    ((85, 34, 34), (170, 70, 70)),
    ((47, 43, 74), (95, 84, 150)),
    ((70, 56, 28), (138, 110, 56)),
]

STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "ai_student": {
        "pace_mult": 1.0,
        "caption_font_size": 66,
        "title_font_size": 42,
        "hook_font_size": 78,
        "caption_panel_rgba": (0, 0, 0, 168),
        "hook_color": (255, 237, 144, 255),
        "music_volume": 0.11,
        "asset_keywords": ["study", "notes", "student", "desk", "typing"],
    },
    "trend_explainer": {
        "pace_mult": 0.88,
        "caption_font_size": 70,
        "title_font_size": 44,
        "hook_font_size": 82,
        "caption_panel_rgba": (18, 18, 18, 176),
        "hook_color": (255, 190, 120, 255),
        "music_volume": 0.10,
        "asset_keywords": ["trend", "news", "graph", "explainer", "city"],
    },
    "gameplay_story": {
        "pace_mult": 1.12,
        "caption_font_size": 64,
        "title_font_size": 40,
        "hook_font_size": 76,
        "caption_panel_rgba": (8, 8, 8, 162),
        "hook_color": (140, 255, 210, 255),
        "music_volume": 0.09,
        "asset_keywords": ["game", "gameplay", "fps", "story", "play"],
    },
}

DEFAULT_RENDER_CONFIG = {
    "style": "ai_student",
    "assets_dir": "assets",
    "music_dir": "assets/music",
    "enable_voiceover": True,
}


@dataclass
class ScriptEntry:
    script_path: Path
    title: str
    topic: str
    angle: str
    hook: str
    on_screen_hook: str
    video_title: str
    caption: str
    cta: str
    hashtags: list[str]
    caption_lines: list[str]
    script_text: str
    visual_queries: list[str]
    posting_order: int


@dataclass
class RenderOptions:
    style: str
    assets_dir: Path
    music_dir: Path
    enable_voiceover: bool


def _run_cmd(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Command failed")


def _run_cmd_checked(cmd: list[str]) -> bool:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def _check_ffmpeg() -> None:
    _run_cmd(["ffmpeg", "-version"])
    _run_cmd(["ffprobe", "-version"])


def _find_latest_batch(drafts_dir: Path) -> Path:
    batches = [path for path in drafts_dir.glob("batch_*") if path.is_dir()]
    if not batches:
        raise FileNotFoundError("No batch folders found under drafts/")
    return max(batches, key=lambda path: path.stat().st_mtime)


def _load_manifest(batch_dir: Path) -> dict[str, Any]:
    manifest_path = batch_dir / "batch_manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_render_options(style_override: str | None) -> RenderOptions:
    config = dict(DEFAULT_RENDER_CONFIG)
    cfg_path = Path("render_config.json")
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config.update({k: v for k, v in data.items() if v is not None})
        except Exception:
            pass

    env_style = os.getenv("SHORTS_STYLE", "").strip()
    resolved_style = style_override or env_style or str(config.get("style", "ai_student"))
    if resolved_style not in STYLE_PRESETS:
        resolved_style = "ai_student"

    assets_dir = Path(str(config.get("assets_dir", "assets")))
    music_dir = Path(str(config.get("music_dir", "assets/music")))
    enable_voiceover = bool(config.get("enable_voiceover", True))

    return RenderOptions(
        style=resolved_style,
        assets_dir=assets_dir,
        music_dir=music_dir,
        enable_voiceover=enable_voiceover,
    )


def _parse_script_file(script_path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": "",
        "topic": "",
        "angle": "",
        "hook": "",
        "on_screen_hook": "",
        "video_title": "",
        "broll_idea": "",
        "caption": "",
        "cta": "",
        "hashtags": [],
        "shot_list": [],
        "caption_lines": [],
        "script_text": "",
        "visual_queries": [],
        "posting_order": 999,
    }
    scalar = {
        "TITLE": "title",
        "TOPIC": "topic",
        "ANGLE": "angle",
        "HOOK": "hook",
        "ON_SCREEN_HOOK": "on_screen_hook",
        "VIDEO_TITLE": "video_title",
        "BROLL_IDEA": "broll_idea",
        "CAPTION": "caption",
        "CTA": "cta",
        "POSTING_ORDER": "posting_order",
    }

    section = ""
    for raw_line in script_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key in scalar:
                data[scalar[key]] = value
                section = ""
                continue
            if key in {"HASHTAGS", "SHOT_LIST", "CAPTION_LINES", "VISUAL_QUERIES", "SCRIPT"}:
                section = key
                if value:
                    cleaned = _clean_list_item(value)
                    if cleaned:
                        if key == "HASHTAGS":
                            data["hashtags"].append(cleaned)
                        elif key == "SHOT_LIST":
                            data["shot_list"].append(cleaned)
                        elif key == "CAPTION_LINES":
                            data["caption_lines"].append(cleaned)
                        elif key == "SCRIPT":
                            data["script_text"] = f"{data['script_text']} {cleaned}".strip()
                        else:
                            data["visual_queries"].append(cleaned)
                continue

        if section == "HASHTAGS":
            cleaned = _clean_list_item(line)
            if cleaned:
                data["hashtags"].append(cleaned)
        elif section == "SHOT_LIST":
            cleaned = _clean_list_item(line)
            if cleaned:
                data["shot_list"].append(cleaned)
        elif section == "CAPTION_LINES":
            cleaned = _clean_list_item(line)
            if cleaned:
                data["caption_lines"].append(cleaned)
        elif section == "VISUAL_QUERIES":
            cleaned = _clean_list_item(line)
            if cleaned:
                data["visual_queries"].append(cleaned)
        elif section == "SCRIPT":
            cleaned = line.strip()
            if cleaned:
                data["script_text"] = f"{data['script_text']} {cleaned}".strip()

    try:
        data["posting_order"] = int(str(data["posting_order"]))
    except Exception:
        data["posting_order"] = 999
    return data


def _clean_list_item(value: str) -> str:
    cleaned = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", value or "").strip()
    return cleaned


def _coerce_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _collect_entries(batch_dir: Path) -> list[ScriptEntry]:
    manifest = _load_manifest(batch_dir)
    manifest_scripts = manifest.get("scripts", []) if isinstance(manifest, dict) else []
    manifest_map = {
        str(item.get("filename", "")): item
        for item in manifest_scripts
        if isinstance(item, dict) and item.get("filename")
    }

    script_paths = sorted(batch_dir.glob("dry_run_*.txt"))
    if not script_paths:
        raise FileNotFoundError(f"No script files found in {batch_dir}")

    entries: list[ScriptEntry] = []
    for script_path in script_paths:
        parsed = _parse_script_file(script_path)
        manifest_item = manifest_map.get(script_path.name, {})

        caption_lines = _coerce_list(parsed.get("caption_lines")) or _coerce_list(
            manifest_item.get("caption_lines")
        )
        if not caption_lines:
            fallback = str(parsed.get("caption") or manifest_item.get("caption") or parsed.get("hook") or "")
            if fallback:
                caption_lines = [fallback]

        hashtags = _coerce_list(parsed.get("hashtags"))
        if not hashtags:
            hashtags = _coerce_list(manifest_item.get("hashtags"))
        hashtags = hashtags[:6]

        visual_queries = _coerce_list(parsed.get("visual_queries"))
        if not visual_queries:
            visual_queries = _coerce_list(manifest_item.get("visual_queries"))
        if not visual_queries:
            visual_queries = (
                _coerce_list(manifest_item.get("shot_list"))
                or _coerce_list(manifest_item.get("broll_idea"))
                or _coerce_list(parsed.get("title"))
            )

        script_text = str(
            parsed.get("script_text")
            or manifest_item.get("script")
            or ""
        ).strip()

        posting_order = parsed.get("posting_order", 999)
        if posting_order == 999:
            try:
                posting_order = int(str(manifest_item.get("posting_order", 999)))
            except Exception:
                posting_order = 999

        entry = ScriptEntry(
            script_path=script_path,
            title=str(parsed.get("title") or manifest_item.get("title") or script_path.stem),
            topic=str(parsed.get("topic") or manifest_item.get("topic") or ""),
            angle=str(parsed.get("angle") or manifest_item.get("angle") or ""),
            hook=str(parsed.get("hook") or manifest_item.get("hook") or ""),
            on_screen_hook=str(
                parsed.get("on_screen_hook")
                or manifest_item.get("on_screen_hook")
                or ""
            ),
            video_title=str(
                parsed.get("video_title")
                or manifest_item.get("video_title")
                or parsed.get("title")
                or script_path.stem
            ),
            caption=str(parsed.get("caption") or manifest_item.get("caption") or ""),
            cta=str(parsed.get("cta") or manifest_item.get("cta") or "Follow for more."),
            hashtags=hashtags,
            caption_lines=caption_lines[:8],
            script_text=script_text,
            visual_queries=visual_queries[:8],
            posting_order=posting_order,
        )
        entries.append(entry)

    entries.sort(key=lambda item: (item.posting_order, item.script_path.name))
    return entries


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates.extend(
            [
                "DejaVuSans-Bold.ttf",
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/Library/Fonts/Arial Bold.ttf",
            ]
        )
    candidates.extend(
        [
            "DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
        ]
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        probe = f"{current} {word}"
        bbox = draw.textbbox((0, 0), probe, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = probe
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines[:6]


def _draw_segment_image(
    output_path: Path,
    main_text: str,
    title_text: str,
    footer_text: str,
    theme_idx: int,
    label: str,
    style: str,
) -> None:
    # Slide fallback renderer with the same caption/hook layout used on motion clips.
    c1, c2 = THEMES[theme_idx % len(THEMES)]
    image = Image.new("RGBA", (WIDTH, HEIGHT), c1 + (255,))
    draw = ImageDraw.Draw(image, "RGBA")

    for y in range(HEIGHT):
        t = y / max(1, HEIGHT - 1)
        color = (
            int(c1[0] * (1 - t) + c2[0] * t),
            int(c1[1] * (1 - t) + c2[1] * t),
            int(c1[2] * (1 - t) + c2[2] * t),
            255,
        )
        draw.line([(0, y), (WIDTH, y)], fill=color)

    draw.rounded_rectangle((44, 86, WIDTH - 44, 186), radius=18, fill=(0, 0, 0, 108))
    title_font = _load_font(42, bold=True)
    draw.text((70, 112), title_text[:50], font=title_font, fill=(255, 255, 255, 242))

    text = (main_text or footer_text).strip()
    if label == "HOOK":
        hook_font = _load_font(88, bold=True)
        wrapped = _wrap_text(draw, text, hook_font, WIDTH - 140)
        line_height = 102
        block_height = line_height * len(wrapped)
        y = int(HEIGHT * 0.40) - (block_height // 2)
        for line in wrapped:
            bbox = draw.textbbox((0, 0), line, font=hook_font)
            x = (WIDTH - (bbox[2] - bbox[0])) // 2
            draw.text((x + 2, y + 2), line, font=hook_font, fill=(0, 0, 0, 220))
            draw.text((x, y), line, font=hook_font, fill=(255, 215, 0, 255))
            y += line_height
    else:
        caption_font = _load_font(76, bold=True)
        panel_x1 = 78
        panel_x2 = WIDTH - 78
        panel_y1 = int(HEIGHT * 0.63)
        panel_y2 = int(HEIGHT * 0.79)
        draw.rounded_rectangle((panel_x1, panel_y1, panel_x2, panel_y2), radius=32, fill=(0, 0, 0, 180))
        wrapped = _wrap_text(draw, text, caption_font, panel_x2 - panel_x1 - 82)
        line_height = 86
        block_height = line_height * len(wrapped)
        y = panel_y1 + ((panel_y2 - panel_y1 - block_height) // 2)
        for line in wrapped:
            bbox = draw.textbbox((0, 0), line, font=caption_font)
            x = (WIDTH - (bbox[2] - bbox[0])) // 2
            draw.text((x + 2, y + 2), line, font=caption_font, fill=(0, 0, 0, 220))
            draw.text((x, y), line, font=caption_font, fill=(255, 255, 255, 252))
            y += line_height

    image.convert("RGB").save(output_path, format="PNG")


def _draw_caption_overlay(
    output_path: Path,
    main_text: str,
    title_text: str,
    label: str,
    style: str,
) -> None:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image, "RGBA")
    text = (main_text or "").strip()
    if not text:
        image.save(output_path, format="PNG")
        return

    if label == "HOOK":
        hook_font = _load_font(88, bold=True)
        wrapped = _wrap_text(draw, text, hook_font, WIDTH - 140)
        line_height = 102
        block_height = line_height * len(wrapped)
        y = int(HEIGHT * 0.40) - (block_height // 2)
        for line in wrapped:
            bbox = draw.textbbox((0, 0), line, font=hook_font)
            x = (WIDTH - (bbox[2] - bbox[0])) // 2
            draw.text((x + 2, y + 2), line, font=hook_font, fill=(0, 0, 0, 220))
            draw.text((x, y), line, font=hook_font, fill=(255, 215, 0, 255))
            y += line_height
        image.save(output_path, format="PNG")
        return

    caption_font = _load_font(76, bold=True)
    panel_x1 = 78
    panel_x2 = WIDTH - 78
    panel_y1 = int(HEIGHT * 0.63)
    panel_y2 = int(HEIGHT * 0.79)
    draw.rounded_rectangle((panel_x1, panel_y1, panel_x2, panel_y2), radius=32, fill=(0, 0, 0, 180))

    wrapped = _wrap_text(draw, text, caption_font, panel_x2 - panel_x1 - 82)
    line_height = 86
    block_height = line_height * len(wrapped)
    y = panel_y1 + ((panel_y2 - panel_y1 - block_height) // 2)
    for line in wrapped:
        bbox = draw.textbbox((0, 0), line, font=caption_font)
        x = (WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text((x + 2, y + 2), line, font=caption_font, fill=(0, 0, 0, 220))
        draw.text((x, y), line, font=caption_font, fill=(255, 255, 255, 252))
        y += line_height

    image.save(output_path, format="PNG")


def _make_segment_video(image_path: Path, duration: float, output_path: Path) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-t",
        f"{duration:.3f}",
        "-r",
        str(FPS),
        "-vf",
        f"scale={WIDTH}:{HEIGHT},format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        str(output_path),
    ]
    _run_cmd(cmd)


def _probe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return 0.0
    try:
        return max(0.0, float(result.stdout.strip()))
    except Exception:
        return 0.0


def _is_valid_audio_file(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 2048:
        return False
    return _probe_duration(path) > 0.1


def _discover_background_clips(options: RenderOptions) -> list[Path]:
    if not options.assets_dir.exists():
        return []

    exts = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
    backgrounds_root = options.assets_dir / "backgrounds"
    if backgrounds_root.exists():
        preferred_mp4 = sorted([p for p in backgrounds_root.rglob("*.mp4") if p.is_file()])
        if preferred_mp4:
            return preferred_mp4

    roots: list[Path] = []
    style_root = options.assets_dir / "backgrounds" / options.style
    common_root = options.assets_dir / "backgrounds" / "common"
    if style_root.exists():
        roots.append(style_root)
    if common_root.exists():
        roots.append(common_root)

    gameplay_root = options.assets_dir / "gameplay"
    videos_root = options.assets_dir / "videos"
    if gameplay_root.exists():
        roots.append(gameplay_root)
    if videos_root.exists():
        roots.append(videos_root)

    if not roots:
        roots.append(options.assets_dir)

    candidates: list[Path] = []
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in exts:
                candidates.append(path)

    return sorted(candidates)


def _keyword_slug(value: str) -> str:
    tokens = [tok.lower() for tok in re.findall(r"[A-Za-z0-9]+", value or "") if len(tok) >= 3]
    if not tokens:
        return "motion_background"
    return "_".join(tokens[:3])[:48].strip("_") or "motion_background"


def _unique_non_empty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _build_pexels_queries(entry: ScriptEntry) -> list[str]:
    visual_queries = _unique_non_empty(_coerce_list(entry.visual_queries))
    if visual_queries:
        return visual_queries[:8]

    parsed = _parse_script_file(entry.script_path)
    fallback_queries: list[str] = []
    fallback_queries.extend(_coerce_list(parsed.get("shot_list")))
    fallback_queries.extend(_coerce_list(parsed.get("broll_idea")))

    title_topic = " ".join(
        part
        for part in [
            str(parsed.get("title", "")).strip(),
            str(parsed.get("topic", "")).strip(),
            entry.video_title.strip(),
            entry.title.strip(),
        ]
        if part
    ).strip()
    if title_topic:
        fallback_queries.append(title_topic)

    if not fallback_queries:
        fallback_queries.append(entry.video_title or entry.title)

    return _unique_non_empty(fallback_queries)[:8]


def _pick_pexels_file_link(video: dict[str, Any]) -> str | None:
    files = video.get("video_files", []) if isinstance(video, dict) else []
    scored: list[tuple[int, int, str]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link", "")).strip()
        file_type = str(item.get("file_type", "")).strip().lower()
        if not link or (file_type and "mp4" not in file_type):
            continue
        try:
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
        except Exception:
            width, height = 0, 0
        if width <= 0 or height <= 0:
            continue
        portrait = 1 if height >= width else 0
        scored.append((portrait, width * height, link))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored[0][2]


def _fetch_pexels_background_clips(
    options: RenderOptions,
    entries: list[ScriptEntry],
    max_new_clips: int = 3,
) -> list[Path]:
    if max_new_clips <= 0:
        return []

    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key:
        print("Warning: Pexels background fetch skipped (PEXELS_API_KEY missing).")
        return []

    backgrounds_dir = options.assets_dir / "backgrounds"
    backgrounds_dir.mkdir(parents=True, exist_ok=True)

    all_queries: list[str] = []
    for entry in entries:
        all_queries.extend(_build_pexels_queries(entry))
        if len(all_queries) >= 24:
            break
    query_pool = _unique_non_empty(all_queries)
    if not query_pool:
        print("Warning: Pexels background fetch skipped (no usable search queries).")
        return []

    downloaded: list[Path] = []
    warned_api_failure = False
    for query in query_pool:
        if len(downloaded) >= max_new_clips:
            break

        base_slug = _keyword_slug(query)
        target_path = backgrounds_dir / f"{base_slug}_pexels.mp4"
        if target_path.exists() and target_path.stat().st_size > 2048:
            continue

        search_url = (
            "https://api.pexels.com/videos/search"
            f"?query={quote_plus(query)}&orientation=portrait&size=medium&per_page=6"
        )
        search_req = urllib.request.Request(
            search_url,
            headers={
                "Authorization": api_key,
                "User-Agent": "shorts-bot-render/1.0",
                "Accept": "application/json",
            },
            method="GET",
        )

        try:
            with urllib.request.urlopen(search_req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8", "ignore"))
        except Exception as exc:
            if not warned_api_failure:
                print(f"Warning: Pexels video search failed; continuing without Pexels ({exc}).")
                warned_api_failure = True
            continue

        videos = payload.get("videos", []) if isinstance(payload, dict) else []
        if not videos:
            continue

        clip_url: str | None = None
        for video in videos:
            clip_url = _pick_pexels_file_link(video if isinstance(video, dict) else {})
            if clip_url:
                break
        if not clip_url:
            continue

        download_req = urllib.request.Request(
            clip_url,
            headers={"User-Agent": "shorts-bot-render/1.0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(download_req, timeout=40) as resp:
                clip_bytes = resp.read()
        except Exception as exc:
            if not warned_api_failure:
                print(f"Warning: Pexels clip download failed; continuing without Pexels ({exc}).")
                warned_api_failure = True
            continue

        if not clip_bytes or len(clip_bytes) < 2048:
            continue
        target_path.write_bytes(clip_bytes)
        if _probe_duration(target_path) <= 0.1:
            target_path.unlink(missing_ok=True)
            continue

        downloaded.append(target_path)
        print(f"Pexels background saved: {target_path.name} (query: {query})")

    return downloaded


def _extract_visual_keywords(queries: list[str]) -> list[str]:
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "your",
        "from",
        "into",
        "how",
        "are",
        "you",
        "use",
    }
    seen: set[str] = set()
    keywords: list[str] = []
    for query in queries:
        for token in re.findall(r"[A-Za-z0-9]+", (query or "").lower()):
            if len(token) < 3 or token in stop_words or token in seen:
                continue
            seen.add(token)
            keywords.append(token)
    return keywords


def _choose_background_clip(
    clips: list[Path],
    seed_key: str,
    duration: float,
    visual_keywords: list[str] | None = None,
) -> tuple[Path | None, float]:
    if not clips:
        return None, 0.0

    ranked = clips
    if visual_keywords:
        scored: list[tuple[int, Path]] = []
        for clip in clips:
            name = clip.stem.lower()
            score = sum(1 for kw in visual_keywords if kw in name)
            scored.append((score, clip))
        top_score = max(score for score, _ in scored)
        if top_score > 0:
            ranked = [clip for score, clip in scored if score == top_score]

    seed_int = int(hashlib.md5(seed_key.encode("utf-8")).hexdigest()[:8], 16)
    clip = ranked[seed_int % len(ranked)]
    clip_duration = _probe_duration(clip)
    if clip_duration <= duration + 0.2:
        return clip, 0.0

    max_start = max(0.0, clip_duration - duration - 0.1)
    offset_ratio = (seed_int % 1000) / 1000.0
    start_at = max_start * offset_ratio
    return clip, start_at


def _render_motion_segment(
    clip_path: Path,
    start_at: float,
    duration: float,
    overlay_png: Path,
    output_path: Path,
    fade_overlay: bool = True,
) -> None:
    base = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    if fade_overlay:
        fade_out_start = max(0.0, duration - 0.28)
        overlay_chain = (
            f"[1:v]format=rgba,fade=t=in:st=0:d=0.22:alpha=1,"
            f"fade=t=out:st={fade_out_start:.2f}:d=0.22:alpha=1[cap]"
        )
    else:
        overlay_chain = "[1:v]format=rgba[cap]"
    filter_complex = f"[0:v]{base},setsar=1[bg];{overlay_chain};[bg][cap]overlay=0:0:shortest=1,format=yuv420p[v]"

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start_at:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(clip_path),
        "-loop",
        "1",
        "-i",
        str(overlay_png),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-an",
        "-r",
        str(FPS),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        str(output_path),
    ]
    _run_cmd(cmd)


def _concat_segments(segment_videos: list[Path], output_path: Path) -> None:
    concat_file = output_path.parent / f"{output_path.stem}_concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{video.absolute()}'" for video in segment_videos),
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(output_path),
    ]
    _run_cmd(cmd)
    concat_file.unlink(missing_ok=True)


def _add_silent_audio(input_video: Path, duration: float, output_video: Path) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_video),
        "-f",
        "lavfi",
        "-t",
        f"{duration:.3f}",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output_video),
    ]
    _run_cmd(cmd)


def _split_caption_phrases(lines: list[str], fallback_text: str) -> list[str]:
    source_lines = [line.strip() for line in lines if line and line.strip()]
    if not source_lines and fallback_text.strip():
        source_lines = [fallback_text.strip()]

    cards: list[list[str]] = []
    carry: list[str] = []
    for line in source_lines:
        phrases = [p.strip() for p in re.split(r"[.!?;:]+", line) if p.strip()]
        if not phrases:
            phrases = [line]

        for phrase in phrases:
            words = re.findall(r"[A-Za-z0-9']+", phrase)
            if not words:
                continue
            if carry:
                words = carry + words
                carry = []

            while len(words) > 6:
                take = 6
                if len(words) - take == 1:
                    take = 5
                cards.append(words[:take])
                words = words[take:]

            if len(words) == 1:
                if cards and len(cards[-1]) < 6:
                    cards[-1].append(words[0])
                else:
                    carry = words
            elif words:
                cards.append(words)

    if carry:
        if cards and len(cards[-1]) < 6:
            cards[-1].extend(carry)
        elif cards:
            cards.append([cards[-1][-1], carry[0]])
        else:
            cards.append(carry * 2)

    normalized: list[str] = []
    for card in cards:
        if len(card) <= 1:
            continue
        normalized.append(" ".join(card[:6]))
    return normalized


def _build_segment_plan(entry: ScriptEntry, style: str) -> list[dict[str, Any]]:
    preset = STYLE_PRESETS.get(style, STYLE_PRESETS["ai_student"])
    pace_mult = float(preset.get("pace_mult", 1.0))
    fallback = entry.caption or entry.hook or entry.cta or entry.video_title
    caption_cards = _split_caption_phrases(entry.caption_lines, fallback)
    if entry.cta:
        caption_cards.extend(_split_caption_phrases([entry.cta], entry.cta))
    if not caption_cards:
        caption_cards = ["Quick study tip"]

    segments: list[dict[str, Any]] = []
    if entry.on_screen_hook.strip():
        segments.append({"label": "HOOK", "text": entry.on_screen_hook.strip(), "duration": 2.5})

    for idx, card in enumerate(caption_cards, start=1):
        word_count = max(2, len(card.split()))
        duration = max(1.3, min(2.8, (0.95 + (0.26 * word_count)) * pace_mult))
        segments.append({"label": f"C{idx}", "text": card, "duration": duration})

    total = sum(item["duration"] for item in segments)
    if total > MAX_SHORT_SECONDS and segments:
        overflow = total - MAX_SHORT_SECONDS
        adjustable = [item for item in segments if item["label"].startswith("C")]
        if adjustable:
            reduce_each = overflow / len(adjustable)
            for item in adjustable:
                item["duration"] = max(1.1, item["duration"] - reduce_each)
    return segments


def _build_voiceover_text(entry: ScriptEntry) -> str:
    source_text = (entry.script_text or "").strip()
    if not source_text:
        parts: list[str] = []
        if entry.hook:
            parts.append(entry.hook)
        for line in entry.caption_lines[:8]:
            if line:
                parts.append(line)
        if entry.cta:
            parts.append(entry.cta)
        source_text = " ".join(part.strip() for part in parts if part.strip())
    return _sanitize_tts_text(source_text)


def _sanitize_tts_text(text: str) -> str:
    if not text:
        return ""
    cleaned = str(text)
    cleaned = re.sub(r"(?im)^\s*(HOOK|CTA|NARRATION|SCRIPT|CAPTION)\s*:\s*", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*(?:[-*•]+|\d+[.)])\s*", "", cleaned)
    cleaned = re.sub(r"#\w+", "", cleaned)
    cleaned = cleaned.replace("*", "")
    cleaned = re.sub(r"\bAI\b", "A.I.", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _generate_openai_tts_audio(text: str, output_path: Path, api_key: str) -> Path | None:
    if not api_key or not text.strip():
        return None

    payload = {
        "model": "gpt-4o-mini-tts",
        "voice": os.getenv("OPENAI_TTS_VOICE", "alloy"),
        "input": text,
        "response_format": "mp3",
    }
    req = urllib.request.Request(
        url="https://api.openai.com/v1/audio/speech",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            audio_bytes = resp.read()
    except urllib.error.HTTPError as exc:
        details = ""
        try:
            details = exc.read().decode("utf-8", "ignore")[:240]
        except Exception:
            details = ""
        print(f"OpenAI narration request failed ({exc.code}): {details}")
        return None
    except Exception as exc:
        print(f"OpenAI narration request failed: {exc}")
        return None

    if not audio_bytes:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(audio_bytes)
    if _is_valid_audio_file(output_path):
        print(f"Narration generated via OpenAI audio/speech: {output_path.name}")
        return output_path
    output_path.unlink(missing_ok=True)
    return None


def _generate_local_tts_audio(text: str, output_path: Path) -> Path | None:
    text = text.strip()
    if not text:
        return None

    say_bin = shutil.which("say")
    if say_bin:
        with tempfile.TemporaryDirectory(prefix="tts_local_") as tmp:
            tmp_dir = Path(tmp)
            aiff_path = tmp_dir / "voice.aiff"
            cmd = [say_bin, "-o", str(aiff_path), "-r", "185", text]
            if _run_cmd_checked(cmd):
                convert_cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(aiff_path),
                    "-ac",
                    "2",
                    "-ar",
                    "44100",
                    "-b:a",
                    "192k",
                    str(output_path),
                ]
                if _run_cmd_checked(convert_cmd):
                    return output_path

    espeak_bin = shutil.which("espeak")
    if espeak_bin:
        with tempfile.TemporaryDirectory(prefix="tts_local_") as tmp:
            tmp_dir = Path(tmp)
            wav_path = tmp_dir / "voice.wav"
            cmd = [espeak_bin, "-s", "165", "-w", str(wav_path), text]
            if _run_cmd_checked(cmd):
                convert_cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(wav_path),
                    "-ac",
                    "2",
                    "-ar",
                    "44100",
                    "-b:a",
                    "192k",
                    str(output_path),
                ]
                if _run_cmd_checked(convert_cmd):
                    return output_path

    return None


def _generate_voiceover_audio(entry: ScriptEntry, rendered_dir: Path, enable_voiceover: bool) -> Path | None:
    if not enable_voiceover:
        return None

    voice_text = _build_voiceover_text(entry)
    if not voice_text:
        print(f"Narration skipped (empty narration text): {entry.script_path.name}")
        return None

    voice_path = rendered_dir / f"{entry.script_path.stem}_voice.mp3"
    if _is_valid_audio_file(voice_path):
        print(f"Reusing existing narration: {voice_path.name}")
        return voice_path
    voice_path.unlink(missing_ok=True)

    eleven_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()

    if eleven_key or openai_key:
        try:
            from voiceover import VoiceoverEngine

            engine = VoiceoverEngine(
                {
                    "ELEVENLABS_API_KEY": eleven_key,
                    "OPENAI_API_KEY": openai_key,
                    "NICHE": "ai_tools",
                }
            )
            generated = engine.generate(voice_text, rendered_dir)
            if generated and Path(generated).exists():
                if Path(generated) != voice_path:
                    shutil.copy2(generated, voice_path)
                if _is_valid_audio_file(voice_path):
                    print(f"Narration generated via VoiceoverEngine: {voice_path.name}")
                    return voice_path
                voice_path.unlink(missing_ok=True)
        except Exception as exc:
            print(f"Narration provider logic failed, trying fallback paths: {exc}")

    openai_direct = _generate_openai_tts_audio(text=voice_text, output_path=voice_path, api_key=openai_key)
    if openai_direct and _is_valid_audio_file(openai_direct):
        return openai_direct
    voice_path.unlink(missing_ok=True)

    local_generated = _generate_local_tts_audio(voice_text, voice_path)
    if local_generated and _is_valid_audio_file(local_generated):
        print(f"Narration generated via local TTS: {voice_path.name}")
        return local_generated
    voice_path.unlink(missing_ok=True)

    print(f"Narration generation failed for {entry.script_path.name}; continuing without narration.")
    return None


def _discover_music_tracks(music_dir: Path) -> list[Path]:
    if not music_dir.exists():
        return []
    exts = {".mp3", ".wav"}
    return sorted([p for p in music_dir.rglob("*") if p.is_file() and p.suffix.lower() in exts])


def _music_preference_candidates(entry: ScriptEntry) -> list[str]:
    primary_sources = [
        entry.title,
        entry.video_title,
        entry.topic,
        entry.angle,
    ]
    secondary_sources = [
        entry.caption,
        " ".join(entry.hashtags),
    ]
    primary_text = " ".join(part for part in primary_sources if part).lower().replace("#", " ")
    secondary_text = " ".join(part for part in secondary_sources if part).lower().replace("#", " ")
    primary_tokens = set(re.findall(r"[a-z0-9]+", primary_text))
    secondary_tokens = set(re.findall(r"[a-z0-9]+", secondary_text))

    preference_rules: list[tuple[set[str], list[str]]] = [
        (
            {"ai", "tech", "productivity", "tool", "tools", "automation"},
            ["tech_ambient.mp3", "tech_ambient.wav"],
        ),
        (
            {"storytelling", "story", "mystery", "horror", "confession"},
            ["dark_tension.mp3", "dark_tension.wav"],
        ),
        (
            {"motivation", "discipline", "success", "mindset"},
            ["motivational_pulse.mp3", "motivational_pulse.wav"],
        ),
        (
            {"brainrot", "meme", "memes", "streamer", "gaming", "game"},
            ["clean_upbeat.mp3", "clean_upbeat.wav"],
        ),
    ]

    for keywords, filenames in preference_rules:
        if any(keyword in primary_tokens for keyword in keywords):
            return filenames
    for keywords, filenames in preference_rules:
        if any(keyword in secondary_tokens for keyword in keywords):
            return filenames
    return []


def _choose_music_track(tracks: list[Path], entry: ScriptEntry) -> tuple[Path | None, str]:
    if not tracks:
        return None, "none"

    preferred_filenames = _music_preference_candidates(entry)
    track_map = {track.name.lower(): track for track in tracks}
    for filename in preferred_filenames:
        preferred = track_map.get(filename.lower())
        if preferred:
            return preferred, "preferred match"

    return random.choice(tracks), "fallback"


def _mux_audio(
    input_video: Path,
    output_video: Path,
    duration: float,
    voice_path: Path | None,
    music_path: Path | None,
    style: str,
) -> None:
    music_volume = 0.08

    if voice_path and music_path:
        filter_complex = (
            f"[2:a]volume={music_volume:.3f},aresample=44100[music];"
            f"[music][1:a]sidechaincompress=threshold=0.03:ratio=12:attack=15:release=300[ducked];"
            f"[1:a][ducked]amix=inputs=2:duration=first:normalize=0[aout]"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_video),
            "-i",
            str(voice_path),
            "-stream_loop",
            "-1",
            "-i",
            str(music_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_video),
        ]
        _run_cmd(cmd)
        return

    if voice_path:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_video),
            "-i",
            str(voice_path),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_video),
        ]
        _run_cmd(cmd)
        return

    if music_path:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_video),
            "-stream_loop",
            "-1",
            "-i",
            str(music_path),
            "-filter:a",
            f"volume={music_volume:.3f}",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_video),
        ]
        _run_cmd(cmd)
        return

    _add_silent_audio(input_video=input_video, duration=duration, output_video=output_video)


def _render_entry(
    entry: ScriptEntry,
    rendered_dir: Path,
    theme_idx: int,
    options: RenderOptions,
    background_clips: list[Path],
    music_tracks: list[Path],
) -> tuple[Path, Path]:
    output_video = rendered_dir / f"{entry.script_path.stem}.mp4"
    output_meta = rendered_dir / f"{entry.script_path.stem}.json"

    segments = _build_segment_plan(entry, style=options.style)
    total_duration = sum(item["duration"] for item in segments)
    visual_keywords = _extract_visual_keywords(entry.visual_queries)

    used_background_assets = bool(background_clips)

    with tempfile.TemporaryDirectory(prefix="render_", dir=str(rendered_dir)) as tmp:
        tmp_dir = Path(tmp)
        segment_videos: list[Path] = []

        for idx, seg in enumerate(segments, start=1):
            seg_video = tmp_dir / f"segment_{idx:02d}.mp4"

            if used_background_assets:
                overlay_png = tmp_dir / f"overlay_{idx:02d}.png"
                _draw_caption_overlay(
                    output_path=overlay_png,
                    main_text=seg["text"],
                    title_text=entry.video_title,
                    label=seg["label"],
                    style=options.style,
                )
                clip_path, start_at = _choose_background_clip(
                    background_clips,
                    seed_key=f"{entry.script_path.name}:{idx}:{options.style}",
                    duration=float(seg["duration"]),
                    visual_keywords=visual_keywords,
                )
                if clip_path is None:
                    used_background_assets = False
                else:
                    _render_motion_segment(
                        clip_path=clip_path,
                        start_at=start_at,
                        duration=float(seg["duration"]),
                        overlay_png=overlay_png,
                        output_path=seg_video,
                        fade_overlay=seg["label"] != "HOOK",
                    )
                    segment_videos.append(seg_video)
                    continue

            # Fallback to legacy static renderer for this segment.
            image_path = tmp_dir / f"segment_{idx:02d}.png"
            footer = entry.caption or entry.cta

            _draw_segment_image(
                output_path=image_path,
                main_text=seg["text"],
                title_text=entry.video_title,
                footer_text=footer,
                theme_idx=theme_idx + idx,
                label=seg["label"],
                style=options.style,
            )
            _make_segment_video(image_path=image_path, duration=float(seg["duration"]), output_path=seg_video)
            segment_videos.append(seg_video)

        concat_video = tmp_dir / "concat.mp4"
        _concat_segments(segment_videos=segment_videos, output_path=concat_video)

        voice_path = _generate_voiceover_audio(entry=entry, rendered_dir=rendered_dir, enable_voiceover=options.enable_voiceover)
        music_path, music_reason = _choose_music_track(music_tracks, entry=entry)
        if music_path:
            print(f"Music bed selected: {music_path.name} ({music_reason})")

        _mux_audio(
            input_video=concat_video,
            output_video=output_video,
            duration=total_duration,
            voice_path=voice_path,
            music_path=music_path,
            style=options.style,
        )

    metadata = {
        "title": entry.video_title or entry.title,
        "caption": entry.caption,
        "cta": entry.cta,
        "hashtags": entry.hashtags,
        "visual_queries": entry.visual_queries,
        "source_script_filename": entry.script_path.name,
        "render_style": options.style,
        "has_voiceover": bool(_probe_duration(rendered_dir / f"{entry.script_path.stem}_voice.mp3")),
        "background_mode": "motion_assets" if used_background_assets else "slide_fallback",
    }
    output_meta.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return output_video, output_meta


def render_batch(batch_dir: Path, style_override: str | None = None) -> tuple[list[Path], list[Path]]:
    if load_dotenv is not None:
        load_dotenv()
    _check_ffmpeg()
    entries = _collect_entries(batch_dir)
    rendered_dir = batch_dir / "rendered"
    rendered_dir.mkdir(parents=True, exist_ok=True)

    options = _load_render_options(style_override)
    background_clips = _discover_background_clips(options)
    min_desired_clips = 3
    if len(background_clips) < min_desired_clips:
        needed = min_desired_clips - len(background_clips)
        fetched = _fetch_pexels_background_clips(options=options, entries=entries, max_new_clips=needed)
        if fetched:
            if background_clips:
                print(
                    f"Pexels fetched {len(fetched)} clip(s) to supplement assets/backgrounds/."
                    " Using existing local assets first for this render."
                )
            else:
                background_clips = _discover_background_clips(options)
    music_tracks = _discover_music_tracks(options.music_dir)

    if background_clips:
        print(f"Using motion background assets: {len(background_clips)} clip(s) for style '{options.style}'.")
    else:
        print("No background video assets found. Using slide renderer fallback.")

    if music_tracks:
        print(f"Music bed enabled: {len(music_tracks)} track(s) discovered.")
    else:
        print("No music tracks found. Rendering without music bed.")

    videos: list[Path] = []
    metas: list[Path] = []

    for idx, entry in enumerate(entries):
        video, meta = _render_entry(
            entry=entry,
            rendered_dir=rendered_dir,
            theme_idx=idx,
            options=options,
            background_clips=background_clips,
            music_tracks=music_tracks,
        )
        videos.append(video)
        metas.append(meta)
        print(f"Rendered: {video}")

    return videos, metas


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a batch folder into Shorts videos.")
    parser.add_argument(
        "batch_dir",
        nargs="?",
        help="Optional batch folder path. If omitted, the newest drafts/batch_* folder is used.",
    )
    parser.add_argument(
        "--style",
        choices=sorted(STYLE_PRESETS.keys()),
        help="Optional style override (otherwise render_config.json / SHORTS_STYLE is used).",
    )
    args = parser.parse_args()

    if args.batch_dir:
        batch_dir = Path(args.batch_dir)
    else:
        batch_dir = _find_latest_batch(Path("drafts"))

    if not batch_dir.exists() or not batch_dir.is_dir():
        print(f"Batch folder not found: {batch_dir}")
        return 1

    try:
        videos, metas = render_batch(batch_dir=batch_dir, style_override=args.style)
    except Exception as exc:
        print(f"Render failed: {exc}")
        return 1

    print(f"\nBatch folder: {batch_dir}")
    print(f"Rendered dir: {batch_dir / 'rendered'}")
    print(f"Videos created: {len(videos)}")
    print(f"Metadata JSON created: {len(metas)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
