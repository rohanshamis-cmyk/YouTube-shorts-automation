"""Review pack exporter for story pipeline runs."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


_LOCAL_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def detect_prompt_image_source_type(value: str) -> str:
    raw = str(value or "").strip()
    lowered = raw.lower()
    if lowered.startswith("data:image/"):
        return "data_uri"
    if lowered.startswith("runway://"):
        return "runway_uri"
    if lowered.startswith("https://"):
        return "https_url"
    if lowered.startswith("http://"):
        return "unknown"

    suffix = Path(raw).suffix.lower()
    if suffix in _LOCAL_IMAGE_SUFFIXES:
        return "local_path"
    return "unknown"


def _get_value(obj: Any, key: str, default: Any = "") -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _ordered_shot_ids(*groups: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for group in groups:
        for item in group:
            shot_id = str(_get_value(item, "shot_id", "")).strip()
            if not shot_id or shot_id in seen:
                continue
            seen.add(shot_id)
            ordered.append(shot_id)
    return ordered


def build_review_pack(
    *,
    topic: str,
    script: Mapping[str, Any],
    planned_shots: list[Any],
    keyframes: list[Any],
    final_shots: list[Any],
    counts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    counts_map = dict(counts or {})

    planned_by_id = {str(_get_value(shot, "shot_id", "")).strip(): shot for shot in planned_shots}
    keyframe_by_id = {str(_get_value(keyframe, "shot_id", "")).strip(): keyframe for keyframe in keyframes}
    final_by_id = {str(_get_value(shot, "shot_id", "")).strip(): shot for shot in final_shots}

    rows: list[dict[str, Any]] = []
    for shot_id in _ordered_shot_ids(planned_shots, keyframes, final_shots):
        planned = planned_by_id.get(shot_id)
        keyframe = keyframe_by_id.get(shot_id)
        final_shot = final_by_id.get(shot_id)

        prompt_image_value = str(
            _get_value(final_shot, "prompt_image", _get_value(planned, "prompt_image", ""))
        ).strip()
        image_prompt = str(
            _get_value(keyframe, "image_prompt", _get_value(planned, "prompt_text", ""))
        ).strip()
        output_image_path = str(_get_value(keyframe, "output_image_path", "")).strip()

        rows.append(
            {
                "shot_id": shot_id,
                "beat": str(_get_value(planned, "beat", "")).strip(),
                "visual_goal": str(_get_value(planned, "visual_goal", "")).strip(),
                "on_screen_text": str(_get_value(planned, "on_screen_text", "")).strip(),
                "duration": _get_value(planned, "duration", ""),
                "image_prompt": image_prompt,
                "output_image_path": output_image_path,
                "prompt_image_source_type": detect_prompt_image_source_type(prompt_image_value),
            }
        )

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "topic": str(topic or "").strip(),
        "script": {
            "title": str(script.get("title", "")).strip(),
            "hook": str(script.get("hook", "")).strip(),
            "narration": str(script.get("narration", "")).strip(),
            "script_source_winner": str(script.get("script_source_winner", "")).strip(),
            "winner_reason": str(script.get("winner_reason", "")).strip(),
            "live_script_provider": str(script.get("live_script_provider", "")).strip(),
            "live_script_model": str(script.get("live_script_model", "")).strip(),
            "judge_model": str(script.get("judge_model", "")).strip(),
            "judge_summary": str(script.get("judge_summary", "")).strip(),
        },
        "summary": {
            "planned_shot_count": int(counts_map.get("planned_shots", len(planned_shots))),
            "real_keyframe_successes": int(counts_map.get("real_keyframe_successes", 0)),
            "fallback_keyframes": int(counts_map.get("fallback_keyframes", 0)),
            "assets_count": int(counts_map.get("assets", 0)),
            "final_runway_shots_count": int(counts_map.get("final_runway_shots", 0)),
        },
        "shots": rows,
    }


def _render_review_markdown(review_pack: Mapping[str, Any]) -> str:
    script = review_pack.get("script", {}) if isinstance(review_pack, Mapping) else {}
    summary = review_pack.get("summary", {}) if isinstance(review_pack, Mapping) else {}
    shots = review_pack.get("shots", []) if isinstance(review_pack, Mapping) else []

    lines = [
        "# Review Pack",
        "",
        f"- topic: {review_pack.get('topic', '')}",
        f"- script title: {script.get('title', '')}",
        f"- script source winner: {script.get('script_source_winner', '')}",
        f"- winner reason: {script.get('winner_reason', '')}",
        f"- live script provider: {script.get('live_script_provider', '')}",
        f"- live script model: {script.get('live_script_model', '')}",
        f"- judge model: {script.get('judge_model', '')}",
        f"- judge summary: {script.get('judge_summary', '')}",
        f"- planned shot count: {summary.get('planned_shot_count', 0)}",
        f"- real keyframe successes: {summary.get('real_keyframe_successes', 0)}",
        f"- fallback keyframes: {summary.get('fallback_keyframes', 0)}",
        f"- assets count: {summary.get('assets_count', 0)}",
        f"- final runway shots count: {summary.get('final_runway_shots_count', 0)}",
        "",
        "## Hook",
        str(script.get("hook", "")),
        "",
        "## Narration",
        str(script.get("narration", "")),
        "",
    ]

    for shot in shots if isinstance(shots, list) else []:
        if not isinstance(shot, Mapping):
            continue
        lines.extend(
            [
                f"## {shot.get('shot_id', '')}",
                f"- shot_id: {shot.get('shot_id', '')}",
                f"- beat: {shot.get('beat', '')}",
                f"- visual_goal: {shot.get('visual_goal', '')}",
                f"- on_screen_text: {shot.get('on_screen_text', '')}",
                f"- duration: {shot.get('duration', '')}",
                f"- image_prompt: {shot.get('image_prompt', '')}",
                f"- output_image_path: {shot.get('output_image_path', '')}",
                f"- prompt_image source type: {shot.get('prompt_image_source_type', 'unknown')}",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def write_review_pack(review_pack: Mapping[str, Any], output_dir: str | Path) -> dict[str, str]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = out_dir / "review_pack.md"
    json_path = out_dir / "review_pack.json"
    csv_path = out_dir / "shots_table.csv"

    markdown_path.write_text(_render_review_markdown(review_pack), encoding="utf-8")
    json_path.write_text(json.dumps(review_pack, indent=2, ensure_ascii=False), encoding="utf-8")

    fieldnames = [
        "shot_id",
        "beat",
        "visual_goal",
        "on_screen_text",
        "duration",
        "image_prompt",
        "output_image_path",
        "prompt_image_source_type",
    ]
    shots = review_pack.get("shots", []) if isinstance(review_pack, Mapping) else []
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        if isinstance(shots, list):
            for shot in shots:
                if not isinstance(shot, Mapping):
                    continue
                writer.writerow({field: shot.get(field, "") for field in fieldnames})

    return {
        "markdown_path": str(markdown_path),
        "json_path": str(json_path),
        "csv_path": str(csv_path),
    }
