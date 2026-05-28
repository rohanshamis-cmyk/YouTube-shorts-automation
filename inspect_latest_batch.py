#!/usr/bin/env python3
"""Print a quick overview of the newest generated batch pack."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _get_latest_batch_dir(drafts_dir: Path) -> Path | None:
    batches = [path for path in drafts_dir.glob("batch_*") if path.is_dir()]
    if not batches:
        return None
    return max(batches, key=lambda path: path.stat().st_mtime)


def _get_topic(manifest_path: Path, summary_path: Path) -> str:
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            topic = str(data.get("topic", "")).strip()
            if topic:
                return topic
        except Exception:
            pass

    if summary_path.exists():
        for line in summary_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("- Topic:"):
                return line.split(":", 1)[1].strip()
    return ""


def _get_best_two_files(posting_plan_path: Path) -> list[str]:
    if not posting_plan_path.exists():
        return []

    best_files: list[str] = []
    in_best_section = False
    for raw_line in posting_plan_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            in_best_section = line.lower() == "## best 2 to post first"
            continue
        if not in_best_section:
            continue
        if not line:
            if best_files:
                break
            continue
        if line.startswith("- "):
            match = re.search(r"-\s*\d+\.\s+([^\s]+)", line)
            if match:
                best_files.append(match.group(1))
            else:
                best_files.append(line[2:].split(" (", 1)[0].strip())
        if len(best_files) == 2:
            break
    return best_files


def main() -> int:
    drafts_dir = Path("drafts")
    latest_batch = _get_latest_batch_dir(drafts_dir)
    if latest_batch is None:
        print("No batch folder found under drafts/", file=sys.stderr)
        return 1

    csv_candidates = sorted(latest_batch.glob("batch_dry_run_*.csv"))
    csv_path = csv_candidates[0] if csv_candidates else None
    summary_path = latest_batch / "batch_summary.md"
    posting_plan_path = latest_batch / "posting_plan.md"
    manifest_path = latest_batch / "batch_manifest.json"

    script_count = len(list(latest_batch.glob("dry_run_*.txt")))
    topic = _get_topic(manifest_path=manifest_path, summary_path=summary_path)
    best_two = _get_best_two_files(posting_plan_path=posting_plan_path)

    print(f"Newest batch folder: {latest_batch}")
    print(f"CSV path: {csv_path if csv_path else 'missing'}")
    print(f"Summary path: {summary_path}")
    print(f"Posting plan path: {posting_plan_path}")
    print(f"Manifest path: {manifest_path}")
    print(f"Total scripts: {script_count}")
    print(f"Topic: {topic or 'unknown'}")
    print(f"First 2 recommended files: {', '.join(best_two) if best_two else 'not found'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
