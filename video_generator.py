"""
Video Generator - Assembles YouTube Shorts from stock footage + voiceover.

Pipeline:
  1. Fetches vertical stock videos (Pexels/Pixabay API)
  2. Crops/resizes to 1080x1920 (9:16)
  3. Cuts clips to match narration timing
  4. Overlays animated captions (word-by-word for retention)
  5. Adds subtle background music
  6. Renders final MP4 with FFmpeg

Output: Production-ready 1080x1920 MP4 under 60 seconds.
"""

import os
import re
import json
import time
import logging
import subprocess
import requests
import hashlib
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("RevenueMachine.VideoGenerator")

# Background music tracks (royalty-free)
BACKGROUND_MUSIC = {
    "ai_tools": "assets/music/tech_ambient.mp3",
    "finance": "assets/music/corporate_soft.mp3",
    "motivation": "assets/music/epic_lo_fi.mp3",
    "mystery": "assets/music/dark_ambient.mp3",
}


class VideoGenerator:
    def __init__(self, config: dict):
        self.config = config
        self.pexels_key = config.get("PEXELS_API_KEY", "")
        self.pixabay_key = config.get("PIXABAY_API_KEY", "")
        self.niche = config.get("NICHE", "ai_tools")

        # Verify FFmpeg is installed
        try:
            result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
            self.ffmpeg_available = result.returncode == 0
        except Exception:
            self.ffmpeg_available = False
            logger.warning("⚠️  FFmpeg not found. Install: sudo apt install ffmpeg")

        # Ensure asset dirs exist
        Path("assets/music").mkdir(parents=True, exist_ok=True)
        Path("assets/fonts").mkdir(parents=True, exist_ok=True)
        Path("cache/footage").mkdir(parents=True, exist_ok=True)

    def fetch_stock_footage(self, queries: list, count: int = 5) -> list:
        """
        Fetches stock footage clips for each scene.
        Returns list of local file paths.
        """
        clips = []

        for i, query in enumerate(queries[:count]):
            logger.info(f"  🎥 Fetching footage: '{query}' ({i+1}/{min(len(queries), count)})")

            # Try Pexels first (better quality)
            clip_path = self._fetch_pexels_video(query)

            # Fallback to Pixabay
            if not clip_path and self.pixabay_key:
                clip_path = self._fetch_pixabay_video(query)

            if clip_path:
                clips.append({"path": clip_path, "query": query})
            else:
                logger.warning(f"  ⚠️  No footage found for '{query}', using placeholder")
                clips.append({"path": None, "query": query})

        return clips

    def assemble(
        self,
        audio: Path,
        footage: list,
        script: dict,
        output_dir: Path,
        add_captions: bool = True,
        add_background_music: bool = True,
        add_progress_bar: bool = False,
    ) -> Path:
        """
        Main assembly function. Creates the final Short video.
        """
        if not self.ffmpeg_available:
            logger.error("FFmpeg not available - cannot assemble video")
            raise RuntimeError("FFmpeg required")

        timestamp = int(time.time())
        output_path = output_dir / f"short_{timestamp}.mp4"
        output_dir.mkdir(exist_ok=True)

        # Get audio duration
        audio_duration = self._get_audio_duration(audio)
        logger.info(f"  ⏱  Audio duration: {audio_duration:.1f}s")

        # Step 1: Prepare video clips (resize to 1080x1920, cut to duration)
        video_clips_file = self._prepare_video_clips(footage, audio_duration)

        # Step 2: Concatenate clips
        concat_video = output_dir / f"concat_{timestamp}.mp4"
        self._concatenate_clips(video_clips_file, concat_video)

        # Step 3: Overlay audio
        video_with_audio = output_dir / f"audio_{timestamp}.mp4"
        self._add_audio(concat_video, audio, video_with_audio)

        # Step 4: Add animated captions
        if add_captions:
            video_with_captions = output_dir / f"captions_{timestamp}.mp4"
            self._add_animated_captions(
                video_with_audio,
                script["narration"],
                audio_duration,
                video_with_captions
            )
            current_video = video_with_captions
        else:
            current_video = video_with_audio

        # Step 5: Add background music
        if add_background_music:
            music_path = BACKGROUND_MUSIC.get(self.niche)
            if music_path and Path(music_path).exists():
                video_with_music = output_dir / f"music_{timestamp}.mp4"
                self._add_background_music(current_video, music_path, video_with_music)
                current_video = video_with_music

        # Step 6: Final render with optimization
        self._final_render(current_video, output_path)

        # Cleanup temp files
        self._cleanup_temp_files(output_dir, timestamp)

        file_size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(f"  ✅ Video assembled: {output_path.name} ({file_size_mb:.1f}MB, {audio_duration:.0f}s)")
        return output_path

    # ────────────────────────────────────────────────
    #  STOCK FOOTAGE FETCHING
    # ────────────────────────────────────────────────

    def _fetch_pexels_video(self, query: str) -> Optional[Path]:
        """Fetch a relevant vertical video from Pexels."""
        if not self.pexels_key:
            return None

        # Check cache
        cache_key = hashlib.md5(query.encode()).hexdigest()[:8]
        cache_path = Path(f"cache/footage/pexels_{cache_key}.mp4")
        if cache_path.exists():
            return cache_path

        try:
            resp = requests.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": self.pexels_key},
                params={
                    "query": query,
                    "orientation": "portrait",  # Vertical = Shorts ready
                    "size": "medium",
                    "per_page": 5,
                },
                timeout=15,
            )

            if resp.status_code != 200:
                return None

            data = resp.json()
            videos = data.get("videos", [])
            if not videos:
                return None

            # Pick the video with best quality portrait format
            for video in videos:
                for vf in video.get("video_files", []):
                    if vf.get("width", 0) < vf.get("height", 0):  # Portrait
                        video_url = vf["link"]
                        return self._download_video(video_url, cache_path)

            # Fallback: take first video
            video_url = videos[0]["video_files"][0]["link"]
            return self._download_video(video_url, cache_path)

        except Exception as e:
            logger.warning(f"  Pexels error: {e}")
            return None

    def _fetch_pixabay_video(self, query: str) -> Optional[Path]:
        """Fallback: Fetch video from Pixabay."""
        try:
            cache_key = hashlib.md5(f"pixabay_{query}".encode()).hexdigest()[:8]
            cache_path = Path(f"cache/footage/pixabay_{cache_key}.mp4")
            if cache_path.exists():
                return cache_path

            resp = requests.get(
                "https://pixabay.com/api/videos/",
                params={
                    "key": self.pixabay_key,
                    "q": query,
                    "video_type": "film",
                    "per_page": 5,
                },
                timeout=15,
            )

            data = resp.json()
            hits = data.get("hits", [])
            if not hits:
                return None

            video_url = hits[0]["videos"]["medium"]["url"]
            return self._download_video(video_url, cache_path)

        except Exception as e:
            logger.warning(f"  Pixabay error: {e}")
            return None

    def _download_video(self, url: str, save_path: Path) -> Optional[Path]:
        """Downloads a video file with progress."""
        try:
            resp = requests.get(url, stream=True, timeout=60)
            save_path.parent.mkdir(parents=True, exist_ok=True)

            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            return save_path
        except Exception as e:
            logger.warning(f"  Download error: {e}")
            return None

    # ────────────────────────────────────────────────
    #  FFMPEG OPERATIONS
    # ────────────────────────────────────────────────

    def _get_audio_duration(self, audio_path: Path) -> float:
        """Gets audio file duration in seconds using FFprobe."""
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            str(audio_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(result.stdout)
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "audio":
                return float(stream.get("duration", 45))
        return 45.0

    def _prepare_video_clips(self, footage: list, total_duration: float) -> Path:
        """
        Crops, resizes each clip to 1080x1920 and trims duration.
        Returns path to a concat list file.
        """
        clip_duration = total_duration / max(len(footage), 1)
        concat_list = Path("cache/concat_list.txt")
        lines = []

        for i, clip_info in enumerate(footage):
            clip_path = clip_info.get("path")

            if not clip_path or not Path(str(clip_path)).exists():
                # Generate colored placeholder
                placeholder = self._create_placeholder_clip(
                    clip_info.get("query", "visual"),
                    clip_duration,
                    i
                )
                lines.append(f"file '{placeholder.absolute()}'")
                continue

            # Resize and crop to 1080x1920
            processed = Path(f"cache/clip_{i}_processed.mp4")
            cmd = [
                "ffmpeg", "-y",
                "-i", str(clip_path),
                "-t", str(clip_duration + 0.5),  # Slight overlap
                "-vf", (
                    "scale=1080:1920:force_original_aspect_ratio=increase,"
                    "crop=1080:1920"
                ),
                "-r", "30",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-an",  # Remove audio from footage
                str(processed),
            ]
            subprocess.run(cmd, capture_output=True)
            lines.append(f"file '{processed.absolute()}'")

        with open(concat_list, "w") as f:
            f.write("\n".join(lines))

        return concat_list

    def _concatenate_clips(self, concat_file: Path, output: Path):
        """Concatenates clips using FFmpeg concat demuxer."""
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(output),
        ]
        subprocess.run(cmd, capture_output=True)

    def _add_audio(self, video: Path, audio: Path, output: Path):
        """Merges video and audio, trims to audio duration."""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video),
            "-i", str(audio),
            "-c:v", "copy",
            "-c:a", "aac",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            str(output),
        ]
        subprocess.run(cmd, capture_output=True)

    def _add_animated_captions(self, video: Path, narration: str, duration: float, output: Path):
        """
        Adds time-synced caption lines to the video.

        Captions are rendered to transparent PNGs with PIL and composited via
        FFmpeg's `overlay` filter. This deliberately avoids the `drawtext`
        filter: many FFmpeg builds (including the Homebrew core bottle) ship
        without libfreetype, so `drawtext` is unavailable. `overlay` + PIL
        works on any FFmpeg build.
        """
        import shutil

        try:
            from PIL import Image, ImageDraw
        except ImportError:
            logger.warning("PIL not installed - skipping captions (pip install Pillow)")
            shutil.copy(str(video), str(output))
            return

        # ~3-word caption lines - short enough to render on one line, which
        # keeps per-word x-positioning (for the karaoke highlight) simple.
        words = narration.split()
        if not words:
            shutil.copy(str(video), str(output))
            return
        lines = [words[i:i + 3] for i in range(0, len(words), 3)]

        width, height = self._probe_dimensions(video)
        font = self._load_caption_font(max(int(height * 0.0375), 24))  # ~72px @ 1920h

        # Two estimated timing levels, both summing to `duration`: each LINE's
        # window is weighted by word count; within a line each WORD's slot is
        # weighted by character count. Estimated (not ElevenLabs timestamps) so
        # captions stay a pure post-process - no extra API plumbing and no
        # per-turn offset math that could desync.
        line_word_counts = [len(ln) for ln in lines]
        total_words = sum(line_word_counts)
        line_durations = [duration * wc / total_words for wc in line_word_counts]

        caption_dir = output.parent / f"_captions_{int(time.time())}"
        caption_dir.mkdir(parents=True, exist_ok=True)

        _HIGHLIGHT = (255, 214, 10)   # active word - punchy yellow
        _BASE = (255, 255, 255)       # inactive words - white
        _STROKE = 6
        cap_y = int(height * 0.75)
        _scratch = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        space_w = _scratch.textlength(" ", font=font)

        # entries: (png_path, on-screen seconds) in play order. One PNG per
        # word state - the whole line drawn, with exactly one word highlighted.
        entries: list[tuple[Path, float]] = []
        try:
            png_index = 0
            for line_words, line_dur in zip(lines, line_durations):
                char_counts = [max(len(w), 1) for w in line_words]
                total_chars = sum(char_counts)
                word_durs = [line_dur * c / total_chars for c in char_counts]
                widths = [_scratch.textlength(w, font=font) for w in line_words]
                line_w = sum(widths) + space_w * (len(line_words) - 1)
                start_x = (width - line_w) / 2.0

                for active in range(len(line_words)):
                    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                    draw = ImageDraw.Draw(img)
                    x = start_x
                    for j, w in enumerate(line_words):
                        draw.text(
                            (x, cap_y), w, font=font,
                            fill=_HIGHLIGHT if j == active else _BASE,
                            stroke_width=_STROKE, stroke_fill="black", anchor="lm",
                        )
                        x += widths[j] + space_w
                    png = caption_dir / f"cap_{png_index:04d}.png"
                    img.save(str(png))
                    entries.append((png, word_durs[active]))
                    png_index += 1

            # The caption track is built with the concat *demuxer* (a playlist
            # file), never the concat *filter*. Three earlier designs failed:
            #  - one `-i` per line + chained `overlay` filters: single-frame
            #    image inputs hit EOF, so captions past ~6-7s never showed;
            #  - one `-i` per line + the concat *filter*: 30+ image inputs
            #    deadlocked ffmpeg's filtergraph - the render hung for hours;
            #  - the concat demuxer piped DIRECTLY into the overlay
            #    filtergraph: the demuxer's frame pacing fought the overlay's
            #    framesync and captions came out in chunks, then stopped dead
            #    at ~20s of a 37s video.
            # The fix is two separate ffmpeg passes. Pass 1 renders the concat
            # playlist into a standalone caption-track file (qtrle keeps the
            # alpha channel). Pass 2 overlays that finished file onto the base
            # video. A pre-rendered track file overlays cleanly; a live concat
            # demuxer inside the same graph does not.
            list_file = caption_dir / "captions.txt"
            with open(list_file, "w") as fh:
                for png, dur in entries:
                    fh.write(f"file '{png.name}'\n")
                    fh.write(f"duration {dur:.3f}\n")
                # The concat demuxer drops the final entry's `duration` unless
                # the last file is repeated - repeat it so the last word
                # holds its window instead of flashing one frame.
                fh.write(f"file '{entries[-1][0].name}'\n")

            track = caption_dir / "caption_track.mov"
            track_cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", str(list_file),
                # `fps=30` here makes the track CONSTANT-framerate: the concat
                # demuxer emits one sparse frame per caption line, and
                # `overlay`'s framesync drops some of those sparse frames
                # (coverage came out at 26/37s with visible multi-second
                # gaps). Duplicating to a dense 30fps track gives every base
                # frame a caption frame to pair with - gapless coverage.
                # Running `fps` here, on a standalone track file, is safe;
                # the failure mode was `fps` on a concat demuxer piped live
                # into the overlay graph, which this two-pass split avoids.
                "-vf", "fps=30",
                "-c:v", "qtrle", "-pix_fmt", "argb",
                str(track),
            ]
            overlay_cmd = [
                "ffmpeg", "-y",
                "-i", str(video),
                "-i", str(track),
                "-filter_complex",
                "[0:v][1:v]overlay=0:0:eof_action=pass[outv]",
                "-map", "[outv]", "-map", "0:a?",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "copy",
                str(output),
            ]
            try:
                result = subprocess.run(track_cmd, capture_output=True, timeout=600)
                if result.returncode == 0:
                    result = subprocess.run(overlay_cmd, capture_output=True, timeout=600)
            except subprocess.TimeoutExpired:
                logger.warning("Caption render exceeded 600s - using video without captions")
                shutil.copy(str(video), str(output))
                return
        finally:
            shutil.rmtree(caption_dir, ignore_errors=True)

        if result.returncode != 0:
            logger.warning("Caption overlay failed, using video without captions")
            logger.debug(
                "ffmpeg caption stderr: %s",
                result.stderr.decode("utf-8", "ignore")[-600:],
            )
            shutil.copy(str(video), str(output))
        else:
            logger.info(f"  💬 Karaoke captions overlaid: {len(entries)} word states")

    def _probe_dimensions(self, video_path: Path) -> tuple:
        """Returns (width, height) of the video, defaulting to 1080x1920."""
        try:
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_streams", str(video_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            data = json.loads(result.stdout)
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    w = int(stream.get("width") or 0)
                    h = int(stream.get("height") or 0)
                    if w > 0 and h > 0:
                        return w, h
        except Exception:
            pass
        return 1080, 1920

    @staticmethod
    def _load_caption_font(size: int):
        """Loads a bold TrueType font, falling back across common OS paths."""
        from PIL import ImageFont
        font_paths = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
        for fp in font_paths:
            if Path(fp).exists():
                try:
                    return ImageFont.truetype(fp, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    @staticmethod
    def _wrap_caption(draw, text: str, font, max_width: int) -> str:
        """Greedy word-wrap so rendered text stays within max_width."""
        words = text.split()
        if not words:
            return text
        lines, current = [], words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            bbox = draw.textbbox((0, 0), trial, font=font, stroke_width=6)
            if (bbox[2] - bbox[0]) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return "\n".join(lines)

    def _add_background_music(self, video: Path, music_path: str, output: Path):
        """Adds subtle background music at -25dB under narration."""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video),
            "-i", music_path,
            "-filter_complex",
            "[1:a]volume=0.15,aloop=loop=-1:size=2e+09[bg];"
            "[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[a]",
            "-map", "0:v",
            "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            import shutil
            shutil.copy(str(video), str(output))

    def _final_render(self, input_video: Path, output_path: Path):
        """Final render with YouTube-optimized settings."""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_video),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "22",
            "-profile:v", "high",
            "-level", "4.0",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",  # Optimize for streaming
            "-pix_fmt", "yuv420p",
            "-r", "30",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True)

    def _create_placeholder_clip(self, text: str, duration: float, index: int) -> Path:
        """Creates a colored placeholder when no footage is available."""
        colors = ["#1a1a2e", "#16213e", "#0f3460", "#533483"]
        color = colors[index % len(colors)]
        output = Path(f"cache/placeholder_{index}.mp4")

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c={color}:s=1080x1920:d={duration}",
            "-c:v", "libx264",
            "-t", str(duration),
            str(output),
        ]
        subprocess.run(cmd, capture_output=True)
        return output

    def _cleanup_temp_files(self, output_dir: Path, timestamp: int):
        """Removes intermediate temp files."""
        patterns = [f"concat_{timestamp}.mp4", f"audio_{timestamp}.mp4",
                    f"captions_{timestamp}.mp4", f"music_{timestamp}.mp4"]
        for pattern in patterns:
            path = output_dir / pattern
            if path.exists():
                path.unlink()
