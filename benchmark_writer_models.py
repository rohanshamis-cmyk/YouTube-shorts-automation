#!/usr/bin/env python3
"""Benchmark OpenRouter writer models against fixed suspense topics."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_TOPICS = [
    "the note under my door",
    "what happened after the lights went out",
    "someone called my old nickname at midnight",
    "the mirror in the stairwell",
]

# OpenRouter model slugs. `anthropic/claude-3.5-sonnet` was retired by
# OpenRouter (HTTP 404 "No endpoints found") - replaced with the current
# Sonnet. Keep these slugs current; a dead slug silently fails the live
# path and the run is scored as a fallback win.
DEFAULT_MODELS = [
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "anthropic/claude-sonnet-4.6",
]


@dataclass
class RunResult:
    topic: str
    writer_model: str
    success: bool
    selected_winner: str
    winner_reason: str
    live_generation_mode: str
    live_rewrite_noop: bool
    live_rewrite_noop_reason: str
    live_rewrite_similarity_score: float | None
    live_rewrite_anchor_coverage_score: float | None
    live_rewrite_anchor_loss: bool
    live_rewrite_anchor_loss_reason: str
    judge_model: str
    judge_summary: str
    pipeline_output_folder: str
    script_title: str
    error: str = ""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark multiple writer models with the story pipeline.")
    parser.add_argument(
        "--models-file",
        default="writer_models.txt",
        help="Path to newline-delimited writer model names (default: writer_models.txt).",
    )
    parser.add_argument(
        "--topics-file",
        default="",
        help="Optional newline-delimited benchmark topics file.",
    )
    parser.add_argument(
        "--output-root",
        default="generated/writer_model_benchmark",
        help="Benchmark output root folder.",
    )
    parser.add_argument(
        "--pipeline-script",
        default="run_story_pipeline.py",
        help="Pipeline entrypoint to execute.",
    )
    parser.add_argument(
        "--media-mode",
        default="",
        help="Override media mode (default: BENCHMARK_MEDIA_MODE env or real_images).",
    )
    parser.add_argument(
        "--provider",
        default="",
        help="Override SCRIPT_PROVIDER for benchmark runs (default: SCRIPT_PROVIDER env or openrouter).",
    )
    parser.add_argument(
        "--judge-model",
        default="",
        help="Override SCRIPT_JUDGE_MODEL for benchmark runs (default: env or openai/gpt-4o-mini).",
    )
    return parser.parse_args()


def _load_line_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    values: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        values.append(line)
    return values


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _discover_new_run_dir(output_root: Path, before: set[Path]) -> Path | None:
    after = {path for path in output_root.iterdir() if path.is_dir()} if output_root.exists() else set()
    created = sorted((path for path in after if path not in before), key=lambda path: path.stat().st_mtime)
    if created:
        return created[-1]
    if after:
        return sorted(after, key=lambda path: path.stat().st_mtime)[-1]
    return None


def _parse_manifest(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "pipeline_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"pipeline manifest not found at {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _run_one_topic(
    *,
    pipeline_script: Path,
    output_root: Path,
    topic: str,
    writer_model: str,
    media_mode: str,
    provider: str,
    judge_model: str,
) -> RunResult:
    output_root.mkdir(parents=True, exist_ok=True)
    before = {path for path in output_root.iterdir() if path.is_dir()}

    env = os.environ.copy()
    env["SCRIPT_PROVIDER"] = provider
    env["SCRIPT_WRITER_MODEL"] = writer_model
    env["SCRIPT_JUDGE_MODEL"] = judge_model

    cmd = [
        "python3",
        str(pipeline_script),
        topic,
        "--media-mode",
        media_mode,
        "--output-root",
        str(output_root),
    ]
    completed = subprocess.run(cmd, env=env, capture_output=True, text=True)
    run_dir = _discover_new_run_dir(output_root, before)

    if completed.returncode != 0:
        error_text = completed.stderr.strip() or completed.stdout.strip() or "pipeline command failed"
        return RunResult(
            topic=topic,
            writer_model=writer_model,
            success=False,
            selected_winner="error",
            winner_reason="pipeline run failed",
            live_generation_mode="",
            live_rewrite_noop=False,
            live_rewrite_noop_reason="",
            live_rewrite_similarity_score=None,
            live_rewrite_anchor_coverage_score=None,
            live_rewrite_anchor_loss=False,
            live_rewrite_anchor_loss_reason="",
            judge_model="",
            judge_summary="",
            pipeline_output_folder=str(run_dir) if run_dir else "",
            script_title="",
            error=error_text,
        )

    if run_dir is None:
        return RunResult(
            topic=topic,
            writer_model=writer_model,
            success=False,
            selected_winner="error",
            winner_reason="pipeline manifest folder not found",
            live_generation_mode="",
            live_rewrite_noop=False,
            live_rewrite_noop_reason="",
            live_rewrite_similarity_score=None,
            live_rewrite_anchor_coverage_score=None,
            live_rewrite_anchor_loss=False,
            live_rewrite_anchor_loss_reason="",
            judge_model="",
            judge_summary="",
            pipeline_output_folder="",
            script_title="",
            error="run folder discovery failed",
        )

    try:
        manifest = _parse_manifest(run_dir)
    except Exception as exc:
        return RunResult(
            topic=topic,
            writer_model=writer_model,
            success=False,
            selected_winner="error",
            winner_reason="manifest parse failed",
            live_generation_mode="",
            live_rewrite_noop=False,
            live_rewrite_noop_reason="",
            live_rewrite_similarity_score=None,
            live_rewrite_anchor_coverage_score=None,
            live_rewrite_anchor_loss=False,
            live_rewrite_anchor_loss_reason="",
            judge_model="",
            judge_summary="",
            pipeline_output_folder=str(run_dir),
            script_title="",
            error=str(exc),
        )

    script_meta = manifest.get("script_metadata", {}) if isinstance(manifest, dict) else {}
    arb = manifest.get("script_arbitration", {}) if isinstance(manifest, dict) else {}
    return RunResult(
        topic=topic,
        writer_model=writer_model,
        success=True,
        selected_winner=str(
            arb.get("selected_script_source")
            or script_meta.get("script_source_winner")
            or ""
        ).strip(),
        winner_reason=str(arb.get("winner_reason", "")).strip(),
        live_generation_mode=str(arb.get("live_generation_mode", "")).strip(),
        live_rewrite_noop=bool(arb.get("live_rewrite_noop", False)),
        live_rewrite_noop_reason=str(arb.get("live_rewrite_noop_reason", "")).strip(),
        live_rewrite_similarity_score=_safe_float(arb.get("live_rewrite_similarity_score")),
        live_rewrite_anchor_coverage_score=_safe_float(arb.get("live_rewrite_anchor_coverage_score")),
        live_rewrite_anchor_loss=bool(arb.get("live_rewrite_anchor_loss", False)),
        live_rewrite_anchor_loss_reason=str(arb.get("live_rewrite_anchor_loss_reason", "")).strip(),
        judge_model=str(arb.get("judge_model", "")).strip(),
        judge_summary=str(arb.get("judge_summary", "")).strip(),
        pipeline_output_folder=str(run_dir),
        script_title=str(script_meta.get("title", "")).strip(),
        error="",
    )


def _average(values: list[float | None]) -> float:
    nums = [value for value in values if value is not None]
    if not nums:
        return 0.0
    return round(sum(nums) / len(nums), 4)


def _summarize_model(rows: list[RunResult]) -> dict[str, Any]:
    total_topics = len(rows)
    live_wins = sum(1 for row in rows if row.selected_winner == "live")
    fallback_wins = sum(1 for row in rows if row.selected_winner == "fallback")
    noop_count = sum(1 for row in rows if row.live_rewrite_noop)
    anchor_loss_count = sum(1 for row in rows if row.live_rewrite_anchor_loss)
    failed_runs = sum(1 for row in rows if not row.success)
    avg_similarity = _average([row.live_rewrite_similarity_score for row in rows])
    avg_anchor_coverage = _average([row.live_rewrite_anchor_coverage_score for row in rows])

    # Structural preservation is prioritized over style:
    # - anchor loss is penalized heavily
    # - no-op rewrites are penalized
    # - higher anchor coverage helps
    quality_score = round(
        (live_wins * 3.0)
        - (fallback_wins * 1.0)
        - (noop_count * 2.5)
        - (anchor_loss_count * 6.0)
        - (failed_runs * 3.0)
        + (avg_anchor_coverage * 5.0)
        - (avg_similarity * 1.5),
        4,
    )

    return {
        "writer_model": rows[0].writer_model if rows else "",
        "total_topics": total_topics,
        "live_wins": live_wins,
        "fallback_wins": fallback_wins,
        "noop_count": noop_count,
        "anchor_loss_count": anchor_loss_count,
        "failed_runs": failed_runs,
        "average_similarity_score": avg_similarity,
        "average_anchor_coverage_score": avg_anchor_coverage,
        "quality_score": quality_score,
    }


def _recommend_best_model(summaries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not summaries:
        return None
    return max(
        summaries,
        key=lambda row: (
            float(row.get("quality_score", 0.0)),
            int(row.get("live_wins", 0)),
            -int(row.get("anchor_loss_count", 0)),
            -int(row.get("noop_count", 0)),
            float(row.get("average_anchor_coverage_score", 0.0)),
        ),
    )


def _write_summary_csv(path: Path, summaries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "writer_model",
        "total_topics",
        "live_wins",
        "fallback_wins",
        "noop_count",
        "anchor_loss_count",
        "failed_runs",
        "average_similarity_score",
        "average_anchor_coverage_score",
        "quality_score",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for summary in summaries:
            writer.writerow({field: summary.get(field, "") for field in fieldnames})


def _render_markdown(
    *,
    settings: dict[str, Any],
    summaries: list[dict[str, Any]],
    rows: list[RunResult],
    recommendation: dict[str, Any] | None,
) -> str:
    lines: list[str] = []
    lines.append("# Writer Model Benchmark")
    lines.append("")
    lines.append("## Benchmark Settings")
    lines.append(f"- provider: {settings.get('provider', '')}")
    lines.append(f"- judge_model: {settings.get('judge_model', '')}")
    lines.append(f"- media_mode: {settings.get('media_mode', '')}")
    lines.append(f"- models_file: {settings.get('models_file', '')}")
    lines.append(f"- topics_file: {settings.get('topics_file', '')}")
    lines.append(f"- models_tested: {', '.join(settings.get('models', []))}")
    lines.append(f"- topics_tested: {', '.join(settings.get('topics', []))}")
    lines.append("")

    lines.append("## Per-Model Summary")
    lines.append("")
    lines.append(
        "| writer_model | total_topics | live_wins | fallback_wins | noop_count | anchor_loss_count | "
        "avg_similarity | avg_anchor_coverage | quality_score |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for summary in summaries:
        lines.append(
            f"| {summary.get('writer_model', '')} | {summary.get('total_topics', 0)} | "
            f"{summary.get('live_wins', 0)} | {summary.get('fallback_wins', 0)} | "
            f"{summary.get('noop_count', 0)} | {summary.get('anchor_loss_count', 0)} | "
            f"{summary.get('average_similarity_score', 0.0):.3f} | "
            f"{summary.get('average_anchor_coverage_score', 0.0):.3f} | "
            f"{summary.get('quality_score', 0.0):.3f} |"
        )
    lines.append("")

    lines.append("## Per-Topic Detail")
    lines.append("")
    lines.append(
        "| writer_model | topic | winner | noop | anchor_loss | similarity | anchor_coverage | "
        "live_mode | title | run_folder |"
    )
    lines.append("|---|---|---|---|---|---:|---:|---|---|---|")
    for row in rows:
        lines.append(
            f"| {row.writer_model} | {row.topic} | {row.selected_winner} | "
            f"{row.live_rewrite_noop} | {row.live_rewrite_anchor_loss} | "
            f"{(row.live_rewrite_similarity_score or 0.0):.3f} | "
            f"{(row.live_rewrite_anchor_coverage_score or 0.0):.3f} | "
            f"{row.live_generation_mode} | {row.script_title} | {row.pipeline_output_folder} |"
        )
    lines.append("")

    lines.append("## Recommendation")
    lines.append("")
    if recommendation is None:
        lines.append("- No recommendation available (no successful benchmark rows).")
    else:
        lines.append(f"- recommended_writer_model: {recommendation.get('writer_model', '')}")
        lines.append(f"- quality_score: {float(recommendation.get('quality_score', 0.0)):.3f}")
        lines.append(
            "- rationale: highest quality score with heavy penalties for anchor loss and no-op rewrites."
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = _parse_args()
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    benchmark_root = Path(args.output_root) / run_stamp
    pipeline_outputs_root = benchmark_root / "pipelines"
    pipeline_script = Path(args.pipeline_script)

    if not pipeline_script.exists():
        print(f"pipeline script not found: {pipeline_script}", file=sys.stderr)
        return 1

    models_file = Path(args.models_file)
    models = _load_line_list(models_file)
    if not models:
        models = DEFAULT_MODELS[:]
    models = _dedupe_keep_order(models)
    if not models:
        print("no writer models configured", file=sys.stderr)
        return 1

    topics: list[str]
    if args.topics_file.strip():
        topics = _dedupe_keep_order(_load_line_list(Path(args.topics_file)))
    else:
        topics = DEFAULT_TOPICS[:]
    if not topics:
        print("no benchmark topics configured", file=sys.stderr)
        return 1

    provider = args.provider.strip() or os.getenv("SCRIPT_PROVIDER", "").strip() or "openrouter"
    judge_model = args.judge_model.strip() or os.getenv("SCRIPT_JUDGE_MODEL", "").strip() or "openai/gpt-4o-mini"
    media_mode = args.media_mode.strip() or os.getenv("BENCHMARK_MEDIA_MODE", "").strip() or "real_images"

    print("writer model benchmark")
    print(f"output folder: {benchmark_root}")
    print(f"provider: {provider}")
    print(f"judge model: {judge_model}")
    print(f"media mode: {media_mode}")
    print(f"models: {', '.join(models)}")
    print(f"topics: {', '.join(topics)}")

    rows: list[RunResult] = []
    for writer_model in models:
        for topic in topics:
            print(f"[run] model={writer_model} topic={topic}")
            row = _run_one_topic(
                pipeline_script=pipeline_script,
                output_root=pipeline_outputs_root,
                topic=topic,
                writer_model=writer_model,
                media_mode=media_mode,
                provider=provider,
                judge_model=judge_model,
            )
            rows.append(row)
            print(
                f"[done] model={writer_model} topic={topic} winner={row.selected_winner} "
                f"noop={row.live_rewrite_noop} anchor_loss={row.live_rewrite_anchor_loss}"
            )
            if not row.success and row.error:
                print(f"[error] {row.error}")

    grouped: dict[str, list[RunResult]] = {}
    for row in rows:
        grouped.setdefault(row.writer_model, []).append(row)
    summaries = [_summarize_model(grouped[model]) for model in models if model in grouped]
    recommendation = _recommend_best_model(summaries)

    benchmark_manifest = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "settings": {
            "provider": provider,
            "judge_model": judge_model,
            "media_mode": media_mode,
            "models_file": str(models_file),
            "topics_file": str(args.topics_file).strip(),
            "models": models,
            "topics": topics,
            "pipeline_script": str(pipeline_script),
            "pipeline_output_root": str(pipeline_outputs_root),
            "quality_score_formula": (
                "live_wins*3 - fallback_wins*1 - noop_count*2.5 - anchor_loss_count*6 "
                "- failed_runs*3 + avg_anchor_coverage*5 - avg_similarity*1.5"
            ),
        },
        "rows": [row.__dict__ for row in rows],
        "model_summaries": summaries,
        "recommendation": recommendation or {},
    }

    benchmark_root.mkdir(parents=True, exist_ok=True)
    manifest_path = benchmark_root / "benchmark_manifest.json"
    manifest_path.write_text(json.dumps(benchmark_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    csv_path = benchmark_root / "benchmark_summary.csv"
    _write_summary_csv(csv_path, summaries)

    markdown_text = _render_markdown(
        settings=benchmark_manifest["settings"],
        summaries=summaries,
        rows=rows,
        recommendation=recommendation,
    )
    markdown_path = benchmark_root / "benchmark_summary.md"
    markdown_path.write_text(markdown_text, encoding="utf-8")

    print(f"benchmark manifest: {manifest_path}")
    print(f"benchmark summary csv: {csv_path}")
    print(f"benchmark summary markdown: {markdown_path}")
    if recommendation:
        print(f"recommended writer model: {recommendation.get('writer_model', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

