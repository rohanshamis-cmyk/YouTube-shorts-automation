#!/usr/bin/env python3
"""Upload rendered batch videos to YouTube in posting order (optional scheduling)."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


@dataclass
class UploadItem:
    posting_order: int
    video_path: Path
    metadata_path: Path
    source_script_filename: str
    title: str
    caption: str
    cta: str
    hashtags: list[str]
    best_post_time_guess: str = ""
    publish_at: str = ""
    already_uploaded: bool = False
    existing_video_id: str = ""


def _find_latest_batch(drafts_dir: Path) -> Path:
    batches = [path for path in drafts_dir.glob("batch_*") if path.is_dir()]
    if not batches:
        raise FileNotFoundError("No batch folders found under drafts/")
    return max(batches, key=lambda path: path.stat().st_mtime)


def _parse_iso_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _load_manifest(batch_dir: Path) -> dict[str, Any]:
    manifest_path = batch_dir / "batch_manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _save_manifest(batch_dir: Path, manifest_data: dict[str, Any]) -> None:
    manifest_path = batch_dir / "batch_manifest.json"
    manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")


def _build_manifest_script_index(manifest_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    scripts = manifest_data.get("scripts", [])
    script_map: dict[str, dict[str, Any]] = {}
    if not isinstance(scripts, list):
        return script_map
    for script in scripts:
        if not isinstance(script, dict):
            continue
        filename = str(script.get("filename", "")).strip()
        if filename:
            script_map[filename] = script
    return script_map


def _load_manifest_start_date(batch_dir: Path) -> date | None:
    data = _load_manifest(batch_dir)
    return _parse_iso_date(str(data.get("start_date", "")).strip())


def _load_posting_plan_start_date(batch_dir: Path) -> date | None:
    posting_plan = batch_dir / "posting_plan.md"
    if not posting_plan.exists():
        return None
    try:
        text = posting_plan.read_text(encoding="utf-8")
    except Exception:
        return None

    match = re.search(r"Start date:\s*(\d{4}-\d{2}-\d{2})", text)
    if not match:
        return None
    return _parse_iso_date(match.group(1))


def _resolve_schedule_start_date(batch_dir: Path, cli_start_date: str) -> date:
    override_date = _parse_iso_date(cli_start_date.strip()) if cli_start_date else None
    if override_date:
        return override_date

    manifest_date = _load_manifest_start_date(batch_dir)
    if manifest_date:
        return manifest_date

    plan_date = _load_posting_plan_start_date(batch_dir)
    if plan_date:
        return plan_date

    return datetime.now().date()


def _fallback_order(source_script_filename: str, metadata_path: Path) -> int:
    if source_script_filename:
        m = re.search(r"_(\d+)\.txt$", source_script_filename)
        if m:
            return int(m.group(1))
    m = re.search(r"_(\d+)\.json$", metadata_path.name)
    if m:
        return int(m.group(1))
    return 999


def _coerce_hashtags(value: Any) -> list[str]:
    if isinstance(value, list):
        tags = [str(v).strip() for v in value if str(v).strip()]
    elif isinstance(value, str):
        tags = [part.strip() for part in value.split() if part.strip()]
    else:
        tags = []
    cleaned: list[str] = []
    for tag in tags:
        tag = tag.strip()
        if tag.startswith("#"):
            tag = tag[1:]
        if tag:
            cleaned.append(tag)
    return cleaned


def _load_upload_items(batch_dir: Path) -> list[UploadItem]:
    rendered_dir = batch_dir / "rendered"
    if not rendered_dir.exists():
        raise FileNotFoundError(f"Rendered folder not found: {rendered_dir}")

    manifest_data = _load_manifest(batch_dir)
    script_map = _build_manifest_script_index(manifest_data)
    items: list[UploadItem] = []

    for metadata_path in sorted(rendered_dir.glob("*.json")):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Skip metadata parse error: {metadata_path} ({exc})")
            continue

        video_path = rendered_dir / f"{metadata_path.stem}.mp4"
        if not video_path.exists():
            print(f"Skip missing video for metadata: {metadata_path}")
            continue

        source_script = str(payload.get("source_script_filename", "")).strip()
        script_meta = script_map.get(source_script, {})

        posting_order = _fallback_order(source_script, metadata_path)
        try:
            manifest_order = int(str(script_meta.get("posting_order", "")).strip())
            posting_order = manifest_order
        except Exception:
            pass

        best_post_time_guess = str(script_meta.get("best_post_time_guess", "")).strip()
        existing_video_id = str(script_meta.get("youtube_video_id", "")).strip()
        status = str(script_meta.get("youtube_upload_status", "")).strip().lower()
        uploaded_flag = bool(script_meta.get("youtube_uploaded"))
        already_uploaded = (uploaded_flag and bool(existing_video_id)) or (
            status == "uploaded" and bool(existing_video_id)
        )

        item = UploadItem(
            posting_order=posting_order,
            video_path=video_path,
            metadata_path=metadata_path,
            source_script_filename=source_script,
            title=str(payload.get("title", video_path.stem)).strip()[:100],
            caption=str(payload.get("caption", "")).strip(),
            cta=str(payload.get("cta", "")).strip(),
            hashtags=_coerce_hashtags(payload.get("hashtags", [])),
            best_post_time_guess=best_post_time_guess,
            already_uploaded=already_uploaded,
            existing_video_id=existing_video_id,
        )
        items.append(item)

    items.sort(key=lambda i: (i.posting_order, i.video_path.name))
    return items


def _parse_best_post_time_guess(value: str) -> dtime:
    if value:
        match = re.search(r"(\d{1,2}):(\d{2})", value)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return dtime(hour=hour, minute=minute)
    return dtime(hour=17, minute=0)


def _compute_publish_at_iso(start_date: date, posting_order: int, best_post_time_guess: str) -> str:
    offset_days = max(posting_order - 1, 0)
    post_date = start_date + timedelta(days=offset_days)
    post_time = _parse_best_post_time_guess(best_post_time_guess)

    local_tz = datetime.now().astimezone().tzinfo
    local_dt = datetime.combine(post_date, post_time).replace(tzinfo=local_tz)
    utc_dt = local_dt.astimezone(timezone.utc).replace(microsecond=0)
    return utc_dt.isoformat().replace("+00:00", "Z")


def _assign_schedule(items: list[UploadItem], schedule_start_date: date) -> None:
    for item in items:
        item.publish_at = _compute_publish_at_iso(
            start_date=schedule_start_date,
            posting_order=item.posting_order,
            best_post_time_guess=item.best_post_time_guess,
        )


def _build_upload_metadata(item: UploadItem, privacy_status: str) -> dict[str, Any]:
    caption = item.caption or ""
    cta = item.cta or ""
    hashtag_line = " ".join(f"#{tag}" for tag in item.hashtags[:10]) if item.hashtags else ""

    description_parts = [part for part in [caption, cta, hashtag_line] if part]
    description = "\n\n".join(description_parts) if description_parts else item.title

    payload: dict[str, Any] = {
        "title": item.title,
        "description": description,
        "tags": item.hashtags[:15],
        "categoryId": "22",
        "privacyStatus": privacy_status,
    }
    if item.publish_at:
        payload["publishAt"] = item.publish_at
    return payload


def _check_execute_config(allow_oauth_flow: bool) -> bool:
    load_dotenv()
    client_secrets = Path(os.getenv("YOUTUBE_CLIENT_SECRETS", "client_secrets.json"))
    token_file = Path("youtube_token.pickle")

    if not client_secrets.exists():
        print("Configuration missing: YouTube client secrets file was not found.")
        print(f"Expected path: {client_secrets}")
        print("Create OAuth credentials in Google Cloud and save the JSON file at that path.")
        return False

    if not token_file.exists() and not allow_oauth_flow:
        print("Configuration missing: YouTube OAuth token file was not found.")
        print(f"Expected path: {token_file}")
        print("For first-time setup, run with --allow-oauth-flow to complete browser auth once.")
        return False

    return True


def _mark_manifest_upload_result(
    manifest_data: dict[str, Any],
    script_index: dict[str, dict[str, Any]],
    source_script_filename: str,
    status: str,
    video_id: str = "",
    error_message: str = "",
) -> None:
    if not source_script_filename:
        return

    script = script_index.get(source_script_filename)
    if script is None:
        scripts = manifest_data.setdefault("scripts", [])
        if not isinstance(scripts, list):
            scripts = []
            manifest_data["scripts"] = scripts
        script = {"filename": source_script_filename}
        scripts.append(script)
        script_index[source_script_filename] = script

    script["youtube_upload_status"] = status
    script["youtube_upload_updated_at"] = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )

    if status == "uploaded":
        script["youtube_uploaded"] = True
        script["youtube_video_id"] = video_id
        script.pop("youtube_upload_error", None)
    else:
        script["youtube_uploaded"] = False
        if error_message:
            script["youtube_upload_error"] = error_message[:500]


def _upload_items(batch_dir: Path, items: list[UploadItem], privacy_status: str) -> tuple[int, int, bool]:
    load_dotenv()
    # Import lazily so dry-run mode works even if upload dependencies are unavailable.
    from uploader import YouTubeQuotaExceededError, YouTubeUploader

    config = {
        "YOUTUBE_CLIENT_SECRETS": os.getenv("YOUTUBE_CLIENT_SECRETS", "client_secrets.json"),
    }
    uploader = YouTubeUploader(config)

    manifest_data = _load_manifest(batch_dir)
    script_index = _build_manifest_script_index(manifest_data)

    success = 0
    failures = 0
    stopped_on_real_quota = False

    for idx, item in enumerate(items, start=1):
        metadata = _build_upload_metadata(item=item, privacy_status=privacy_status)
        print(
            f"[{idx}/{len(items)}] Uploading {item.video_path.name} "
            f"(order {item.posting_order}) using {item.metadata_path.name}"
        )
        if metadata.get("publishAt"):
            print(f"  schedule publishAt: {metadata['publishAt']}")

        try:
            video_id = uploader.upload(video_path=item.video_path, thumbnail_path=None, metadata=metadata)
            print(f"  SUCCESS: https://youtube.com/shorts/{video_id}")
            success += 1
            _mark_manifest_upload_result(
                manifest_data=manifest_data,
                script_index=script_index,
                source_script_filename=item.source_script_filename,
                status="uploaded",
                video_id=video_id,
            )
            _save_manifest(batch_dir, manifest_data)
        except YouTubeQuotaExceededError as exc:
            failures += 1
            stopped_on_real_quota = True
            print(f"  FAILED_REAL_QUOTA: {exc}")
            print("  Stopping remaining uploads because YouTube API reported real quota exhaustion.")
            _mark_manifest_upload_result(
                manifest_data=manifest_data,
                script_index=script_index,
                source_script_filename=item.source_script_filename,
                status="quota_exceeded",
                error_message=str(exc),
            )
            _save_manifest(batch_dir, manifest_data)
            break
        except Exception as exc:
            failures += 1
            print(f"  FAILED: {exc}")
            _mark_manifest_upload_result(
                manifest_data=manifest_data,
                script_index=script_index,
                source_script_filename=item.source_script_filename,
                status="failed",
                error_message=str(exc),
            )
            _save_manifest(batch_dir, manifest_data)

    return success, failures, stopped_on_real_quota


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload rendered batch videos in posting order.")
    parser.add_argument(
        "--batch-dir",
        type=str,
        help="Optional batch folder path. Defaults to newest drafts/batch_*",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually upload to YouTube. Default is dry/safe mode.",
    )
    parser.add_argument(
        "--privacy",
        choices=["private", "unlisted", "public"],
        default="private",
        help="Privacy status for upload mode (default: private).",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Compute publishAt values from posting order + start date + best post time.",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default="",
        help="Optional schedule start date override in YYYY-MM-DD.",
    )
    parser.add_argument(
        "--allow-oauth-flow",
        action="store_true",
        help="Allow first-time browser OAuth flow when token file is missing.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of pending videos to upload in this run.",
    )
    args = parser.parse_args()

    if args.limit < 0:
        print("--limit must be >= 0")
        return 1

    batch_dir = Path(args.batch_dir) if args.batch_dir else _find_latest_batch(Path("drafts"))
    if not batch_dir.exists() or not batch_dir.is_dir():
        print(f"Batch folder not found: {batch_dir}")
        return 1

    items = _load_upload_items(batch_dir)
    if not items:
        print(f"No rendered video + metadata pairs found in {batch_dir / 'rendered'}")
        return 1

    privacy = args.privacy
    schedule_start_date: date | None = None
    if args.schedule:
        schedule_start_date = _resolve_schedule_start_date(batch_dir, args.start_date)
        _assign_schedule(items, schedule_start_date)
        if privacy != "private":
            print("Scheduling requires private uploads. Overriding privacy to private.")
            privacy = "private"

    skipped_items = [item for item in items if item.already_uploaded]
    pending_items = [item for item in items if not item.already_uploaded]

    if args.limit > 0:
        pending_items = pending_items[: args.limit]

    print(f"Batch folder: {batch_dir}")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY/SAFE'}")
    print(f"Privacy: {privacy}")
    print(f"Scheduling: {'enabled' if args.schedule else 'disabled'}")
    if schedule_start_date:
        print(f"Schedule start date: {schedule_start_date.isoformat()}")
    print(f"Repo quota policy: API-authoritative (repo-side tracker is warning-only).")
    print(f"Total files discovered: {len(items)}")
    print(f"Already uploaded (will skip): {len(skipped_items)}")
    print(f"Selected for this run: {len(pending_items)}")

    for item in skipped_items:
        url = f"https://youtube.com/shorts/{item.existing_video_id}" if item.existing_video_id else "(video id missing)"
        print(
            f"SKIP already uploaded: {item.video_path.name} "
            f"(order {item.posting_order}) {url}"
        )

    if not pending_items:
        print("Nothing left to upload for this batch.")
        return 0

    if not args.execute:
        for idx, item in enumerate(pending_items, start=1):
            metadata = _build_upload_metadata(item=item, privacy_status=privacy)
            print(
                f"[{idx}/{len(pending_items)}] Would upload {item.video_path.name} "
                f"(order {item.posting_order}) using {item.metadata_path.name}"
            )
            print(f"  title: {metadata['title']}")
            print(f"  tags: {', '.join(metadata['tags']) if metadata['tags'] else '(none)'}")
            if metadata.get("publishAt"):
                print(f"  publishAt: {metadata['publishAt']}")
        print("Dry/safe mode complete. No uploads performed.")
        return 0

    if not _check_execute_config(allow_oauth_flow=args.allow_oauth_flow):
        return 1

    success, failures, stopped_on_real_quota = _upload_items(
        batch_dir=batch_dir,
        items=pending_items,
        privacy_status=privacy,
    )
    print(f"Upload summary: success={success}, failed={failures}")
    if stopped_on_real_quota:
        print("Summary note: stopped early because YouTube API returned a real quota-exceeded response.")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
