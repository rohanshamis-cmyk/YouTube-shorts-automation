#!/usr/bin/env python3
"""
Content QA harness for script quality evaluation across topic sets.

Runs the real generation flow sequentially via:
    ./run_batch.sh 1 "<topic>"

Produces:
  - qa_reports/content_quality_<timestamp>.csv
  - qa_reports/content_quality_<timestamp>.md
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_TOPICS: list[tuple[str, str]] = [
    ("ai_productivity", "AI productivity tools"),
    ("ai_productivity", "AI email workflow"),
    ("ai_productivity", "AI lecture notes summary"),
    ("ai_productivity", "AI meeting follow up"),
    ("ai_productivity", "AI writing assistant for students"),
    ("ai_productivity", "AI checklist automation"),
    ("ai_productivity", "AI study workflow"),
    ("ai_productivity", "AI admin task automation"),
    ("ai_productivity", "AI note cleanup"),
    ("ai_productivity", "AI inbox management"),
    ("storytelling", "mystery confession story"),
    ("storytelling", "creepy hallway confession"),
    ("storytelling", "the whisper behind the door"),
    ("storytelling", "I heard my name at midnight"),
    ("storytelling", "the room went quiet after one sentence"),
    ("storytelling", "I should have left right there"),
    ("storytelling", "the note under my door"),
    ("storytelling", "something moved in the hallway"),
    ("storytelling", "the confession nobody should have heard"),
    ("storytelling", "what happened after the lights went out"),
]

GENERIC_HOOK_PHRASES = [
    "new to",
    "start with this one rule",
    "here are",
    "this tool can",
    "imagine",
    "powerful tools",
    "boost productivity",
    "next level",
]

GENERIC_PENALTY_PHRASES = [
    "boost productivity",
    "next level",
    "powerful tools",
    "supercharge",
    "streamline your workflow",
    "game changer",
    "game-changer",
    "secret weapon",
    "start with this one rule",
    "new to",
    "here are",
    "imagine",
]

STORY_CONTAMINATION_TERMS = [
    "ai",
    "chatgpt",
    "notion",
    "productivity",
    "workflow",
    "automation",
    "inbox",
    "lecture notes",
    "checklist",
]

AI_TARGET_TERMS = [
    "email",
    "emails",
    "inbox",
    "notes",
    "lecture",
    "meeting",
    "writing",
    "checklist",
    "admin",
    "follow-up",
    "follow up",
    "calendar",
]

AI_ACTION_TERMS = [
    "summarize",
    "draft",
    "reply",
    "clean",
    "organize",
    "convert",
    "automate",
    "review",
    "send",
]

CSV_COLUMNS = [
    "timestamp",
    "lane",
    "topic_input",
    "batch_dir",
    "title",
    "hook",
    "sentence_1",
    "sentence_2",
    "sentence_3",
    "sentence_4",
    "caption",
    "cta",
    "live_model_used",
    "fallback_used",
    "selected_candidate",
    "candidate_1_total",
    "candidate_2_total",
    "candidate_3_total",
    "candidate_1_hook",
    "candidate_2_hook",
    "candidate_3_hook",
    "generic_hook_flag",
    "repetitive_hook_flag",
    "storytelling_contamination_flag",
    "ai_concreteness_flag",
    "provider_attempted",
    "selected_total",
    "command_exit_code",
    "error_message",
]


@dataclass
class TopicItem:
    lane: str
    topic: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate script quality across a topic set.")
    parser.add_argument(
        "--topic-file",
        type=str,
        default="",
        help="Optional topic file (.txt or .json). Defaults to built-in 20-topic set.",
    )
    return parser.parse_args()


def normalize_text(text: str) -> str:
    lowered = re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", lowered).strip()


def token_set(text: str) -> set[str]:
    stop = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "for",
        "of",
        "in",
        "on",
        "with",
        "this",
        "that",
        "it",
        "is",
        "are",
        "was",
        "were",
        "be",
        "you",
        "your",
        "i",
        "we",
        "my",
    }
    tokens = re.findall(r"[a-z0-9']+", normalize_text(text))
    return {tok for tok in tokens if tok not in stop and len(tok) > 2}


def jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def infer_lane(topic: str) -> str:
    lowered = normalize_text(topic)
    story_terms = {
        "mystery",
        "confession",
        "whisper",
        "hallway",
        "midnight",
        "door",
        "lights",
        "room",
    }
    if any(term in lowered for term in story_terms):
        return "storytelling"
    return "ai_productivity"


def load_topics(topic_file: str) -> list[TopicItem]:
    if not topic_file:
        return [TopicItem(lane=lane, topic=topic) for lane, topic in DEFAULT_TOPICS]

    path = Path(topic_file)
    if not path.exists():
        raise FileNotFoundError(f"Topic file not found: {path}")

    items: list[TopicItem] = []
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("JSON topic file must be a list")
        for entry in data:
            if isinstance(entry, dict):
                topic = str(entry.get("topic") or entry.get("title") or "").strip()
                if not topic:
                    continue
                lane = str(entry.get("lane") or "").strip().lower() or infer_lane(topic)
                items.append(TopicItem(lane=lane, topic=topic))
            elif isinstance(entry, str):
                topic = entry.strip()
                if topic:
                    items.append(TopicItem(lane=infer_lane(topic), topic=topic))
    else:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            lane = ""
            topic = line
            if "|" in line:
                left, right = [part.strip() for part in line.split("|", 1)]
                if left in {"ai_productivity", "storytelling"} and right:
                    lane, topic = left, right
            elif "::" in line:
                left, right = [part.strip() for part in line.split("::", 1)]
                if left in {"ai_productivity", "storytelling"} and right:
                    lane, topic = left, right
            if topic:
                items.append(TopicItem(lane=lane or infer_lane(topic), topic=topic))

    if not items:
        raise ValueError("No topics loaded from topic file")
    return items


def parse_field(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.*)$", text, re.MULTILINE)
    return (match.group(1).strip() if match else "")


def parse_script_block(text: str) -> str:
    match = re.search(r"^SCRIPT:\n(.*?)(?:\n\n[A-Z_]+:|\Z)", text, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    return " ".join(line.strip() for line in match.group(1).splitlines() if line.strip())


def first_sentences(script: str, count: int = 4) -> list[str]:
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", script or "") if part.strip()]
    out = parts[:count]
    while len(out) < count:
        out.append("")
    return out


def parse_candidate_scores(log_text: str) -> tuple[dict[int, dict[str, float]], str]:
    candidate_scores: dict[int, dict[str, float]] = {1: {}, 2: {}, 3: {}}
    selected_candidate = ""
    current = 0
    for line in (log_text or "").splitlines():
        c_match = re.search(r"Candidate\s+(\d+)\s+score:", line)
        if c_match:
            current = int(c_match.group(1))
            continue

        if current:
            kv = re.search(r"-\s*([a-zA-Z_]+)\s*=\s*([-+]?\d+(?:\.\d+)?)", line)
            if kv:
                key = kv.group(1).strip().lower()
                value = float(kv.group(2))
                candidate_scores.setdefault(current, {})[key] = value

        s_match = re.search(r"Selected candidate:\s*(\d+)", line)
        if s_match:
            selected_candidate = s_match.group(1)
    return candidate_scores, selected_candidate


def parse_provider_attempts(log_text: str) -> str:
    attempts = re.findall(r"Trying live script model:\s*([^\s]+)", log_text or "")
    seen: list[str] = []
    for item in attempts:
        if item not in seen:
            seen.append(item)
    return ";".join(seen)


def detect_batch_dir(before: set[Path], after: set[Path], log_text: str, start_ts: float) -> Path | None:
    from_log = re.findall(r"(drafts/batch_[^\s/]+)", log_text or "")
    for candidate in reversed(from_log):
        path = Path(candidate)
        if path.exists() and path.is_dir():
            return path

    diff = [item for item in after if item not in before]
    if diff:
        return max(diff, key=lambda p: p.stat().st_mtime)

    candidates = sorted(after, key=lambda p: p.stat().st_mtime, reverse=True)
    for item in candidates:
        if item.stat().st_mtime >= start_ts - 2:
            return item
    return None


def find_dry_run_file(batch_dir: Path | None) -> Path | None:
    if not batch_dir or not batch_dir.exists():
        return None
    files = sorted(batch_dir.glob("dry_run_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def generic_hook_flag(hook: str) -> bool:
    lowered = normalize_text(hook)
    return any(phrase in lowered for phrase in GENERIC_HOOK_PHRASES)


def storytelling_contamination_flag(lane: str, script: str) -> bool:
    if lane != "storytelling":
        return False
    lowered = normalize_text(script)
    return any(term in lowered for term in STORY_CONTAMINATION_TERMS)


def ai_concreteness_flag(lane: str, script: str) -> bool:
    if lane != "ai_productivity":
        return False
    lowered = normalize_text(script)
    has_target = any(term in lowered for term in AI_TARGET_TERMS)
    has_action = any(term in lowered for term in AI_ACTION_TERMS)
    return has_target and has_action


def repetitive_hook_flag(hook: str, prior_hooks: list[str], threshold: float = 0.72) -> bool:
    current = token_set(hook)
    if not current:
        return False
    for previous in prior_hooks:
        sim = jaccard_similarity(current, token_set(previous))
        if sim >= threshold:
            return True
    return False


def run_topic(topic_item: TopicItem, prior_hooks: list[str]) -> dict[str, Any]:
    cwd = Path.cwd()
    drafts_dir = cwd / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    before = set(drafts_dir.glob("batch_*"))
    started = datetime.now()
    started_ts = started.timestamp()

    cmd = ["./run_batch.sh", "1", topic_item.topic]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    combined_output = f"{proc.stdout}\n{proc.stderr}".strip()

    after = set(drafts_dir.glob("batch_*"))
    batch_dir = detect_batch_dir(before=before, after=after, log_text=combined_output, start_ts=started_ts)
    dry_file = find_dry_run_file(batch_dir)

    dry_text = ""
    if dry_file and dry_file.exists():
        dry_text = dry_file.read_text(encoding="utf-8", errors="ignore")

    title = parse_field(dry_text, "TITLE")
    topic_value = parse_field(dry_text, "TOPIC")
    hook = parse_field(dry_text, "HOOK")
    script = parse_script_block(dry_text)
    caption = parse_field(dry_text, "CAPTION")
    cta = parse_field(dry_text, "CTA")
    s1, s2, s3, s4 = first_sentences(script, 4)

    candidate_scores, selected_candidate = parse_candidate_scores(combined_output)
    provider_attempted = parse_provider_attempts(combined_output)
    live_model_used = bool(re.search(r"Live model used successfully", combined_output))
    fallback_used = bool(re.search(r"Fallback script used for topic", combined_output))

    c1_total = candidate_scores.get(1, {}).get("total", "")
    c2_total = candidate_scores.get(2, {}).get("total", "")
    c3_total = candidate_scores.get(3, {}).get("total", "")
    c1_hook = candidate_scores.get(1, {}).get("hook", "")
    c2_hook = candidate_scores.get(2, {}).get("hook", "")
    c3_hook = candidate_scores.get(3, {}).get("hook", "")

    selected_total = ""
    if selected_candidate.isdigit():
        selected_total = candidate_scores.get(int(selected_candidate), {}).get("total", "")

    row = {
        "timestamp": started.isoformat(timespec="seconds"),
        "lane": topic_item.lane,
        "topic_input": topic_item.topic,
        "batch_dir": str(batch_dir) if batch_dir else "",
        "title": title,
        "hook": hook,
        "sentence_1": s1,
        "sentence_2": s2,
        "sentence_3": s3,
        "sentence_4": s4,
        "caption": caption,
        "cta": cta,
        "live_model_used": str(live_model_used).lower(),
        "fallback_used": str(fallback_used).lower(),
        "selected_candidate": selected_candidate,
        "candidate_1_total": c1_total,
        "candidate_2_total": c2_total,
        "candidate_3_total": c3_total,
        "candidate_1_hook": c1_hook,
        "candidate_2_hook": c2_hook,
        "candidate_3_hook": c3_hook,
        "generic_hook_flag": str(generic_hook_flag(hook)).lower(),
        "repetitive_hook_flag": str(repetitive_hook_flag(hook, prior_hooks)).lower(),
        "storytelling_contamination_flag": str(storytelling_contamination_flag(topic_item.lane, script)).lower(),
        "ai_concreteness_flag": str(ai_concreteness_flag(topic_item.lane, script)).lower(),
        "provider_attempted": provider_attempted,
        "selected_total": selected_total,
        "command_exit_code": proc.returncode,
        "error_message": "" if proc.returncode == 0 else (proc.stderr or proc.stdout)[-400:],
    }
    return row


def safe_float(value: Any) -> float | None:
    try:
        if value == "":
            return None
        return float(value)
    except Exception:
        return None


def build_repetition_clusters(rows: list[dict[str, Any]], threshold: float = 0.65) -> list[tuple[str, str, float]]:
    hooks = [(row.get("topic_input", ""), row.get("hook", "")) for row in rows if row.get("hook")]
    clusters: list[tuple[str, str, float]] = []
    for i in range(len(hooks)):
        topic_a, hook_a = hooks[i]
        for j in range(i + 1, len(hooks)):
            topic_b, hook_b = hooks[j]
            sim = jaccard_similarity(token_set(hook_a), token_set(hook_b))
            if sim >= threshold:
                clusters.append((topic_a, topic_b, sim))
    clusters.sort(key=lambda item: item[2], reverse=True)
    return clusters


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in CSV_COLUMNS})


def write_markdown(path: Path, rows: list[dict[str, Any]], clusters: list[tuple[str, str, float]]) -> None:
    total = len(rows)
    ai_rows = [row for row in rows if row.get("lane") == "ai_productivity"]
    story_rows = [row for row in rows if row.get("lane") == "storytelling"]

    live_successes = sum(1 for row in rows if row.get("live_model_used") == "true")
    fallback_runs = sum(1 for row in rows if row.get("fallback_used") == "true")

    def avg_selected(items: list[dict[str, Any]]) -> float:
        values = [safe_float(row.get("selected_total")) for row in items]
        values = [value for value in values if value is not None]
        return (sum(values) / len(values)) if values else 0.0

    ai_avg = avg_selected(ai_rows)
    story_avg = avg_selected(story_rows)

    scored_rows: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        score = safe_float(row.get("selected_total"))
        if score is None:
            score = -1.0
        scored_rows.append((score, row))

    strongest = sorted(scored_rows, key=lambda item: item[0], reverse=True)[:5]
    weakest = sorted(scored_rows, key=lambda item: item[0])[:5]

    contamination_rows = [
        row for row in story_rows if row.get("storytelling_contamination_flag") == "true"
    ]
    weak_ai_rows = [
        row for row in ai_rows if row.get("ai_concreteness_flag") != "true"
    ]

    lines: list[str] = []
    lines.append(f"# Content QA Summary ({datetime.now().isoformat(timespec='seconds')})")
    lines.append("")
    lines.append("## Run Stats")
    lines.append(f"- Total topics run: {total}")
    lines.append(f"- AI topics: {len(ai_rows)}")
    lines.append(f"- Storytelling topics: {len(story_rows)}")
    lines.append(f"- Live-model successes: {live_successes}")
    lines.append(f"- Fallback runs: {fallback_runs}")
    lines.append(f"- Average selected score (AI): {ai_avg:.1f}")
    lines.append(f"- Average selected score (storytelling): {story_avg:.1f}")
    lines.append("")
    lines.append("## Weakest 5 Hooks")
    for score, row in weakest:
        lines.append(
            f"- `{row.get('topic_input', '')}` | score={score:.1f} | {row.get('hook', '').strip()}"
        )
    lines.append("")
    lines.append("## Strongest 5 Hooks")
    for score, row in strongest:
        lines.append(
            f"- `{row.get('topic_input', '')}` | score={score:.1f} | {row.get('hook', '').strip()}"
        )
    lines.append("")
    lines.append("## Possible Repetition Clusters")
    if clusters:
        for left, right, sim in clusters[:10]:
            lines.append(f"- `{left}` ↔ `{right}` (hook similarity: {sim:.2f})")
    else:
        lines.append("- No strong repetition clusters detected.")
    lines.append("")
    lines.append("## Contamination / Quality Issues")
    if contamination_rows:
        lines.append("- Storytelling contamination flags:")
        for row in contamination_rows:
            lines.append(f"  - `{row.get('topic_input', '')}` | hook: {row.get('hook', '')}")
    else:
        lines.append("- No storytelling contamination flags.")
    if weak_ai_rows:
        lines.append("- Weak AI concreteness flags:")
        for row in weak_ai_rows:
            lines.append(f"  - `{row.get('topic_input', '')}` | hook: {row.get('hook', '')}")
    else:
        lines.append("- No weak AI concreteness flags.")
    lines.append("")
    lines.append("## Recommendations")
    if clusters:
        lines.append("- Increase candidate diversity when hook overlap exceeds 0.65 similarity.")
    if weak_ai_rows:
        lines.append("- Enforce stronger AI action+target coupling in fallback variants.")
    if contamination_rows:
        lines.append("- Add stricter storytelling contamination guard if any AI terms leak.")
    if not clusters and not weak_ai_rows and not contamination_rows:
        lines.append("- Current Judge v2 behavior is stable for this run.")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    try:
        topics = load_topics(args.topic_file)
    except Exception as exc:
        print(f"Failed to load topics: {exc}", file=sys.stderr)
        return 1

    reports_dir = Path("qa_reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = reports_dir / f"content_quality_{stamp}.csv"
    md_path = reports_dir / f"content_quality_{stamp}.md"

    rows: list[dict[str, Any]] = []
    prior_hooks: list[str] = []
    total_topics = len(topics)

    for idx, topic in enumerate(topics, start=1):
        print(f"[{idx}/{total_topics}] Running topic ({topic.lane}): {topic.topic}")
        try:
            row = run_topic(topic, prior_hooks=prior_hooks)
        except Exception as exc:
            row = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "lane": topic.lane,
                "topic_input": topic.topic,
                "batch_dir": "",
                "title": "",
                "hook": "",
                "sentence_1": "",
                "sentence_2": "",
                "sentence_3": "",
                "sentence_4": "",
                "caption": "",
                "cta": "",
                "live_model_used": "",
                "fallback_used": "",
                "selected_candidate": "",
                "candidate_1_total": "",
                "candidate_2_total": "",
                "candidate_3_total": "",
                "candidate_1_hook": "",
                "candidate_2_hook": "",
                "candidate_3_hook": "",
                "generic_hook_flag": "",
                "repetitive_hook_flag": "",
                "storytelling_contamination_flag": "",
                "ai_concreteness_flag": "",
                "provider_attempted": "",
                "selected_total": "",
                "command_exit_code": "",
                "error_message": str(exc),
            }

        rows.append(row)
        hook = str(row.get("hook", "")).strip()
        if hook:
            prior_hooks.append(hook)

    write_csv(csv_path, rows)
    clusters = build_repetition_clusters(rows)
    write_markdown(md_path, rows, clusters)

    total_run = len(rows)
    ai_values = [safe_float(row.get("selected_total")) for row in rows if row.get("lane") == "ai_productivity"]
    ai_values = [value for value in ai_values if value is not None]
    story_values = [safe_float(row.get("selected_total")) for row in rows if row.get("lane") == "storytelling"]
    story_values = [value for value in story_values if value is not None]

    avg_ai = (sum(ai_values) / len(ai_values)) if ai_values else 0.0
    avg_story = (sum(story_values) / len(story_values)) if story_values else 0.0

    generic_count = sum(1 for row in rows if row.get("generic_hook_flag") == "true")
    repetitive_count = sum(1 for row in rows if row.get("repetitive_hook_flag") == "true")
    story_contam_count = sum(1 for row in rows if row.get("storytelling_contamination_flag") == "true")
    weak_ai_count = sum(
        1 for row in rows if row.get("lane") == "ai_productivity" and row.get("ai_concreteness_flag") != "true"
    )

    success_count = sum(1 for row in rows if row.get("title") and row.get("hook") and row.get("sentence_1"))

    print("")
    print("=== Content QA Summary ===")
    print(f"CSV report: {csv_path}")
    print(f"Markdown summary: {md_path}")
    print(f"Total topics run: {total_run}")
    print(f"Avg selected score (AI): {avg_ai:.1f}")
    print(f"Avg selected score (storytelling): {avg_story:.1f}")
    print(f"Generic hooks: {generic_count}")
    print(f"Repetitive hooks: {repetitive_count}")
    print(f"Storytelling contamination failures: {story_contam_count}")
    print(f"Weak AI concreteness failures: {weak_ai_count}")
    print(f"Completed successfully: {success_count}/{total_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
