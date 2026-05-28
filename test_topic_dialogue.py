"""
Unit tests for topic_fetcher.py and dialogue_writer.py (plan tasks T1, T2).

Pure-function tests only - no network, no API calls. Run with:
    .venv/bin/python3 test_topic_dialogue.py

Covers the eng-reviewed test set for T1/T2:
  - seen-topic dedup round-trip (the freshness/anti-ban guarantee)
  - placeholder-credential stripping
  - malformed-JSON tolerance in the model-response parser
  - script normalization rejecting bad model output
  - fallback script shape
The accuracy eval and the caption-offset/two-voice tests belong to T4/T5.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import topic_finder
import topic_fetcher
import dialogue_writer

_passed = 0
_failed = 0


def check(label: str, condition: bool) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


def expect_raises(label: str, fn) -> None:
    global _passed, _failed
    try:
        fn()
    except Exception:
        _passed += 1
        print(f"  ok   {label}")
        return
    _failed += 1
    print(f"  FAIL {label} (expected an exception)")


# ── T1: seen-topic dedup ────────────────────────────────────────────────
def test_dedup_round_trip() -> None:
    print("topic dedup round-trip:")
    with tempfile.TemporaryDirectory() as tmp:
        # Redirect the seen-topics log to a temp file so the real one is untouched.
        topic_finder.SEEN_TOPICS_FILE = Path(tmp) / "seen_topics.json"
        finder = topic_finder.TopicFinder(config={}, niche_config={})

        topic = {"title": "OpenAI ships GPT-9"}
        check("unseen topic is not seen initially", not finder._is_seen(topic))
        finder._mark_seen(topic)
        check("topic is seen after _mark_seen", finder._is_seen(topic))

        # A fresh finder reading the same log must still see it (persisted).
        finder2 = topic_finder.TopicFinder(config={}, niche_config={})
        check("dedup persists across finder instances", finder2._is_seen(topic))

        # A different topic is not falsely flagged.
        check("a different topic is still unseen",
              not finder2._is_seen({"title": "Anthropic ships Claude 9"}))

        # The log file is valid JSON of {hash: timestamp}.
        data = json.loads(topic_finder.SEEN_TOPICS_FILE.read_text())
        check("seen log is a non-empty dict", isinstance(data, dict) and len(data) == 1)


def test_placeholder_credentials_stripped() -> None:
    print("placeholder credential stripping:")
    import os
    saved = {k: os.environ.get(k) for k in ("REDDIT_CLIENT_ID", "REDDIT_SECRET", "YOUTUBE_API_KEY")}
    try:
        os.environ["REDDIT_CLIENT_ID"] = "YOUR_REDDIT_APP_ID"
        os.environ["REDDIT_SECRET"] = "YOUR_REDDIT_SECRET"
        os.environ["YOUTUBE_API_KEY"] = "YOUR_YOUTUBE_API_KEY_HERE"
        cfg = topic_fetcher._build_config()
        check("placeholder Reddit id stripped to empty", cfg["REDDIT_CLIENT_ID"] == "")
        check("placeholder Reddit secret stripped to empty", cfg["REDDIT_SECRET"] == "")
        check("placeholder YouTube key stripped to empty", cfg["YOUTUBE_API_KEY"] == "")
        os.environ["REDDIT_CLIENT_ID"] = "real_id_abc123"
        check("a real Reddit id is kept",
              topic_fetcher._build_config()["REDDIT_CLIENT_ID"] == "real_id_abc123")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── T2: malformed-JSON tolerance + normalization ────────────────────────
def test_extract_json() -> None:
    print("model-response JSON parsing:")
    raw = '{"title": "X", "turns": []}'
    check("parses raw JSON", dialogue_writer._extract_json(raw)["title"] == "X")

    fenced = '```json\n{"title": "Y", "turns": []}\n```'
    check("parses fenced JSON", dialogue_writer._extract_json(fenced)["title"] == "Y")

    prosey = 'Here is the script:\n{"title": "Z", "turns": []}\nHope that helps!'
    check("parses JSON wrapped in prose", dialogue_writer._extract_json(prosey)["title"] == "Z")

    expect_raises("raises on non-JSON garbage",
                  lambda: dialogue_writer._extract_json("not json at all"))


def test_normalize_script() -> None:
    print("script normalization:")
    topic = {"title": "T", "url": "u", "source": "rss"}

    good = {"title": "Good", "hook": "hook line",
            "turns": [{"speaker": "max", "text": "one"},
                      {"speaker": "iris", "text": "two"}]}
    script = dialogue_writer._normalize_script(good, topic)
    check("valid turns accepted", len(script["turns"]) == 2)
    check("narration is turns joined", script["narration"] == "one two")
    check("word count computed", script["words"] == 2)
    check("estimated_duration computed", script["estimated_duration"] >= 0)
    check("not flagged as fallback", script["is_fallback"] is False)

    bad_speaker = {"turns": [{"speaker": "max", "text": "hi"},
                             {"speaker": "peter", "text": "drop me"},
                             {"speaker": "iris", "text": "bye"}]}
    s2 = dialogue_writer._normalize_script(bad_speaker, topic)
    check("unknown speaker dropped", len(s2["turns"]) == 2)

    expect_raises("rejects fewer than 2 valid turns",
                  lambda: dialogue_writer._normalize_script(
                      {"turns": [{"speaker": "max", "text": "only one"}]}, topic))
    expect_raises("rejects empty turns",
                  lambda: dialogue_writer._normalize_script({"turns": []}, topic))


def test_fallback_script() -> None:
    print("fallback script:")
    fb = dialogue_writer._fallback_script({"title": "Some AI thing", "url": "u"}, "test reason")
    check("fallback is flagged is_fallback", fb["is_fallback"] is True)
    check("fallback has >= 2 turns", len(fb["turns"]) >= 2)
    check("fallback records the reason", "test reason" in fb["accuracy_warning"])
    check("fallback narration non-empty", len(fb["narration"]) > 0)
    check("fallback speakers are valid personas",
          all(t["speaker"] in dialogue_writer.PERSONAS for t in fb["turns"]))


if __name__ == "__main__":
    test_dedup_round_trip()
    test_placeholder_credentials_stripped()
    test_extract_json()
    test_normalize_script()
    test_fallback_script()
    print(f"\n{_passed} passed, {_failed} failed")
    raise SystemExit(1 if _failed else 0)
