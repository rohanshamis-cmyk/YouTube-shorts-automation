#!/usr/bin/env python3
"""Offline-friendly storytelling regression harness.

Reads a fixed topic list, generates scripts via ScriptGenerator, and writes
CSV/Markdown quality reports for storytelling progression checks.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import types
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

# Keep this harness runnable with plain `python3` even if the anthropic package
# is not installed locally. ScriptGenerator only needs it when Anthropic is used.
try:  # pragma: no cover
    import anthropic  # type: ignore  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    anthropic_stub = types.ModuleType("anthropic")

    class _AnthropicStubClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.messages = self

        def create(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("anthropic package is not installed")

    anthropic_stub.Anthropic = _AnthropicStubClient  # type: ignore[attr-defined]
    sys.modules["anthropic"] = anthropic_stub

from script_generator import ScriptGenerator


def _bool_str(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _load_topics(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Topics file not found: {path}")

    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            items = payload.get("topics", [])
        else:
            items = payload
        topics: list[str] = []
        for item in items:
            if isinstance(item, str):
                topic = item.strip()
            elif isinstance(item, dict):
                topic = str(item.get("title", "")).strip()
            else:
                topic = ""
            if topic:
                topics.append(topic)
        return topics

    topics = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        topics.append(line)
    return topics


def _build_config(allow_live: bool) -> dict[str, Any]:
    cfg = {
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", "").strip(),
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", "").strip(),
        "SCRIPT_OPENAI_API_KEY": os.getenv("SCRIPT_OPENAI_API_KEY", "").strip(),
        "SCRIPT_OPENAI_BASE_URL": os.getenv("SCRIPT_OPENAI_BASE_URL", "").strip(),
        "SCRIPT_OPENAI_MODEL": os.getenv("SCRIPT_OPENAI_MODEL", "").strip(),
        "SCRIPT_ANTHROPIC_MODEL": os.getenv("SCRIPT_ANTHROPIC_MODEL", "").strip(),
        "SCRIPT_PROVIDER": os.getenv("SCRIPT_PROVIDER", "").strip(),
        "NICHE": "ai_tools",
    }

    if not allow_live:
        cfg["OPENAI_API_KEY"] = ""
        cfg["ANTHROPIC_API_KEY"] = ""
        cfg["SCRIPT_OPENAI_API_KEY"] = ""
        cfg["SCRIPT_OPENAI_BASE_URL"] = ""
        cfg["SCRIPT_PROVIDER"] = ""

    return cfg


def _build_niche_config() -> dict[str, Any]:
    return {
        "hook_templates": [
            "Someone whispered my name from the hallway.",
            "The room went quiet after one sentence.",
            "I heard exactly what was said behind that door.",
        ]
    }


def _topic_dict(topic_text: str) -> dict[str, Any]:
    return {
        "title": topic_text,
        "source": "storytelling_regression",
        "engagement": 0,
        "score": 0,
        "summary": "",
        "timestamp": datetime.now().isoformat(),
    }


def _extract_sentences(generator: ScriptGenerator, hook: str, narration: str) -> tuple[str, str, str, str]:
    body = generator._strip_hook_prefix_from_narration(narration=narration, hook=hook)
    sentences = generator._sentence_list(body)
    if not sentences:
        sentences = generator._sentence_list(narration)

    while len(sentences) < 4:
        sentences.append("")

    return sentences[0], sentences[1], sentences[2], sentences[3]


def _evaluate_topic(generator: ScriptGenerator, topic_text: str) -> dict[str, Any]:
    topic = _topic_dict(topic_text)
    lane = generator._detect_content_lane(topic)
    topic_is_ai = generator._topic_mentions_ai(topic)

    row: dict[str, Any] = {
        "topic": topic_text,
        "detected_archetype": "",
        "hook": "",
        "sentence_1": "",
        "sentence_2": "",
        "sentence_3": "",
        "sentence_4": "",
        "selected_total": 0.0,
        "hook_topic_anchored": False,
        "sentence_2_not_near_repeat_of_hook": False,
        "sentence_3_contains_reveal_or_new_detail": False,
        "sentence_4_has_landing_or_consequence": False,
        "no_generic_dead_end": False,
        "all_checks_pass": False,
        "error": "",
    }

    try:
        script = generator.generate(topic=topic, duration_target=45)
        hook = str(script.get("hook", "") or "").strip()
        narration = str(script.get("narration", "") or "").strip()

        sentence_1, sentence_2, sentence_3, sentence_4 = _extract_sentences(
            generator=generator,
            hook=hook,
            narration=narration,
        )

        report = generator._storytelling_anchor_report(hook=hook, narration=narration, topic=topic)
        score = generator._score_script_candidate(script=script, topic=topic, lane=lane, topic_is_ai=topic_is_ai)
        archetype = str(report.get("archetype", "") or "")

        hook_topic_anchored = bool(
            report.get("hook_hits", 0) > 0
            or report.get("hook_anchor_hits", 0) > 0
            or report.get("first_two_anchor_hits", 0) > 0
        )
        sentence_2_not_near_repeat_of_hook = not bool(report.get("repeat_s2_hook", False))
        sentence_3_contains_reveal_or_new_detail = bool(
            report.get("s3_has_reveal", False)
            or (
                not bool(report.get("repeat_s3_s2", False))
                and generator._has_story_sensory_detail(sentence_3)
            )
        )
        sentence_4_has_landing_or_consequence = bool(
            report.get("s4_has_landing", False) and not report.get("s4_dead_end", False)
        )
        no_generic_dead_end = not generator._story_line_is_dead_end(sentence_4, archetype=archetype)

        row.update(
            {
                "detected_archetype": archetype,
                "hook": hook,
                "sentence_1": sentence_1,
                "sentence_2": sentence_2,
                "sentence_3": sentence_3,
                "sentence_4": sentence_4,
                "selected_total": float(score.get("total", 0.0)),
                "hook_topic_anchored": hook_topic_anchored,
                "sentence_2_not_near_repeat_of_hook": sentence_2_not_near_repeat_of_hook,
                "sentence_3_contains_reveal_or_new_detail": sentence_3_contains_reveal_or_new_detail,
                "sentence_4_has_landing_or_consequence": sentence_4_has_landing_or_consequence,
                "no_generic_dead_end": no_generic_dead_end,
            }
        )
    except Exception as exc:  # pragma: no cover
        row["error"] = str(exc)

    row["all_checks_pass"] = bool(
        row["hook_topic_anchored"]
        and row["sentence_2_not_near_repeat_of_hook"]
        and row["sentence_3_contains_reveal_or_new_detail"]
        and row["sentence_4_has_landing_or_consequence"]
        and row["no_generic_dead_end"]
        and not row["error"]
    )
    return row


def _write_csv(rows: list[dict[str, Any]], csv_path: Path) -> None:
    fieldnames = [
        "topic",
        "detected_archetype",
        "hook",
        "sentence_1",
        "sentence_2",
        "sentence_3",
        "sentence_4",
        "selected_total",
        "hook_topic_anchored",
        "sentence_2_not_near_repeat_of_hook",
        "sentence_3_contains_reveal_or_new_detail",
        "sentence_4_has_landing_or_consequence",
        "no_generic_dead_end",
        "all_checks_pass",
        "error",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_markdown(rows: list[dict[str, Any]], md_path: Path, topics_path: Path, allow_live: bool) -> None:
    pass_count = sum(1 for row in rows if row["all_checks_pass"])
    fail_count = len(rows) - pass_count
    weakest = sorted(rows, key=lambda row: float(row.get("selected_total", 0.0)))[:5]

    lines = [
        "# Storytelling Regression Report",
        "",
        f"- Generated at: {datetime.now().isoformat()}",
        f"- Topics file: {topics_path}",
        f"- Allow live providers: {allow_live}",
        f"- Total topics: {len(rows)}",
        f"- Pass: {pass_count}",
        f"- Fail: {fail_count}",
        "",
        "## Weakest 5 by selected_total",
        "",
        "| topic | archetype | selected_total | all_checks_pass |",
        "|---|---|---:|---|",
    ]

    for row in weakest:
        lines.append(
            "| {topic} | {detected_archetype} | {selected_total:.1f} | {all_checks_pass} |".format(
                topic=row["topic"],
                detected_archetype=row["detected_archetype"] or "",
                selected_total=float(row.get("selected_total", 0.0)),
                all_checks_pass=_bool_str(bool(row.get("all_checks_pass", False))),
            )
        )

    lines.extend(
        [
            "",
            "## Per-topic Checks",
            "",
            "| topic | archetype | total | anchor | s2_not_repeat | s3_reveal_or_detail | s4_landing | no_dead_end | overall |",
            "|---|---|---:|---|---|---|---|---|---|",
        ]
    )

    for row in rows:
        lines.append(
            "| {topic} | {detected_archetype} | {selected_total:.1f} | {anchor} | {s2} | {s3} | {s4} | {dead_end} | {overall} |".format(
                topic=row["topic"],
                detected_archetype=row["detected_archetype"] or "",
                selected_total=float(row.get("selected_total", 0.0)),
                anchor=_bool_str(bool(row.get("hook_topic_anchored", False))),
                s2=_bool_str(bool(row.get("sentence_2_not_near_repeat_of_hook", False))),
                s3=_bool_str(bool(row.get("sentence_3_contains_reveal_or_new_detail", False))),
                s4=_bool_str(bool(row.get("sentence_4_has_landing_or_consequence", False))),
                dead_end=_bool_str(bool(row.get("no_generic_dead_end", False))),
                overall=_bool_str(bool(row.get("all_checks_pass", False))),
            )
        )

    errored = [row for row in rows if row.get("error")]
    if errored:
        lines.extend(["", "## Errors", ""])
        for row in errored:
            lines.append(f"- {row['topic']}: {row['error']}")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline storytelling regression checker")
    parser.add_argument(
        "--topics-file",
        default="storytelling_regression_topics.txt",
        help="Path to a .txt or .json topic list",
    )
    parser.add_argument(
        "--csv-out",
        default="storytelling_regression_report.csv",
        help="CSV output path",
    )
    parser.add_argument(
        "--md-out",
        default="storytelling_regression_report.md",
        help="Markdown output path",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Allow configured live provider attempts before fallback",
    )
    args = parser.parse_args()

    if load_dotenv:
        load_dotenv()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    topics_path = Path(args.topics_file)
    csv_path = Path(args.csv_out)
    md_path = Path(args.md_out)

    topics = _load_topics(topics_path)
    if not topics:
        raise RuntimeError(f"No topics found in {topics_path}")

    config = _build_config(allow_live=args.allow_live)
    niche_config = _build_niche_config()
    generator = ScriptGenerator(config=config, niche_config=niche_config)

    rows: list[dict[str, Any]] = []
    for idx, topic in enumerate(topics, start=1):
        logging.info("[%s/%s] Evaluating topic: %s", idx, len(topics), topic)
        rows.append(_evaluate_topic(generator=generator, topic_text=topic))

    _write_csv(rows=rows, csv_path=csv_path)
    _write_markdown(rows=rows, md_path=md_path, topics_path=topics_path, allow_live=args.allow_live)

    pass_count = sum(1 for row in rows if row["all_checks_pass"])
    fail_count = len(rows) - pass_count
    weakest = sorted(rows, key=lambda row: float(row.get("selected_total", 0.0)))[:5]

    print(f"Topics evaluated: {len(rows)}")
    print(f"Pass: {pass_count}")
    print(f"Fail: {fail_count}")
    print(f"CSV report: {csv_path}")
    print(f"Markdown report: {md_path}")
    print("Weakest 5 topics by selected_total:")
    for row in weakest:
        print(f"- {row['topic']} -> {float(row.get('selected_total', 0.0)):.1f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
