#!/usr/bin/env python3
"""Production entrypoint for topic -> script -> hybrid keyframes -> offline Runway batch."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import types
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from asset_bridge.runway_asset_bridge import RunwayAssetBridge, apply_assets_to_planned_shots
from image_planning.keyframe_builder import KeyframeBuilder
from image_providers.hybrid_keyframe_renderer import HybridKeyframeRenderer, summarize_results
from review_pack import build_review_pack, write_review_pack
from video_planning.shot_pack_builder import PlannedShot, ShotPackBuilder
from video_providers.runway_adapter import RunwayAdapter

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency fallback
    load_dotenv = None

_MEDIA_MODE_OFFLINE_SAFE = "offline_safe"
_MEDIA_MODE_REAL_IMAGES = "real_images"
_MEDIA_MODE_REAL_IMAGES_REAL_VIDEO = "real_images_real_video"
_ALLOWED_MEDIA_MODES = (
    _MEDIA_MODE_OFFLINE_SAFE,
    _MEDIA_MODE_REAL_IMAGES,
    _MEDIA_MODE_REAL_IMAGES_REAL_VIDEO,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# A Short below this many seconds is too short to perform well; manifests flag it.
SHORTS_MIN_SECONDS = 25
# Observed TTS pace from real bridge renders: ~175 wpm => ~2.9 words/sec.
_WORDS_PER_SECOND = 2.9


def _narration_floor_check(raw_script: dict[str, Any]) -> dict[str, Any]:
    """Flag a script whose narration is too short to be a viable Short.

    Prefers the script's own `estimated_duration`; if missing, derives it from
    the word count at the observed TTS pace. Returns the two manifest fields.
    """
    estimated = raw_script.get("estimated_duration")
    if not isinstance(estimated, (int, float)) or estimated <= 0:
        words = raw_script.get("words")
        word_count = words if isinstance(words, int) else len(str(raw_script.get("narration", "")).split())
        estimated = round(word_count / _WORDS_PER_SECOND) if word_count else 0
    below = estimated < SHORTS_MIN_SECONDS
    return {
        "narration_below_shorts_floor": below,
        "narration_duration_floor_reason": (
            f"estimated {int(estimated)}s < {SHORTS_MIN_SECONDS}s Shorts floor" if below else ""
        ),
    }


def _slugify(value: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    if not slug:
        return "story-topic"
    return slug[:max_len].strip("-") or "story-topic"


def _write_json(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _write_planned_shots_manifest(planned_shots: list[PlannedShot], output_dir: Path) -> str:
    manifest_path = output_dir / "planned_shots_manifest.json"
    return _write_json(
        manifest_path,
        {
            "created_at": _utc_now_iso(),
            "count": len(planned_shots),
            "planned_shots": [asdict(shot) for shot in planned_shots],
        },
    )


def _script_candidate_block(candidate: Any) -> dict[str, str]:
    if not isinstance(candidate, dict):
        return {"title": "", "hook": "", "narration": ""}
    return {
        "title": str(candidate.get("title", "") or "").strip(),
        "hook": str(candidate.get("hook", "") or "").strip(),
        "narration": str(candidate.get("narration", "") or "").strip(),
    }


def _build_script_arbitration_payload(
    *,
    topic_text: str,
    media_mode: str,
    raw_script: dict[str, Any],
    script: dict[str, Any],
) -> dict[str, Any]:
    selected_script_source = str(
        raw_script.get("selected_script_source")
        or raw_script.get("script_source_winner")
        or script.get("script_source_winner")
        or ("fallback" if media_mode == _MEDIA_MODE_OFFLINE_SAFE else "")
    ).strip()
    live_generation_mode = str(
        raw_script.get("live_generation_mode")
        or script.get("live_generation_mode")
        or ("fallback_only_offline_safe" if media_mode == _MEDIA_MODE_OFFLINE_SAFE else "")
    ).strip()
    live_candidate = _script_candidate_block(
        raw_script.get("live_rewrite_candidate") or raw_script.get("live_script_candidate")
    )
    fallback_candidate = _script_candidate_block(raw_script.get("fallback_script_candidate"))
    if not fallback_candidate.get("title") and selected_script_source == "fallback":
        fallback_candidate = {
            "title": str(script.get("title", "")).strip(),
            "hook": str(script.get("hook", "")).strip(),
            "narration": str(script.get("narration", "")).strip(),
        }

    judge_result = raw_script.get("judge_result", {})
    if not isinstance(judge_result, dict):
        judge_result = {}
    if not judge_result:
        judge_result = {
            "winner": selected_script_source,
            "winner_reason": str(raw_script.get("winner_reason", "") or script.get("winner_reason", "")).strip(),
            "judge_summary": str(raw_script.get("judge_summary", "") or script.get("judge_summary", "")).strip(),
            "scores": raw_script.get("script_arbitration_scores", {}),
            "judge_model": str(raw_script.get("judge_model", "") or script.get("judge_model", "none")).strip() or "none",
        }

    return {
        "topic": topic_text,
        "media_mode": media_mode,
        "selected_script_source": selected_script_source,
        "winner_reason": str(raw_script.get("winner_reason", "") or script.get("winner_reason", "")).strip(),
        "live_generation_mode": live_generation_mode,
        "live_rewrite_skipped": bool(
            raw_script.get("live_rewrite_skipped", script.get("live_rewrite_skipped", False))
        ),
        "live_rewrite_skip_reason": str(
            raw_script.get("live_rewrite_skip_reason", "")
            or script.get("live_rewrite_skip_reason", "")
        ).strip(),
        "live_rewrite_noop": bool(raw_script.get("live_rewrite_noop", script.get("live_rewrite_noop", False))),
        "live_rewrite_similarity_score": raw_script.get(
            "live_rewrite_similarity_score",
            script.get("live_rewrite_similarity_score"),
        ),
        "live_rewrite_noop_reason": str(
            raw_script.get("live_rewrite_noop_reason", "")
            or script.get("live_rewrite_noop_reason", "")
        ).strip(),
        "live_rewrite_anchor_coverage_score": raw_script.get(
            "live_rewrite_anchor_coverage_score",
            script.get("live_rewrite_anchor_coverage_score"),
        ),
        "live_rewrite_anchor_loss": bool(
            raw_script.get("live_rewrite_anchor_loss", script.get("live_rewrite_anchor_loss", False))
        ),
        "live_rewrite_anchor_loss_reason": str(
            raw_script.get("live_rewrite_anchor_loss_reason", "")
            or script.get("live_rewrite_anchor_loss_reason", "")
        ).strip(),
        "live_script_provider": str(raw_script.get("live_script_provider", "") or script.get("live_script_provider", "")).strip(),
        "live_script_model": str(raw_script.get("live_script_model", "") or script.get("live_script_model", "")).strip(),
        "judge_model": str(raw_script.get("judge_model", "") or script.get("judge_model", "none")).strip() or "none",
        "judge_summary": str(raw_script.get("judge_summary", "") or script.get("judge_summary", "")).strip(),
        "live_rewrite_candidate": live_candidate,
        "live_script_candidate": live_candidate,
        "fallback_script_candidate": fallback_candidate,
        "judge_result": judge_result,
    }


def _render_script_arbitration_markdown(payload: dict[str, Any]) -> str:
    live_candidate = payload.get("live_script_candidate", {}) if isinstance(payload, dict) else {}
    fallback_candidate = payload.get("fallback_script_candidate", {}) if isinstance(payload, dict) else {}
    judge_result = payload.get("judge_result", {}) if isinstance(payload, dict) else {}
    scores = judge_result.get("scores", {}) if isinstance(judge_result, dict) else {}

    lines = [
        "# Script Arbitration",
        "",
        f"- topic: {payload.get('topic', '')}",
        f"- media mode: {payload.get('media_mode', '')}",
        f"- selected winner: {payload.get('selected_script_source', '')}",
        f"- winner reason: {payload.get('winner_reason', '')}",
        f"- live generation mode: {payload.get('live_generation_mode', '')}",
        f"- live rewrite skipped: {payload.get('live_rewrite_skipped', False)}",
        f"- live rewrite skip reason: {payload.get('live_rewrite_skip_reason', '')}",
        f"- live rewrite no-op: {payload.get('live_rewrite_noop', False)}",
        f"- live rewrite similarity score: {payload.get('live_rewrite_similarity_score', '')}",
        f"- live rewrite no-op reason: {payload.get('live_rewrite_noop_reason', '')}",
        f"- live rewrite anchor coverage score: {payload.get('live_rewrite_anchor_coverage_score', '')}",
        f"- live rewrite anchor loss: {payload.get('live_rewrite_anchor_loss', False)}",
        f"- live rewrite anchor loss reason: {payload.get('live_rewrite_anchor_loss_reason', '')}",
        f"- live script provider: {payload.get('live_script_provider', '')}",
        f"- live script model: {payload.get('live_script_model', '')}",
        f"- judge model: {payload.get('judge_model', '')}",
        f"- judge summary: {payload.get('judge_summary', '')}",
        "",
        "## Live Rewrite Candidate",
        f"- title: {live_candidate.get('title', '') if isinstance(live_candidate, dict) else ''}",
        f"- hook: {live_candidate.get('hook', '') if isinstance(live_candidate, dict) else ''}",
        f"- narration: {live_candidate.get('narration', '') if isinstance(live_candidate, dict) else ''}",
        "",
        "## Fallback Candidate",
        f"- title: {fallback_candidate.get('title', '') if isinstance(fallback_candidate, dict) else ''}",
        f"- hook: {fallback_candidate.get('hook', '') if isinstance(fallback_candidate, dict) else ''}",
        f"- narration: {fallback_candidate.get('narration', '') if isinstance(fallback_candidate, dict) else ''}",
        "",
        "## Judge Result",
        f"- winner: {judge_result.get('winner', '') if isinstance(judge_result, dict) else ''}",
        f"- winner_reason: {judge_result.get('winner_reason', '') if isinstance(judge_result, dict) else ''}",
        f"- judge_summary: {judge_result.get('judge_summary', '') if isinstance(judge_result, dict) else ''}",
        "- scores:",
        "```json",
        json.dumps(scores if isinstance(scores, dict) else {}, indent=2, ensure_ascii=False),
        "```",
        "",
    ]
    return "\n".join(lines)


def _write_script_arbitration_markdown(payload: dict[str, Any], output_path: Path) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_script_arbitration_markdown(payload), encoding="utf-8")
    return str(output_path)


def _assert_runway_payloads_are_data_uris(runway_output_dir: Path) -> None:
    shots_dir = runway_output_dir / "shots"
    payload_files = sorted(shots_dir.glob("*.payload.json"))
    if not payload_files:
        raise RuntimeError(f"No Runway payload files found in {shots_dir}")

    for payload_file in payload_files:
        payload = json.loads(payload_file.read_text(encoding="utf-8"))
        prompt_image = str(payload.get("promptImage", ""))
        if not prompt_image.startswith("data:image/"):
            raise RuntimeError(
                f"Payload {payload_file.name} has non-data URI promptImage: {prompt_image[:80]}"
            )


def _load_script_generator_class() -> Any:
    try:
        from script_generator import ScriptGenerator

        return ScriptGenerator
    except ModuleNotFoundError as exc:
        if exc.name != "anthropic":
            raise

        # Keep script generation usable even when anthropic isn't installed.
        anthropic_stub = types.ModuleType("anthropic")

        class _StubMessages:
            def create(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("anthropic package is unavailable in this environment.")

        class _StubAnthropicClient:
            def __init__(self, *args: Any, **kwargs: Any):
                self.messages = _StubMessages()

        anthropic_stub.Anthropic = _StubAnthropicClient  # type: ignore[attr-defined]
        sys.modules["anthropic"] = anthropic_stub

        from script_generator import ScriptGenerator

        return ScriptGenerator


def _build_script_generator() -> Any:
    script_generator_cls = _load_script_generator_class()
    config = {
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", "").strip(),
        "SCRIPT_OPENAI_API_KEY": os.getenv("SCRIPT_OPENAI_API_KEY", "").strip(),
        "SCRIPT_OPENAI_BASE_URL": os.getenv("SCRIPT_OPENAI_BASE_URL", "").strip(),
        "SCRIPT_OPENAI_MODEL": os.getenv("SCRIPT_OPENAI_MODEL", "").strip(),
        "SCRIPT_WRITER_MODEL": os.getenv("SCRIPT_WRITER_MODEL", "").strip(),
        "SCRIPT_JUDGE_MODEL": os.getenv("SCRIPT_JUDGE_MODEL", "").strip(),
        "SCRIPT_JUDGE_ENABLED": os.getenv("SCRIPT_JUDGE_ENABLED", "").strip(),
        "SCRIPT_LIVE_REWRITE_ENABLED": os.getenv("SCRIPT_LIVE_REWRITE_ENABLED", "").strip(),
        "APPROVED_WRITER_MODELS": os.getenv("APPROVED_WRITER_MODELS", "").strip(),
        "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY", "").strip(),
        "OPENROUTER_MODEL": os.getenv("OPENROUTER_MODEL", "").strip(),
        "OPENROUTER_BASE_URL": os.getenv("OPENROUTER_BASE_URL", "").strip(),
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", "").strip(),
        "SCRIPT_ANTHROPIC_MODEL": os.getenv("SCRIPT_ANTHROPIC_MODEL", "").strip(),
        "SCRIPT_PROVIDER": os.getenv("SCRIPT_PROVIDER", "").strip(),
        "NICHE": os.getenv("CHANNEL_NICHE", os.getenv("NICHE", "ai_tools")).strip() or "ai_tools",
    }
    niche_config: dict[str, Any] = {
        "hook_templates": [],
        "keywords": [],
        "subreddits": [],
        "rss_feeds": [],
        "affiliate_programs": {},
        "cpm_estimate": 0,
    }
    return script_generator_cls(config=config, niche_config=niche_config)


def _build_topic_payload(topic_text: str) -> dict[str, str]:
    clean_topic = topic_text.strip()
    if not clean_topic:
        raise ValueError("Topic cannot be empty.")
    return {
        "title": clean_topic,
        "summary": f"Dark-curiosity mystery short concept focused on: {clean_topic}.",
    }


def _normalize_script_for_planning(raw_script: dict[str, Any], topic_text: str) -> dict[str, Any]:
    title = str(raw_script.get("title", "")).strip() or topic_text.strip()
    hook = str(raw_script.get("hook", "")).strip()
    narration = str(raw_script.get("narration", "")).strip()
    script_source_winner = str(raw_script.get("script_source_winner", "")).strip()
    if not script_source_winner and bool(raw_script.get("is_fallback")):
        script_source_winner = "fallback"

    if not hook:
        raise ValueError("Generated script is missing 'hook'.")
    if not narration:
        raise ValueError("Generated script is missing 'narration'.")

    return {
        "title": title,
        "hook": hook,
        "narration": narration,
        "series": str(raw_script.get("series", "story_pipeline")).strip() or "story_pipeline",
        "niche": str(raw_script.get("niche", "mystery")).strip() or "mystery",
        "tone": str(raw_script.get("tone", "grounded cinematic suspense")).strip()
        or "grounded cinematic suspense",
        "script_source_winner": script_source_winner,
        "selected_script_source": str(raw_script.get("selected_script_source", "")).strip()
        or script_source_winner,
        "winner_reason": str(raw_script.get("winner_reason", "")).strip(),
        "live_script_provider": str(raw_script.get("live_script_provider", "")).strip(),
        "live_script_model": str(raw_script.get("live_script_model", "")).strip(),
        "judge_model": str(raw_script.get("judge_model", "")).strip() or "none",
        "judge_summary": str(raw_script.get("judge_summary", "")).strip(),
        "live_generation_mode": str(raw_script.get("live_generation_mode", "")).strip(),
        "live_rewrite_skipped": bool(raw_script.get("live_rewrite_skipped", False)),
        "live_rewrite_skip_reason": str(raw_script.get("live_rewrite_skip_reason", "")).strip(),
        "live_rewrite_noop": bool(raw_script.get("live_rewrite_noop", False)),
        "live_rewrite_similarity_score": raw_script.get("live_rewrite_similarity_score"),
        "live_rewrite_noop_reason": str(raw_script.get("live_rewrite_noop_reason", "")).strip(),
        "live_rewrite_anchor_coverage_score": raw_script.get("live_rewrite_anchor_coverage_score"),
        "live_rewrite_anchor_loss": bool(raw_script.get("live_rewrite_anchor_loss", False)),
        "live_rewrite_anchor_loss_reason": str(raw_script.get("live_rewrite_anchor_loss_reason", "")).strip(),
    }


def _generate_script_for_mode(
    script_generator: Any,
    topic_payload: dict[str, str],
    media_mode: str,
) -> dict[str, Any]:
    if media_mode == _MEDIA_MODE_OFFLINE_SAFE:
        fallback_fn = getattr(script_generator, "_get_fallback_script", None)
        if callable(fallback_fn):
            script = fallback_fn(topic=topic_payload)
            if isinstance(script, dict):
                script.setdefault("script_source_winner", "fallback")
                script.setdefault("selected_script_source", "fallback")
                script.setdefault("winner_reason", "offline_safe mode uses fallback-only script path.")
                script.setdefault("judge_model", "none")
                script.setdefault("judge_summary", "offline_safe mode bypasses live script arbitration.")
                script.setdefault("live_script_provider", "")
                script.setdefault("live_script_model", "")
                script.setdefault("live_generation_mode", "fallback_only_offline_safe")
                script.setdefault("live_rewrite_skipped", False)
                script.setdefault("live_rewrite_skip_reason", "")
                script.setdefault("live_rewrite_noop", False)
                script.setdefault("live_rewrite_similarity_score", None)
                script.setdefault("live_rewrite_noop_reason", "")
                script.setdefault("live_rewrite_anchor_coverage_score", None)
                script.setdefault("live_rewrite_anchor_loss", False)
                script.setdefault("live_rewrite_anchor_loss_reason", "")
                script.setdefault(
                    "live_script_candidate",
                    {"title": "", "hook": "", "narration": ""},
                )
                script.setdefault(
                    "live_rewrite_candidate",
                    {"title": "", "hook": "", "narration": ""},
                )
                script.setdefault(
                    "fallback_script_candidate",
                    {
                        "title": str(script.get("title", "")).strip(),
                        "hook": str(script.get("hook", "")).strip(),
                        "narration": str(script.get("narration", "")).strip(),
                    },
                )
                script.setdefault(
                    "judge_result",
                    {
                        "winner": "fallback",
                        "winner_reason": "offline_safe mode uses fallback-only script path.",
                        "judge_summary": "offline_safe mode bypasses live script arbitration.",
                        "scores": {},
                        "judge_model": "none",
                    },
                )
            return script
    return script_generator.generate(topic_payload)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full story pipeline for one topic.")
    parser.add_argument("topic", help="Topic string for script generation")
    parser.add_argument(
        "--output-root",
        default="generated/story_pipeline",
        help="Root folder for generated artifacts (default: generated/story_pipeline)",
    )
    parser.add_argument(
        "--media-mode",
        default=_MEDIA_MODE_OFFLINE_SAFE,
        choices=_ALLOWED_MEDIA_MODES,
        help=(
            "Media provider mode: offline_safe (fallback-first, Runway offline), "
            "real_images (real keyframes + Runway offline), "
            "real_images_real_video (real keyframes + Runway auto/live-ready)."
        ),
    )
    return parser.parse_args()


def _resolve_media_mode_settings(media_mode: str) -> tuple[str, bool, str]:
    mode = str(media_mode or _MEDIA_MODE_OFFLINE_SAFE).strip().lower() or _MEDIA_MODE_OFFLINE_SAFE
    if mode not in _ALLOWED_MEDIA_MODES:
        raise ValueError(
            f"Invalid media mode '{mode}'. Allowed: {', '.join(_ALLOWED_MEDIA_MODES)}."
        )

    allow_live_images = mode in {_MEDIA_MODE_REAL_IMAGES, _MEDIA_MODE_REAL_IMAGES_REAL_VIDEO}
    runway_mode = "auto" if mode == _MEDIA_MODE_REAL_IMAGES_REAL_VIDEO else "offline"
    return mode, allow_live_images, runway_mode


def main() -> int:
    if load_dotenv is not None:
        load_dotenv()

    args = _parse_args()
    topic_text = str(args.topic).strip()
    output_root = Path(str(args.output_root))
    media_mode, allow_live_images, runway_mode = _resolve_media_mode_settings(
        str(args.media_mode)
    )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slugify(topic_text)
    root_dir = output_root / f"{stamp}_{slug}"
    script_dir = root_dir / "script"
    planned_dir = root_dir / "planned_shots"
    keyframes_dir = root_dir / "keyframes"
    assets_dir = root_dir / "asset_bridge"
    runway_dir = root_dir / "runway_batch"

    try:
        topic_payload = _build_topic_payload(topic_text)
        script_generator = _build_script_generator()
        raw_script = _generate_script_for_mode(
            script_generator=script_generator,
            topic_payload=topic_payload,
            media_mode=media_mode,
        )
        script = _normalize_script_for_planning(raw_script, topic_text=topic_text)
        script_arbitration_payload = _build_script_arbitration_payload(
            topic_text=topic_text,
            media_mode=media_mode,
            raw_script=raw_script,
            script=script,
        )
        script_arbitration_markdown_path = _write_script_arbitration_markdown(
            script_arbitration_payload,
            root_dir / "review_pack" / "script_arbitration.md",
        )

        script_manifest_path = _write_json(
            script_dir / "script_manifest.json",
            {
                "created_at": _utc_now_iso(),
                "topic": topic_text,
                "topic_payload": topic_payload,
                "script": script,
                "raw_script_fields": {
                    "estimated_duration": raw_script.get("estimated_duration"),
                    "words": raw_script.get("words"),
                    **_narration_floor_check(raw_script),
                    "cta": raw_script.get("cta"),
                    "script_source_winner": raw_script.get("script_source_winner"),
                    "winner_reason": raw_script.get("winner_reason"),
                    "live_script_provider": raw_script.get("live_script_provider"),
                    "live_script_model": raw_script.get("live_script_model"),
                    "judge_model": raw_script.get("judge_model"),
                    "judge_summary": raw_script.get("judge_summary"),
                    "live_generation_mode": raw_script.get("live_generation_mode"),
                    "live_rewrite_skipped": raw_script.get("live_rewrite_skipped"),
                    "live_rewrite_skip_reason": raw_script.get("live_rewrite_skip_reason"),
                    "live_rewrite_noop": raw_script.get("live_rewrite_noop"),
                    "live_rewrite_similarity_score": raw_script.get("live_rewrite_similarity_score"),
                    "live_rewrite_noop_reason": raw_script.get("live_rewrite_noop_reason"),
                    "live_rewrite_anchor_coverage_score": raw_script.get("live_rewrite_anchor_coverage_score"),
                    "live_rewrite_anchor_loss": raw_script.get("live_rewrite_anchor_loss"),
                    "live_rewrite_anchor_loss_reason": raw_script.get("live_rewrite_anchor_loss_reason"),
                    "script_arbitration_scores": raw_script.get("script_arbitration_scores"),
                    "selected_script_source": raw_script.get("selected_script_source")
                    or script.get("selected_script_source"),
                    "live_rewrite_candidate": raw_script.get("live_rewrite_candidate"),
                    "live_script_candidate": raw_script.get("live_script_candidate"),
                    "fallback_script_candidate": raw_script.get("fallback_script_candidate"),
                    "judge_result": raw_script.get("judge_result"),
                },
            },
        )

        shot_builder = ShotPackBuilder(ratio="720:1280", duration=4)
        planned_shots = shot_builder.build_from_script(script)
        planned_shots_manifest_path = _write_planned_shots_manifest(planned_shots, planned_dir)

        keyframe_builder = KeyframeBuilder(output_root=str(keyframes_dir))
        keyframes = keyframe_builder.build_from_planned_shots(planned_shots)
        keyframe_manifest_path = keyframe_builder.write_manifest(keyframes, keyframes_dir)

        hybrid_renderer = HybridKeyframeRenderer(
            prefer_real=allow_live_images,
            output_root=str(keyframes_dir),
        )
        hybrid_results = hybrid_renderer.render_keyframes(keyframes)
        hybrid_summary = summarize_results(hybrid_results)
        hybrid_keyframe_manifest_path = hybrid_renderer.write_manifest(hybrid_results, output_dir=keyframes_dir)

        real_keyframe_successes = hybrid_summary["real_success_count"]
        fallback_keyframes = hybrid_summary["fallback_count"]
        hard_keyframe_failures = hybrid_summary["hard_fail_count"]
        if hard_keyframe_failures > 0:
            raise RuntimeError(
                "Hard keyframe failures encountered. "
                f"real={real_keyframe_successes}, fallback={fallback_keyframes}, hard_fail={hard_keyframe_failures}"
            )

        missing_keyframes = [
            keyframe.output_image_path
            for keyframe in keyframes
            if not Path(keyframe.output_image_path).exists()
        ]
        if missing_keyframes:
            raise RuntimeError(
                "Some keyframe files are missing after hybrid render: "
                + ", ".join(missing_keyframes[:3])
            )

        planned_with_keyframes = keyframe_builder.attach_keyframes_to_shots(planned_shots, keyframes)

        bridge = RunwayAssetBridge(mode="data_uri", output_root=str(assets_dir))
        asset_inputs = [(shot.shot_id, shot.prompt_image) for shot in planned_with_keyframes]
        assets = bridge.build_assets(asset_inputs)
        asset_manifest_path = bridge.write_manifest(assets, assets_dir)

        planned_with_data_uri = apply_assets_to_planned_shots(planned_with_keyframes, assets)
        runway_shots = shot_builder.to_runway_shots(planned_with_data_uri)

        adapter = RunwayAdapter(mode=runway_mode, output_root=str(runway_dir))
        runway_results = adapter.render_batch(runway_shots)
        runway_manifest_path = adapter.last_manifest_path or str(runway_dir / "batch_manifest.json")

        runway_mocked = sum(1 for result in runway_results if result.status == "mocked")
        runway_submitted = sum(1 for result in runway_results if result.status == "submitted")
        runway_errors = [result for result in runway_results if result.status == "error"]
        runway_live_successes = runway_submitted if runway_mode == "auto" else 0
        runway_live_failures = len(runway_errors) if runway_mode == "auto" else 0
        runway_offline_fallbacks_used = 0

        if runway_errors and runway_mode == "auto":
            error_shot_ids = {result.shot_id for result in runway_errors}
            fallback_shots = [shot for shot in runway_shots if shot.shot_id in error_shot_ids]
            fallback_adapter = RunwayAdapter(mode="offline", output_root=str(runway_dir))
            fallback_results = fallback_adapter.render_batch(fallback_shots)
            fallback_errors = [result for result in fallback_results if result.status == "error"]
            if fallback_errors:
                raise RuntimeError(
                    "Runway live failed and offline fallback also failed: "
                    + "; ".join(
                        f"{result.shot_id}: {result.error or 'unknown runway error'}"
                        for result in fallback_errors[:3]
                    )
                )

            fallback_by_id = {result.shot_id: result for result in fallback_results}
            combined_results = []
            for result in runway_results:
                if result.shot_id in fallback_by_id:
                    combined_results.append(fallback_by_id[result.shot_id])
                else:
                    combined_results.append(result)
            runway_results = combined_results
            runway_offline_fallbacks_used = len(fallback_results)

            # Preserve one manifest representing the final effective batch state.
            combined_manifest_path = adapter._write_batch_manifest(shots=runway_shots, results=runway_results)
            adapter.last_manifest_path = str(combined_manifest_path)
            runway_manifest_path = str(combined_manifest_path)
        elif runway_errors:
            raise RuntimeError(
                "Runway render failures encountered: "
                + "; ".join(
                    f"{result.shot_id}: {result.error or 'unknown runway error'}"
                    for result in runway_errors[:3]
                )
            )

        runway_mocked = sum(1 for result in runway_results if result.status == "mocked")
        runway_submitted = sum(1 for result in runway_results if result.status == "submitted")
        if runway_mode != "auto":
            runway_offline_fallbacks_used = runway_mocked
        if runway_mode == "offline" and runway_mocked != len(runway_shots):
            raise RuntimeError(
                f"Offline render mismatch: expected {len(runway_shots)} mocked results, got {runway_mocked}."
            )
        final_runway_shots = runway_mocked + runway_submitted
        if final_runway_shots != len(runway_shots):
            raise RuntimeError(
                "Runway render mismatch: expected "
                f"{len(runway_shots)} results, got mocked={runway_mocked}, submitted={runway_submitted}."
            )

        _assert_runway_payloads_are_data_uris(runway_dir)

        counts_payload = {
            "planned_shots": len(planned_shots),
            "keyframes": len(keyframes),
            "real_keyframe_successes": real_keyframe_successes,
            "fallback_keyframes": fallback_keyframes,
            "hard_keyframe_failures": hard_keyframe_failures,
            "assets": len(assets),
            "runway_live_successes": runway_live_successes,
            "runway_live_failures": runway_live_failures,
            "runway_offline_fallbacks_used": runway_offline_fallbacks_used,
            "runway_mocked_shots": runway_mocked,
            "runway_submitted_shots": runway_submitted,
            "final_runway_shots": final_runway_shots,
        }

        pipeline_manifest_path = _write_json(
            root_dir / "pipeline_manifest.json",
            {
                "created_at": _utc_now_iso(),
                "topic": topic_text,
                "script_metadata": {
                    "title": script.get("title", ""),
                    "hook": script.get("hook", ""),
                    "narration_chars": len(str(script.get("narration", ""))),
                    "estimated_duration": raw_script.get("estimated_duration"),
                    "words": raw_script.get("words"),
                    **_narration_floor_check(raw_script),
                    "script_source_winner": raw_script.get("script_source_winner")
                    or script.get("script_source_winner", ""),
                    "winner_reason": raw_script.get("winner_reason")
                    or script.get("winner_reason", ""),
                    "live_script_provider": raw_script.get("live_script_provider")
                    or script.get("live_script_provider", ""),
                    "live_script_model": raw_script.get("live_script_model")
                    or script.get("live_script_model", ""),
                    "live_generation_mode": raw_script.get("live_generation_mode")
                    or script.get("live_generation_mode", ""),
                    "live_rewrite_skipped": raw_script.get("live_rewrite_skipped")
                    if raw_script.get("live_rewrite_skipped") is not None
                    else script.get("live_rewrite_skipped", False),
                    "live_rewrite_skip_reason": raw_script.get("live_rewrite_skip_reason")
                    or script.get("live_rewrite_skip_reason", ""),
                    "live_rewrite_noop": raw_script.get("live_rewrite_noop")
                    if raw_script.get("live_rewrite_noop") is not None
                    else script.get("live_rewrite_noop", False),
                    "live_rewrite_similarity_score": raw_script.get("live_rewrite_similarity_score")
                    if raw_script.get("live_rewrite_similarity_score") is not None
                    else script.get("live_rewrite_similarity_score"),
                    "live_rewrite_noop_reason": raw_script.get("live_rewrite_noop_reason")
                    or script.get("live_rewrite_noop_reason", ""),
                    "live_rewrite_anchor_coverage_score": raw_script.get("live_rewrite_anchor_coverage_score")
                    if raw_script.get("live_rewrite_anchor_coverage_score") is not None
                    else script.get("live_rewrite_anchor_coverage_score"),
                    "live_rewrite_anchor_loss": raw_script.get("live_rewrite_anchor_loss")
                    if raw_script.get("live_rewrite_anchor_loss") is not None
                    else script.get("live_rewrite_anchor_loss", False),
                    "live_rewrite_anchor_loss_reason": raw_script.get("live_rewrite_anchor_loss_reason")
                    or script.get("live_rewrite_anchor_loss_reason", ""),
                    "judge_model": raw_script.get("judge_model") or script.get("judge_model", ""),
                    "judge_summary": raw_script.get("judge_summary")
                    or script.get("judge_summary", ""),
                },
                "script_arbitration": script_arbitration_payload,
                "stage_manifests": {
                    "script_manifest": script_manifest_path,
                    "script_arbitration_markdown": script_arbitration_markdown_path,
                    "planned_shots_manifest": planned_shots_manifest_path,
                    "keyframe_manifest": keyframe_manifest_path,
                    "hybrid_keyframe_manifest": hybrid_keyframe_manifest_path,
                    "asset_manifest": asset_manifest_path,
                    "runway_manifest": runway_manifest_path,
                },
                "counts": counts_payload,
                "settings": {
                    "media_mode": media_mode,
                    "allow_live_images": allow_live_images,
                    "runway_mode": runway_mode,
                    "asset_bridge_mode": "data_uri",
                },
            },
        )

        review_pack_payload = build_review_pack(
            topic=topic_text,
            script=script,
            planned_shots=planned_shots,
            keyframes=keyframes,
            final_shots=planned_with_data_uri,
            counts=counts_payload,
        )
        review_pack_paths = write_review_pack(review_pack_payload, root_dir / "review_pack")

    except Exception as exc:
        print(f"story pipeline failed: {exc}", file=sys.stderr)
        return 1

    print(f"topic: {topic_text}")
    print(f"media mode: {media_mode}")
    print(f"script title: {script.get('title', '')}")
    print(f"script source winner: {raw_script.get('script_source_winner', script.get('script_source_winner', ''))}")
    print(f"winner reason: {raw_script.get('winner_reason', script.get('winner_reason', ''))}")
    print(f"live script provider: {raw_script.get('live_script_provider', script.get('live_script_provider', ''))}")
    print(f"live script model: {raw_script.get('live_script_model', script.get('live_script_model', ''))}")
    print(f"live generation mode: {raw_script.get('live_generation_mode', script.get('live_generation_mode', ''))}")
    print(f"live rewrite skipped: {raw_script.get('live_rewrite_skipped', script.get('live_rewrite_skipped', False))}")
    print(f"live rewrite skip reason: {raw_script.get('live_rewrite_skip_reason', script.get('live_rewrite_skip_reason', ''))}")
    print(f"live rewrite no-op: {raw_script.get('live_rewrite_noop', script.get('live_rewrite_noop', False))}")
    print(f"live rewrite no-op reason: {raw_script.get('live_rewrite_noop_reason', script.get('live_rewrite_noop_reason', ''))}")
    print(f"judge model: {raw_script.get('judge_model', script.get('judge_model', ''))}")
    print(f"judge summary: {raw_script.get('judge_summary', script.get('judge_summary', ''))}")
    print(f"planned shots: {len(planned_shots)}")
    print(f"real keyframe successes: {real_keyframe_successes}")
    print(f"fallback keyframes: {fallback_keyframes}")
    print(f"assets: {len(assets)}")
    print(f"runway live successes: {runway_live_successes}")
    print(f"runway live failures: {runway_live_failures}")
    print(f"runway offline fallbacks used: {runway_offline_fallbacks_used}")
    print(f"final runway shots: {final_runway_shots}")
    print(f"root output folder: {root_dir}")
    print(f"pipeline manifest path: {pipeline_manifest_path}")
    print(f"review pack markdown path: {review_pack_paths.get('markdown_path', '')}")
    print(f"review pack json path: {review_pack_paths.get('json_path', '')}")
    print(f"review pack csv path: {review_pack_paths.get('csv_path', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
