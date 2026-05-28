"""pivot.pipeline.render_stills - the net-new render core.

Builds a caption-free vertical Short from per-segment (image + audio):
  per segment -> ffmpeg zoompan motion clip (len = audio duration), VIDEO ONLY
  -> concat the silent clips -> concat the raw audio into ONE gapless track
  -> mux once -> final 1080x1920 mp4, audio baked, NO captions.

Why audio is muxed once at the end, not per segment: encoding each segment's
audio to AAC separately and then joining the segment MP4s with `-c copy` bakes
AAC encoder-delay/priming silence into every segment and exposes it at each
boundary (the concat demuxer can't do gapless playback across separately
encoded AAC streams). Measured: per-segment-AAC+concat ran ~48ms long over 7
segments, audible as awkward pauses between segments. Decoding the source
clips and encoding the joined audio a single time is sample-exact gapless.
The TTS clips themselves carry <=14ms of edge silence, so trimming them is
both unnecessary and risky (it cuts into speech) - the gap was never in them.

Captions are added downstream in Submagic, so output is intentionally clean.
Pure ffmpeg/ffprobe via subprocess - no paid SDKs - fully verifiable offline.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .config import Config
from .tts import probe_duration


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd)}\n{p.stderr[-1500:]}")


def _zoom_expr(index: int, cfg: Config) -> str:
    """Alternate zoom-in / zoom-out per segment; linear in output-frame 'on'."""
    r, zmax = cfg.zoom_rate, cfg.zoom_max
    if index % 2 == 0:  # zoom in
        return f"min(1.0+{r}*on,{zmax})"
    return f"max({zmax}-{r}*on,1.0)"  # zoom out


def render_segment(image_path: Path, audio_path: Path, out_path: Path,
                   index: int, cfg: Config) -> Path:
    """Render one VIDEO-ONLY zoompan motion clip, length = this segment's audio.

    Audio is intentionally NOT muxed here; it is joined once at the end (see
    module docstring) so AAC priming silence cannot accumulate at boundaries.
    audio_path is still read to size the clip to the narration duration.
    """
    dur = probe_duration(audio_path)
    frames = max(1, round(dur * cfg.fps))
    big_w, big_h = cfg.width * 2, cfg.height * 2
    vf = (
        f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
        f"crop={big_w}:{big_h},"
        f"zoompan=z='{_zoom_expr(index, cfg)}':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={cfg.width}x{cfg.height}:fps={cfg.fps},format=yuv420p"
    )
    _run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
        "-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-t", f"{dur:.3f}", "-movflags", "+faststart", str(out_path),
    ])
    return out_path


def concat_segments(segment_mp4s: list[Path], out_path: Path) -> Path:
    """Concat the silent segment clips. Video stream copy is gapless (no priming)."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for m in segment_mp4s:
            f.write(f"file '{m.resolve()}'\n")
        listfile = f.name
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
          "-c", "copy", "-movflags", "+faststart", str(out_path)])
    Path(listfile).unlink(missing_ok=True)
    return out_path


def _concat_audio(audio_paths: list[Path], out_path: Path, cfg: Config) -> Path:
    """Join the raw per-segment audio into ONE gapless AAC track.

    The concat demuxer decodes each source clip (applying its own gapless
    trimming) and we encode the joined PCM a single time, so the result is
    sample-exact: total duration == sum of segment durations, with no
    encoder-delay silence injected at the joins.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for a in audio_paths:
            f.write(f"file '{a.resolve()}'\n")
        listfile = f.name
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
          "-c:a", "aac", "-b:a", "192k", "-ar", "44100", str(out_path)])
    Path(listfile).unlink(missing_ok=True)
    return out_path


def render_video(image_paths: list[Path], audio_paths: list[Path],
                 out_path: Path, cfg: Config) -> dict:
    if len(image_paths) != len(audio_paths):
        raise ValueError("image/audio count mismatch")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seg_mp4s = []
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        for i, (img, aud) in enumerate(zip(image_paths, audio_paths)):
            seg_mp4s.append(render_segment(img, aud, tdp / f"seg_{i:02d}.mp4", i, cfg))
        silent_video = tdp / "video_silent.mp4"
        concat_segments(seg_mp4s, silent_video)
        audio_track = tdp / "audio.m4a"
        _concat_audio(audio_paths, audio_track, cfg)
        # Final mux: stream-copy both (single audio track -> no boundary gaps).
        _run([
            "ffmpeg", "-y", "-i", str(silent_video), "-i", str(audio_track),
            "-map", "0:v", "-map", "1:a", "-c", "copy", "-shortest",
            "-movflags", "+faststart", str(out_path),
        ])
    return {"path": str(out_path), "segments": len(image_paths),
            "duration_s": probe_duration(out_path)}


def stream_info(path: Path) -> dict:
    """ffprobe summary used by acceptance checks."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
         str(path)], capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    v = [s for s in streams if s.get("codec_type") == "video"]
    a = [s for s in streams if s.get("codec_type") == "audio"]
    sub = [s for s in streams if s.get("codec_type") == "subtitle"]
    fps = 0.0
    if v:
        num, den = (v[0].get("r_frame_rate", "0/1").split("/") + ["1"])[:2]
        fps = float(num) / float(den or 1)
    return {
        "width": v[0]["width"] if v else None,
        "height": v[0]["height"] if v else None,
        "fps": round(fps, 3),
        "video_streams": len(v), "audio_streams": len(a), "subtitle_streams": len(sub),
        "duration_s": float(data["format"]["duration"]),
    }


if __name__ == "__main__":
    cfg = Config.load()
    colors = ["0x14283c", "0x3c2814", "0x143c28", "0x28143c"]
    durs = [3.0, 2.4, 4.1, 2.0]
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        imgs, auds = [], []
        for i, (c, d) in enumerate(zip(colors, durs)):
            ip, ap = tdp / f"i{i}.png", tdp / f"a{i}.mp3"
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                            f"color=c={c}:s=1024x1536", "-frames:v", "1", str(ip)],
                           capture_output=True, check=True)
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                            f"sine=frequency={200+i*40}:duration={d}", "-q:a", "9", str(ap)],
                           capture_output=True, check=True)
            imgs.append(ip); auds.append(ap)
        out = tdp / "final.mp4"
        info = render_video(imgs, auds, out, cfg)
        si = stream_info(out)
        expected = sum(durs)
        # Gapless regression check: the muxed audio track must equal the sum of
        # the source segment durations to within a frame. The old per-segment
        # AAC + concat-copy path injected ~8ms of priming silence per boundary,
        # so this would drift; the single-encode gapless path is sample-exact.
        audio_dur = next(
            float(s["duration"]) for s in json.loads(subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a:0",
                 "-show_streams", "-of", "json", str(out)],
                capture_output=True, text=True, check=True).stdout)["streams"])
        gap_drift = abs(audio_dur - expected)
        print("render result:", info)
        print("stream_info:", si)
        print(f"audio track {audio_dur:.4f}s vs sum {expected:.4f}s -> drift {gap_drift*1000:.1f}ms")
        ok = (si["width"] == 1080 and si["height"] == 1920 and si["fps"] == 30
              and si["video_streams"] == 1 and si["audio_streams"] == 1
              and si["subtitle_streams"] == 0
              and abs(si["duration_s"] - expected) < 0.3
              and gap_drift < 0.020)
        print(f"expected ~{expected:.1f}s, got {si['duration_s']:.2f}s")
        print("CORE RENDER VERIFIED" if ok else "FAIL: acceptance checks not met")
        assert ok
