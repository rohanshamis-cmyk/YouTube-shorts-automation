#!/usr/bin/env python3
"""Batch runner for story pipeline topic generation."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_TOPIC_FILE = "story_topics.txt"
DEFAULT_OUTPUT_ROOT = "generated/story_batch"
DEFAULT_MEDIA_MODE = "offline_safe"


@dataclass(slots=True)
class BatchRow:
    topic: str
    status: str
    script_title: str
    media_mode: str
    planned_shots: int
    real_keyframe_successes: int
    fallback_keyframes: int
    runway_offline_fallbacks: int
    root_output_folder: str
    review_pack_markdown_path: str
    pipeline_manifest_path: str
    error: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    if not slug:
        return "story-topic"
    return slug[:max_len].strip("-") or "story-topic"


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run story pipeline for a list of topics.")
    parser.add_argument(
        "topic_file",
        nargs="?",
        default=DEFAULT_TOPIC_FILE,
        help=f"Path to topic list file (default: {DEFAULT_TOPIC_FILE})",
    )
    parser.add_argument(
        "--media-mode",
        default=DEFAULT_MEDIA_MODE,
        help=f"Media mode to pass through to run_story_pipeline.py (default: {DEFAULT_MEDIA_MODE})",
    )
    parser.add_argument(
        "--output-root",
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Batch output root (default: {DEFAULT_OUTPUT_ROOT})",
    )
    return parser.parse_args()


def _read_topics(topic_file: Path) -> list[str]:
    if not topic_file.exists():
        raise FileNotFoundError(f"Topic file not found: {topic_file}")

    topics: list[str] = []
    for raw_line in topic_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        topics.append(line)

    if not topics:
        raise ValueError(f"No topics found in {topic_file}")

    return topics


def _parse_pipeline_stdout(stdout: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip().lower()] = value.strip()
    return values


def _discover_latest_manifest(pipeline_output_root: Path, topic: str) -> Path | None:
    slug = _slugify(topic)
    candidates = sorted(
        [path for path in pipeline_output_root.glob(f"*{slug}") if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        manifest_path = candidate / "pipeline_manifest.json"
        if manifest_path.exists():
            return manifest_path
    return None


def _extract_error(stdout: str, stderr: str) -> str:
    for stream in (stderr, stdout):
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        if not lines:
            continue
        for line in reversed(lines):
            if line.lower().startswith("story pipeline failed:"):
                return line
        return lines[-1]
    return "unknown failure"


def _run_topic(topic: str, media_mode: str, pipeline_output_root: Path) -> BatchRow:
    cmd = [
        sys.executable,
        "run_story_pipeline.py",
        topic,
        "--media-mode",
        media_mode,
        "--output-root",
        str(pipeline_output_root),
    ]

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - defensive wrapper
        return BatchRow(
            topic=topic,
            status="failure",
            script_title="",
            media_mode=media_mode,
            planned_shots=0,
            real_keyframe_successes=0,
            fallback_keyframes=0,
            runway_offline_fallbacks=0,
            root_output_folder="",
            review_pack_markdown_path="",
            pipeline_manifest_path="",
            error=f"Failed to execute run_story_pipeline.py: {exc}",
        )

    parsed = _parse_pipeline_stdout(completed.stdout)
    manifest_path = parsed.get("pipeline manifest path", "").strip()
    if not manifest_path:
        discovered = _discover_latest_manifest(pipeline_output_root=pipeline_output_root, topic=topic)
        if discovered is not None:
            manifest_path = str(discovered)

    manifest_data: dict[str, Any] = {}
    manifest_file = Path(manifest_path) if manifest_path else None
    if manifest_file is not None and manifest_file.exists():
        try:
            manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception:
            manifest_data = {}

    counts = manifest_data.get("counts", {}) if isinstance(manifest_data, dict) else {}
    script_meta = manifest_data.get("script_metadata", {}) if isinstance(manifest_data, dict) else {}

    script_title = str(script_meta.get("title", "") or parsed.get("script title", "")).strip()
    planned_shots = _as_int(counts.get("planned_shots", parsed.get("planned shots", 0)))
    real_keyframe_successes = _as_int(
        counts.get("real_keyframe_successes", parsed.get("real keyframe successes", 0))
    )
    fallback_keyframes = _as_int(counts.get("fallback_keyframes", parsed.get("fallback keyframes", 0)))
    runway_offline_fallbacks = _as_int(
        counts.get("runway_offline_fallbacks_used", parsed.get("runway offline fallbacks used", 0))
    )

    root_output_folder = parsed.get("root output folder", "").strip()
    if not root_output_folder and manifest_file is not None:
        root_output_folder = str(manifest_file.parent)

    review_pack_markdown_path = parsed.get("review pack markdown path", "").strip()
    if not review_pack_markdown_path and root_output_folder:
        candidate_review_pack = Path(root_output_folder) / "review_pack" / "review_pack.md"
        if candidate_review_pack.exists():
            review_pack_markdown_path = str(candidate_review_pack)

    status = "success" if completed.returncode == 0 else "failure"
    error = "" if status == "success" else _extract_error(stdout=completed.stdout, stderr=completed.stderr)

    return BatchRow(
        topic=topic,
        status=status,
        script_title=script_title,
        media_mode=media_mode,
        planned_shots=planned_shots,
        real_keyframe_successes=real_keyframe_successes,
        fallback_keyframes=fallback_keyframes,
        runway_offline_fallbacks=runway_offline_fallbacks,
        root_output_folder=root_output_folder,
        review_pack_markdown_path=review_pack_markdown_path,
        pipeline_manifest_path=manifest_path,
        error=error,
    )


def _write_csv(rows: list[BatchRow], path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(BatchRow.__dataclass_fields__.keys())
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    return str(path)


def _escape_md(value: str) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _write_markdown(rows: list[BatchRow], path: Path, topic_file: Path, media_mode: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    total = len(rows)
    successes = sum(1 for row in rows if row.status == "success")
    failures = total - successes

    lines = [
        "# Story Batch Summary",
        "",
        f"- created_at: {_utc_now_iso()}",
        f"- topic_file: {topic_file}",
        f"- media_mode: {media_mode}",
        f"- total_topics: {total}",
        f"- successes: {successes}",
        f"- failures: {failures}",
        "",
        "| topic | status | script_title | media_mode | planned_shots | real_keyframe_successes | fallback_keyframes | runway_offline_fallbacks | root_output_folder | review_pack_markdown_path | pipeline_manifest_path | error |",
        "|---|---|---|---|---:|---:|---:|---:|---|---|---|---|",
    ]

    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_md(row.topic),
                    _escape_md(row.status),
                    _escape_md(row.script_title),
                    _escape_md(row.media_mode),
                    str(row.planned_shots),
                    str(row.real_keyframe_successes),
                    str(row.fallback_keyframes),
                    str(row.runway_offline_fallbacks),
                    _escape_md(row.root_output_folder),
                    _escape_md(row.review_pack_markdown_path),
                    _escape_md(row.pipeline_manifest_path),
                    _escape_md(row.error),
                ]
            )
            + " |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _write_manifest(
    rows: list[BatchRow],
    path: Path,
    topic_file: Path,
    media_mode: str,
    batch_output_folder: Path,
    pipeline_output_root: Path,
    summary_csv_path: str,
    summary_md_path: str,
) -> str:
    total = len(rows)
    successes = sum(1 for row in rows if row.status == "success")
    payload = {
        "created_at": _utc_now_iso(),
        "topic_file": str(topic_file),
        "media_mode": media_mode,
        "total_topics": total,
        "successes": successes,
        "failures": total - successes,
        "batch_output_folder": str(batch_output_folder),
        "pipeline_output_root": str(pipeline_output_root),
        "summary_paths": {
            "csv": summary_csv_path,
            "markdown": summary_md_path,
        },
        "rows": [asdict(row) for row in rows],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def main() -> int:
    args = _parse_args()
    topic_file = Path(str(args.topic_file))
    media_mode = str(args.media_mode).strip() or DEFAULT_MEDIA_MODE
    output_root = Path(str(args.output_root))

    try:
        topics = _read_topics(topic_file)
    except Exception as exc:
        print(f"story batch failed: {exc}", file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_output_folder = output_root / stamp
    pipeline_output_root = batch_output_folder / "story_pipeline"
    batch_output_folder.mkdir(parents=True, exist_ok=True)

    rows: list[BatchRow] = []

    for index, topic in enumerate(topics, start=1):
        print(f"[{index}/{len(topics)}] running topic: {topic}")
        row = _run_topic(topic=topic, media_mode=media_mode, pipeline_output_root=pipeline_output_root)
        rows.append(row)

    summary_csv_path = _write_csv(rows=rows, path=batch_output_folder / "batch_summary.csv")
    summary_md_path = _write_markdown(
        rows=rows,
        path=batch_output_folder / "batch_summary.md",
        topic_file=topic_file,
        media_mode=media_mode,
    )
    manifest_path = _write_manifest(
        rows=rows,
        path=batch_output_folder / "batch_manifest.json",
        topic_file=topic_file,
        media_mode=media_mode,
        batch_output_folder=batch_output_folder,
        pipeline_output_root=pipeline_output_root,
        summary_csv_path=summary_csv_path,
        summary_md_path=summary_md_path,
    )

    total = len(rows)
    successes = sum(1 for row in rows if row.status == "success")
    failures = total - successes

    print(f"total topics: {total}")
    print(f"successes: {successes}")
    print(f"failures: {failures}")
    print(f"batch output folder: {batch_output_folder}")
    print(f"summary csv path: {summary_csv_path}")
    print(f"summary md path: {summary_md_path}")
    print(f"batch manifest path: {manifest_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
