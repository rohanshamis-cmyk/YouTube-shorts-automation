#!/usr/bin/env python3
"""
Run `main.py --dry-run` multiple times and save each draft with a unique name.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ANGLE_SEQUENCE = [
    "beginner tip",
    "mistake to avoid",
    "tool recommendation",
    "quick workflow",
    "time-saving trick",
]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] if slug else "script"


def _clean_list_line(value: str) -> str:
    return re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", value.strip())


def _parse_draft_fields(draft_path: Path) -> dict[str, object]:
    fields = {
        "title": "",
        "topic": "",
        "hook": "",
        "on_screen_hook": "",
        "video_title": "",
        "broll_idea": "",
        "posting_order": "",
        "best_post_time_guess": "",
        "target_audience": "",
        "thumbnail_text": "",
        "caption": "",
        "cta": "",
        "angle": "",
        "shot_list": [],
        "edit_notes": [],
        "caption_lines": [],
        "top_shot": "",
        "edit_style": "",
        "first_caption_line": "",
    }
    scalar_map = {
        "TITLE": "title",
        "TOPIC": "topic",
        "ANGLE": "angle",
        "HOOK": "hook",
        "ON_SCREEN_HOOK": "on_screen_hook",
        "VIDEO_TITLE": "video_title",
        "BROLL_IDEA": "broll_idea",
        "POSTING_ORDER": "posting_order",
        "BEST_POST_TIME_GUESS": "best_post_time_guess",
        "TARGET_AUDIENCE": "target_audience",
        "THUMBNAIL_TEXT": "thumbnail_text",
        "CAPTION": "caption",
        "CTA": "cta",
    }
    list_map = {
        "SHOT_LIST": "shot_list",
        "EDIT_NOTES": "edit_notes",
        "CAPTION_LINES": "caption_lines",
    }

    active_list_key = ""
    for raw_line in draft_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        header_match = re.match(r"^([A-Z_]+):\s*(.*)$", stripped)
        if header_match:
            header = header_match.group(1)
            value = header_match.group(2).strip()
            active_list_key = ""

            if header in scalar_map:
                fields[scalar_map[header]] = value
                continue
            if header in list_map:
                active_list_key = list_map[header]
                if value:
                    cleaned_value = _clean_list_line(value)
                    if cleaned_value:
                        fields[active_list_key].append(cleaned_value)
                continue
            continue

        if active_list_key:
            cleaned = _clean_list_line(stripped)
            if cleaned:
                fields[active_list_key].append(cleaned)

    if not fields["shot_list"] and fields["broll_idea"]:
        fields["shot_list"] = [fields["broll_idea"]]
    if not fields["caption_lines"]:
        fallback_caption = fields["hook"] or fields["caption"]
        if fallback_caption:
            fields["caption_lines"] = [str(fallback_caption)]
    if not fields["edit_notes"]:
        fields["edit_notes"] = ["use fast cuts and bold first hook text"]

    fields["top_shot"] = fields["shot_list"][0] if fields["shot_list"] else ""
    fields["edit_style"] = fields["edit_notes"][0] if fields["edit_notes"] else ""
    fields["first_caption_line"] = (
        fields["caption_lines"][0] if fields["caption_lines"] else ""
    )
    return fields


def _write_angle_to_script(script_file: Path, angle: str, base_topic: str | None, posting_order: int) -> None:
    lines = script_file.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []

    for line in lines:
        if base_topic and line.startswith("TOPIC:"):
            updated.append(f"TOPIC: {base_topic}")
            continue
        if line.startswith("ANGLE:") or line.startswith("POSTING_ORDER:"):
            continue
        updated.append(line)

    topic_index = -1
    for idx, line in enumerate(updated):
        if line.startswith("TOPIC:"):
            topic_index = idx
            break

    if topic_index == -1:
        if base_topic:
            updated.insert(0, f"TOPIC: {base_topic}")
        updated.insert(1 if base_topic else 0, f"ANGLE: {angle}")
        updated.insert(2 if base_topic else 1, f"POSTING_ORDER: {posting_order}")
    else:
        updated.insert(topic_index + 1, f"ANGLE: {angle}")
        updated.insert(topic_index + 2, f"POSTING_ORDER: {posting_order}")

    script_file.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _write_batch_summary(batch_dir: Path, rows: list[dict[str, object]], topic: str | None) -> Path:
    summary_path = batch_dir / "batch_summary.md"
    lines = [
        "# Batch Summary",
        "",
        f"- Topic: {topic or 'auto-discovered'}",
        f"- Total scripts: {len(rows)}",
        "",
    ]
    for row in rows:
        posting_order = str(row.get("posting_order", ""))
        lines.extend([
            f"## {posting_order}. {row['filename']}",
            f"- angle: {row['angle']}",
            f"- title: {row['title']}",
            f"- hook: {row['hook']}",
            f"- caption: {row['caption']}",
            f"- CTA: {row['cta']}",
            f"- video title: {row['video_title']}",
            f"- thumbnail text: {row['thumbnail_text']}",
            f"- b-roll idea: {row['broll_idea']}",
            f"- best post time guess: {row['best_post_time_guess']}",
            f"- top shot: {row['top_shot']}",
            f"- editing recommendation: {row['edit_style']}",
            "",
        ])
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    return summary_path


def _recommendation_reason(angle: str) -> str:
    reasons = {
        "beginner tip": "broad entry point for new viewers.",
        "mistake to avoid": "warning-style hooks can increase comments.",
        "tool recommendation": "clear utility usually boosts saves and shares.",
        "quick workflow": "step-by-step structure supports strong retention.",
        "time-saving trick": "time-saving promise typically improves click-through.",
    }
    return reasons.get(angle, "clear practical value for Shorts viewers.")


def _extract_post_time_window(value: str) -> str:
    if not value:
        return "17:00 local"
    first = value.split("-", 1)[0].strip()
    return f"{first} local" if first else "17:00 local"


def _write_posting_plan(
    batch_dir: Path,
    rows: list[dict[str, object]],
    topic: str | None,
    start_date: date | None = None,
) -> Path:
    plan_path = batch_dir / "posting_plan.md"
    ordered_rows = sorted(
        rows,
        key=lambda row: int(str(row.get("posting_order", "999")) or "999"),
    )

    schedule_rows = ordered_rows[:5]
    schedule_lines: list[str] = []
    schedule_start = start_date or datetime.now().date()
    for i, row in enumerate(schedule_rows):
        scheduled_day = schedule_start + timedelta(days=i)
        post_time = _extract_post_time_window(str(row.get("best_post_time_guess", "")))
        reason = _recommendation_reason(str(row.get("angle", "")))
        schedule_lines.append(
            f"{i + 1}. {scheduled_day.isoformat()} — {row['filename']} at {post_time} ({reason})"
        )

    priority_map = {
        "tool recommendation": 1,
        "quick workflow": 2,
        "beginner tip": 3,
        "time-saving trick": 4,
        "mistake to avoid": 5,
    }
    best_two = sorted(
        ordered_rows,
        key=lambda row: priority_map.get(str(row.get("angle", "")), 99),
    )[:2]

    lines = [
        "# Posting Plan",
        "",
        f"- Topic: {topic or 'auto-discovered'}",
        f"- Total scripts: {len(rows)}",
        f"- Start date: {schedule_start.isoformat()}",
        "",
        "## Recommended posting order",
    ]
    for row in ordered_rows:
        reason = _recommendation_reason(str(row.get("angle", "")))
        lines.append(
            f"- {row['posting_order']}. {row['filename']} ({row['angle']}) — {reason}"
        )

    lines.extend([
        "",
        "## Suggested 5-day schedule",
    ])
    lines.extend([f"- {line}" for line in schedule_lines])

    lines.extend([
        "",
        "## Best 2 to post first",
    ])
    for i, row in enumerate(best_two, start=1):
        reason = _recommendation_reason(str(row.get("angle", "")))
        lines.append(f"- {i}. {row['filename']} ({row['angle']}) — {reason}")

    plan_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return plan_path


def _write_batch_manifest(
    batch_dir: Path,
    topic: str | None,
    csv_file: Path,
    summary_file: Path,
    posting_plan_file: Path,
    rows: list[dict[str, object]],
    start_date: date | None = None,
) -> Path:
    manifest_path = batch_dir / "batch_manifest.json"
    manifest = {
        "topic": topic or (str(rows[0].get("topic", "")) if rows else ""),
        "batch_folder_name": batch_dir.name,
        "start_date": start_date.isoformat() if start_date else "",
        "csv_path": str(csv_file),
        "summary_path": str(summary_file),
        "posting_plan_path": str(posting_plan_file),
        "scripts": [
            {
                "filename": str(row.get("filename", "")),
                "title": str(row.get("title", "")),
                "topic": str(row.get("topic", "")),
                "angle": str(row.get("angle", "")),
                "hook": str(row.get("hook", "")),
                "on_screen_hook": str(row.get("on_screen_hook", "")),
                "video_title": str(row.get("video_title", "")),
                "broll_idea": str(row.get("broll_idea", "")),
                "posting_order": str(row.get("posting_order", "")),
                "best_post_time_guess": str(row.get("best_post_time_guess", "")),
                "target_audience": str(row.get("target_audience", "")),
                "thumbnail_text": str(row.get("thumbnail_text", "")),
                "caption": str(row.get("caption", "")),
                "cta": str(row.get("cta", "")),
                "top_shot": str(row.get("top_shot", "")),
                "edit_style": str(row.get("edit_style", "")),
                "first_caption_line": str(row.get("first_caption_line", "")),
                "shot_list": list(row.get("shot_list", [])),
                "edit_notes": list(row.get("edit_notes", [])),
                "caption_lines": list(row.get("caption_lines", [])),
            }
            for row in rows
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def run_batch(
    count: int,
    topic: str | None = None,
    start_date: date | None = None,
) -> tuple[list[Path], Path]:
    drafts_dir = Path("drafts")
    drafts_dir.mkdir(parents=True, exist_ok=True)
    latest_file = drafts_dir / "latest_dry_run_script.txt"

    created_files: list[Path] = []
    rows: list[dict[str, object]] = []
    python_exe = sys.executable
    batch_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_slug = _slugify(topic or "auto-topic")
    batch_dir = drafts_dir / f"batch_{batch_stamp}_{batch_slug}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    for i in range(1, count + 1):
        print(f"Running dry-run {i}/{count}...")
        angle = ANGLE_SEQUENCE[(i - 1) % len(ANGLE_SEQUENCE)]
        topic_with_angle = f"{topic} ({angle})" if topic else None
        command = [python_exe, "main.py", "--dry-run", "--batch-position", str(i)]
        if topic_with_angle:
            command.extend(["--topic", topic_with_angle])
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"dry-run failed on iteration {i} (exit {result.returncode})")
        if not latest_file.exists():
            raise FileNotFoundError(f"Expected output missing: {latest_file}")

        latest_fields = _parse_draft_fields(latest_file)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        slug_source = (
            topic
            or str(latest_fields.get("topic", ""))
            or str(latest_fields.get("title", ""))
            or "script"
        )
        output_file = batch_dir / f"dry_run_{_slugify(slug_source)}_{timestamp}_{i}.txt"
        shutil.copy2(latest_file, output_file)
        _write_angle_to_script(output_file, angle=angle, base_topic=topic, posting_order=i)
        fields = _parse_draft_fields(output_file)
        created_files.append(output_file)
        rows.append({
            "filename": output_file.name,
            "title": str(fields.get("title", "")),
            "topic": topic or str(fields.get("topic", "")),
            "angle": angle,
            "hook": str(fields.get("hook", "")),
            "on_screen_hook": str(fields.get("on_screen_hook", "")),
            "video_title": str(fields.get("video_title", "")),
            "broll_idea": str(fields.get("broll_idea", "")),
            "posting_order": str(fields.get("posting_order", "")) or str(i),
            "best_post_time_guess": str(fields.get("best_post_time_guess", "")),
            "target_audience": str(fields.get("target_audience", "")),
            "thumbnail_text": str(fields.get("thumbnail_text", "")),
            "caption": str(fields.get("caption", "")),
            "cta": str(fields.get("cta", "")),
            "top_shot": str(fields.get("top_shot", "")),
            "edit_style": str(fields.get("edit_style", "")),
            "first_caption_line": str(fields.get("first_caption_line", "")),
            "shot_list": list(fields.get("shot_list", [])),
            "edit_notes": list(fields.get("edit_notes", [])),
            "caption_lines": list(fields.get("caption_lines", [])),
        })

    csv_file = batch_dir / f"batch_dry_run_{batch_stamp}.csv"
    csv_fieldnames = [
        "filename",
        "title",
        "topic",
        "angle",
        "hook",
        "on_screen_hook",
        "video_title",
        "broll_idea",
        "posting_order",
        "best_post_time_guess",
        "target_audience",
        "thumbnail_text",
        "top_shot",
        "edit_style",
        "first_caption_line",
        "caption",
        "cta",
    ]

    with csv_file.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fieldnames)
        writer.writeheader()
        writer.writerows([{k: row.get(k, "") for k in csv_fieldnames} for row in rows])

    summary_file = _write_batch_summary(batch_dir=batch_dir, rows=rows, topic=topic)
    posting_plan_file = _write_posting_plan(
        batch_dir=batch_dir,
        rows=rows,
        topic=topic,
        start_date=start_date,
    )
    _write_batch_manifest(
        batch_dir=batch_dir,
        topic=topic,
        csv_file=csv_file,
        summary_file=summary_file,
        posting_plan_file=posting_plan_file,
        rows=rows,
        start_date=start_date,
    )

    return created_files, csv_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate multiple dry-run scripts.")
    parser.add_argument("count", type=int, help="Number of dry-runs to execute")
    parser.add_argument("--topic", type=str, help="Custom topic to use for all dry-runs")
    parser.add_argument(
        "--start-date",
        type=str,
        help="Optional posting plan start date in YYYY-MM-DD format",
    )
    args = parser.parse_args()

    if args.count < 1:
        print("count must be >= 1", file=sys.stderr)
        return 1

    start_date: date | None = None
    if args.start_date:
        try:
            start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
        except ValueError:
            print("--start-date must be in YYYY-MM-DD format", file=sys.stderr)
            return 1

    created, csv_file = run_batch(args.count, topic=args.topic, start_date=start_date)
    print("\nCreated files:")
    for path in created:
        print(path)
    print(f"\nBatch CSV: {csv_file}")
    print(f"Batch summary: {csv_file.parent / 'batch_summary.md'}")
    print(f"Posting plan: {csv_file.parent / 'posting_plan.md'}")
    print(f"Batch manifest: {csv_file.parent / 'batch_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
