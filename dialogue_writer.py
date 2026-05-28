"""
Dialogue Writer - turns one AI-news item into a two-host dialogue script.

Replaces the ~6,000-line beat-assembler in script_generator.py. A 2026 model
writes a coherent short dialogue in one pass; there is nothing to "assemble" or
"arbitrate". Two original personas (Max the enthusiast, Iris the skeptic) give
the channel the persona + point-of-view that YouTube's 2026 inauthentic-content
policy requires.

Flow:
  topic dict -> Claude Sonnet 4.6 (dialogue prompt) -> {title, hook, turns}
                       |
                       v  accuracy check (2nd Claude pass)
                 claims supported by the source? hook honest?
                       |  fail -> regenerate once, then ship + warning
                       v
                 script dict  (narration + turns, for story_media_bridge.py)

Output contract: a script dict carrying both `turns` (the dialogue) and
`narration` (turns joined) so any legacy reader of `narration` still works.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger("RevenueMachine.DialogueWriter")

DIALOGUE_MODEL = "claude-sonnet-4-6"
GENERATION_MODE = "two_host_dialogue"
MAX_TURNS = 10
WORDS_PER_SECOND = 2.9  # observed ElevenLabs narration pace

# Two original personas. The channel identity lives here. `voice_id` values are
# ElevenLabs voices - swap them for deliberately chosen ones any time; both this
# module and story_media_bridge.py read speakers from this single config.
PERSONAS: dict[str, dict[str, str]] = {
    "max": {
        "name": "Max",
        "voice_id": "pNInz6obpgDQGcFmaJgB",  # Adam - energetic male (placeholder)
        "pov": (
            "the enthusiast. Genuinely excited about each AI release, leads with "
            "the news and what it unlocks. Fast, punchy, optimistic - but never a "
            "shill: concedes a real point when Iris lands one."
        ),
    },
    "iris": {
        "name": "Iris",
        "voice_id": "21m00Tcm4TlvDq8ikWAM",  # Rachel - sharp female (placeholder)
        "pov": (
            "the skeptic. Pokes holes - does it actually work, who does it hurt, "
            "what's the catch, is the benchmark cherry-picked. Dry and sharp, not "
            "cynical for its own sake: gives credit when something is genuinely good."
        ),
    },
}

_SYSTEM_PROMPT = (
    "You are the head writer for a faceless YouTube Shorts channel about AI news. "
    "Every video is a ~30-second dialogue between two recurring hosts. You write "
    "tight, real, human dialogue - not a press release, not hype, not a list."
)


def _personas_block() -> str:
    return "\n".join(
        f"- {p['name']} ({key}): {p['pov']}" for key, p in PERSONAS.items()
    )


def _build_dialogue_prompt(topic: dict[str, Any]) -> str:
    title = str(topic.get("title", "") or "").strip()
    summary = str(topic.get("summary", "") or "").strip()
    return (
        f"AI-news item to cover:\nTITLE: {title}\nSOURCE TEXT: {summary or '(headline only)'}\n\n"
        f"The two hosts:\n{_personas_block()}\n\n"
        "Write their dialogue about this item. Hard rules:\n"
        "- Open on Max with a concrete bold claim in the first line - a real "
        "3-second hook, never a setup sentence like 'today we're talking about'.\n"
        "- It is a real back-and-forth: Iris pushes back, Max responds. Not two "
        "monologues. Each turn reacts to the previous one.\n"
        "- Ground every factual claim in the SOURCE TEXT above. Do not invent "
        "numbers, features, or quotes. If the source is thin, stay general.\n"
        "- Land one clear takeaway by the end. Plain spoken words, short sentences.\n"
        f"- 6 to {MAX_TURNS} turns total, ~90 words total (it must read in ~30s).\n"
        "- No cliches ('game-changer', 'mind-blowing', 'the future is here').\n\n"
        "Return ONLY this JSON, no markdown fences:\n"
        '{"title": "<=60-char video title", '
        '"hook": "Max\'s exact first line", '
        '"turns": [{"speaker": "max", "text": "..."}, {"speaker": "iris", "text": "..."}]}'
    )


def _build_accuracy_prompt(topic: dict[str, Any], script: dict[str, Any]) -> str:
    summary = str(topic.get("summary", "") or "").strip()
    title = str(topic.get("title", "") or "").strip()
    dialogue = " ".join(t.get("text", "") for t in script.get("turns", []))
    return (
        "Two opinionated hosts discuss an AI-news item. Catch FABRICATED FACTS "
        "only - not opinions.\n\n"
        f"SOURCE TITLE: {title}\nSOURCE TEXT: {summary or '(headline only)'}\n\n"
        f"DIALOGUE: {dialogue}\n\n"
        f"HOOK: {script.get('hook', '')}\n\n"
        "A FABRICATED FACT is a concrete, checkable claim stated as fact that the "
        "source does not support: an invented event, number, statistic, quote, "
        "name, date, or biographical/career detail. Flag every one of those.\n"
        "Do NOT flag the hosts' opinions, predictions, characterizations, or value "
        "judgments ('big deal', 'impressive', 'this matters', 'best in the field', "
        "'a smart move'). Opinionated commentary is the format - it is allowed even "
        "though it is not stated in the source. Only invented FACTS count.\n"
        "Also check the hook is honest: the dialogue delivers what the hook promises.\n"
        'Return ONLY this JSON: {"accurate": true|false, "issues": ["<fabricated facts / dishonest hook only>"]}'
    )


def _extract_json(raw: str) -> dict[str, Any]:
    """Parses a JSON object from a model response, tolerating stray prose/fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _call_claude(client: Any, prompt: str, max_tokens: int) -> str:
    resp = client.messages.create(
        model=DIALOGUE_MODEL,
        max_tokens=max_tokens,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")


def _normalize_script(parsed: dict[str, Any], topic: dict[str, Any]) -> dict[str, Any]:
    """Validates and shapes a parsed model response into a script dict."""
    turns_raw = parsed.get("turns", [])
    if not isinstance(turns_raw, list) or not turns_raw:
        raise ValueError("model response had no turns")

    turns: list[dict[str, str]] = []
    for turn in turns_raw[:MAX_TURNS]:
        speaker = str(turn.get("speaker", "")).strip().lower()
        text = str(turn.get("text", "") or "").strip()
        if speaker not in PERSONAS or not text:
            continue
        turns.append({"speaker": speaker, "text": text})
    if len(turns) < 2:
        raise ValueError("model response had fewer than 2 valid turns")

    narration = " ".join(t["text"] for t in turns)
    word_count = len(narration.split())
    return {
        "title": str(parsed.get("title", "") or topic.get("title", "")).strip()[:80],
        "hook": str(parsed.get("hook", "") or turns[0]["text"]).strip(),
        "narration": narration,
        "turns": turns,
        "words": word_count,
        "estimated_duration": round(word_count / WORDS_PER_SECOND),
        "cta": "Follow for the AI news that actually matters.",
        "niche": "ai_tools",
        "live_generation_mode": GENERATION_MODE,
        "topic_url": str(topic.get("url", "") or ""),
        "topic_source": str(topic.get("source", "") or ""),
        "accuracy_warning": "",
        "is_fallback": False,
    }


def _check_accuracy(client: Any, topic: dict[str, Any], script: dict[str, Any]) -> tuple[bool, str]:
    """Returns (accurate, issue_text). Soft-passes on a check failure."""
    try:
        raw = _call_claude(client, _build_accuracy_prompt(topic, script), max_tokens=400)
        parsed = _extract_json(raw)
        if bool(parsed.get("accurate", False)):
            return True, ""
        issues = "; ".join(str(i) for i in parsed.get("issues", []) if i)
        return False, issues or "accuracy check flagged unsupported claims"
    except Exception as exc:
        # A broken check must not block a render - fail open, but say so.
        logger.warning("dialogue_writer: accuracy check errored (%s); passing soft.", exc)
        return True, "accuracy check could not run"


def _fallback_script(topic: dict[str, Any], reason: str) -> dict[str, Any]:
    """Minimal templated two-turn dialogue when the live model is unavailable."""
    title = str(topic.get("title", "") or "an AI update").strip()
    turns = [
        {"speaker": "max", "text": f"Big one today: {title}."},
        {"speaker": "iris", "text": "Worth a look, but I'd want to see it hold up before believing the headline."},
    ]
    narration = " ".join(t["text"] for t in turns)
    logger.warning("dialogue_writer: using fallback script (%s)", reason)
    return {
        "title": title[:80],
        "hook": turns[0]["text"],
        "narration": narration,
        "turns": turns,
        "words": len(narration.split()),
        "estimated_duration": round(len(narration.split()) / WORDS_PER_SECOND),
        "cta": "Follow for the AI news that actually matters.",
        "niche": "ai_tools",
        "live_generation_mode": "fallback_minimal",
        "topic_url": str(topic.get("url", "") or ""),
        "topic_source": str(topic.get("source", "") or ""),
        "accuracy_warning": f"fallback script used: {reason}",
        "is_fallback": True,
    }


def write_dialogue(topic: dict[str, Any]) -> dict[str, Any]:
    """Turns an AI-news topic dict into a two-host dialogue script dict.

    Resilience (per the eng-reviewed plan):
      - malformed model JSON  -> one retry -> minimal fallback script
      - accuracy check fails  -> regenerate once -> ship + accuracy_warning
      - never loops: at most 2 generation attempts total
    """
    try:
        import anthropic
    except ImportError:
        return _fallback_script(topic, "anthropic package unavailable")

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return _fallback_script(topic, "ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)
    prompt = _build_dialogue_prompt(topic)

    script: Optional[dict[str, Any]] = None
    last_error = ""
    for attempt in (1, 2):  # attempt 1 = normal; attempt 2 = retry after JSON or accuracy fail
        try:
            raw = _call_claude(client, prompt, max_tokens=900)
            candidate = _normalize_script(_extract_json(raw), topic)
        except Exception as exc:
            last_error = f"generation/parse failed: {exc}"
            logger.warning("dialogue_writer: attempt %d %s", attempt, last_error)
            continue

        accurate, issue = _check_accuracy(client, topic, candidate)
        if accurate:
            return candidate
        # Inaccurate: keep this candidate as a fallback, then regenerate once.
        last_error = f"accuracy: {issue}"
        if script is None:
            script = candidate
            script["accuracy_warning"] = issue
        logger.warning("dialogue_writer: attempt %d failed accuracy (%s)", attempt, issue)

    if script is not None:
        # Both attempts had accuracy concerns; ship the first draft with the
        # warning recorded rather than looping or hard-failing.
        logger.warning("dialogue_writer: shipping draft with accuracy_warning")
        return script
    return _fallback_script(topic, last_error or "unknown generation failure")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from topic_fetcher import fetch_fresh_topic

    item = fetch_fresh_topic(mark_seen=False)
    if not item:
        print("No fresh topic.")
        raise SystemExit(0)
    result = write_dialogue(item)
    print("TITLE:", result["title"])
    print("MODE:", result["live_generation_mode"], "| words:", result["words"],
          "| est:", result["estimated_duration"], "s")
    if result["accuracy_warning"]:
        print("ACCURACY WARNING:", result["accuracy_warning"])
    print("--- dialogue ---")
    for t in result["turns"]:
        print(f"  {PERSONAS[t['speaker']]['name']}: {t['text']}")
