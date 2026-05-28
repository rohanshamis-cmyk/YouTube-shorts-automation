"""
Script Generator - Uses Claude/GPT to write viral YouTube Shorts scripts.

Follows the proven structure:
  Hook (0-3s) → Problem/Curiosity (3-15s) → Value Delivery (15-45s) → CTA (45-59s)

Optimized for:
  - Retention hooks that beat algorithm
  - Natural speech patterns for AI voiceover
  - Scene descriptions for stock footage search
  - SEO-optimized metadata
"""

import os
import re
import json
import logging
import anthropic
from difflib import SequenceMatcher
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger("RevenueMachine.ScriptGenerator")

LIVE_GENERATION_MODE_POLISH = "polish_from_fallback_preserve_anchors"
LIVE_GENERATION_MODE_INDEPENDENT = "independent_from_topic"


SYSTEM_PROMPT = """You are an expert YouTube Shorts writer focused on retention and scroll-stopping openings.

Your script structure:
1. Hook (0-3s): aggressive pattern interrupt, curiosity, urgency, surprise, or tension.
2. Bridge (3-10s): make one promise and force watch-through.
3. Value (10-50s): tight, spoken, concrete.
4. CTA (last 5s): one clear action.

Hook quality requirements:
- The first 8-12 words must feel like a real Shorts hook said out loud.
- Avoid safe/generic intros, blog voice, and listicle phrasing.
- NEVER start with: "This...", "Here are...", "Imagine...", "In this video..."
- Prefer human creator tone: direct, blunt, opinionated when relevant.

Style requirements:
- Write for speech, not reading. Short lines. Low-friction words.
- Most sentences should be under 12 words.
- Use tension loops ("wait", "here's the part nobody says", "this is where it flips").
- No corporate explainer tone.

Output requirements:
- Return ONLY valid JSON with the requested fields.
- Do not include markdown fences, commentary, or extra keys."""

JUDGE_SYSTEM_PROMPT = """You are a strict suspense Shorts script judge.
Choose the stronger script for publishable short-form content.

Return ONLY valid JSON with this shape:
{
  "winner": "live" | "fallback",
  "winner_reason": "one short sentence",
  "judge_summary": "short summary",
  "scores": {
    "live": {
      "hook_strength": 0-10,
      "reveal_clarity": 0-10,
      "ending_consequence": 0-10,
      "non_repetition": 0-10,
      "line_quality": 0-10,
      "topic_faithfulness": 0-10,
      "planner_friendliness": 0-10,
      "publishability": 0-10,
      "total": 0-100
    },
    "fallback": {
      "hook_strength": 0-10,
      "reveal_clarity": 0-10,
      "ending_consequence": 0-10,
      "non_repetition": 0-10,
      "line_quality": 0-10,
      "topic_faithfulness": 0-10,
      "planner_friendliness": 0-10,
      "publishability": 0-10,
      "total": 0-100
    }
  }
}

Rules:
- This is SUSPENSE short-form. Weight hook_strength, reveal_clarity, and
  ending_consequence most heavily - they decide retention.
- Reward a hook that lands a concrete unsettling detail in the first 3 seconds
  over a setup sentence or vague mood.
- Reward exactly one specific impossible detail/anomaly over diffuse atmosphere.
- Reward escalating tension and an ending the narrator actually lives with;
  penalize flat, mood-only writing that never raises the stakes.
- Penalize clipped, broken, repetitive, or awkward lines.
- Penalize horror cliches ("little did I know", "to my horror", "sent chills").
- Penalize scripts that drift away from the topic.
- Judge effectiveness, not politeness: do NOT prefer a safer, blander, more
  generic script over a genuinely tense one.
- Pick fallback if live is weaker or broken.
"""


def _parse_bool(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_csv_list(value: str | None) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    values: list[str] = []
    for part in raw.split(","):
        item = str(part or "").strip()
        if item:
            values.append(item)
    return values


ANGLE_ORDER = [
    "beginner tip",
    "mistake to avoid",
    "tool recommendation",
    "quick workflow",
    "time-saving trick",
]

FALLBACK_TEMPLATE_POOLS = {
    "ai_tools": {
        "scene_description": "Person testing AI tools on laptop with task list on desk",
        "scene_query": "ai tools laptop workflow",
        "angles": {
            "beginner tip": [
                {
                    "hook": "New to AI tools? Start with this one rule.",
                    "on_screen_hook": "START AI RIGHT",
                    "thumbnail_text": "AI START RULE",
                    "line1": "Pick one AI tool and one repeat task only.",
                    "line2": "Write a clear prompt template before your first run.",
                    "caption": "Beginner AI setup that avoids overwhelm.",
                    "cta": "Save this and follow for practical AI tool setups.",
                },
                {
                    "hook": "If AI tools feel random, use this setup first.",
                    "on_screen_hook": "FIX AI CHAOS",
                    "thumbnail_text": "STOP AI CHAOS",
                    "line1": "Use AI for one narrow task, like summarizing meeting notes.",
                    "line2": "Keep a small prompt library so your results stay consistent.",
                    "caption": "Simple AI starter flow for reliable output.",
                    "cta": "Try this flow tonight and follow for the next AI tip.",
                },
            ],
            "mistake to avoid": [
                {
                    "hook": "Big AI mistake: copying output without a review pass.",
                    "on_screen_hook": "DON'T SKIP REVIEW",
                    "thumbnail_text": "CHECK OUTPUT",
                    "line1": "Always run a 60-second quality check before you ship.",
                    "line2": "Ask AI to list risks, then edit with your own judgment.",
                    "caption": "Avoid the common AI output quality trap.",
                    "cta": "Save this checklist and follow for safer AI workflows.",
                },
                {
                    "hook": "Most people waste AI by giving vague prompts.",
                    "on_screen_hook": "VAGUE = BAD",
                    "thumbnail_text": "PROMPT BETTER",
                    "line1": "Bad prompt in means noisy output out.",
                    "line2": "Add role, goal, and format in one clear sentence.",
                    "caption": "Fix this prompt mistake to get better AI results.",
                    "cta": "Use this today and follow for more AI prompt upgrades.",
                },
            ],
            "tool recommendation": [
                {
                    "hook": "Use this two-tool AI stack for faster daily work.",
                    "on_screen_hook": "2-TOOL STACK",
                    "thumbnail_text": "USE 2 TOOLS",
                    "line1": "Tool one captures raw ideas, tool two turns them into polished output.",
                    "line2": "That split keeps you fast without losing quality.",
                    "caption": "Practical two-tool AI stack for creators and students.",
                    "cta": "Follow for more AI stack recommendations that are easy to copy.",
                },
                {
                    "hook": "One AI tool for drafts, one for cleanup. That's the play.",
                    "on_screen_hook": "DRAFT + CLEANUP",
                    "thumbnail_text": "STACK THIS",
                    "line1": "Generate first draft fast, then run a cleanup pass for clarity.",
                    "line2": "You get speed and structure without starting over.",
                    "caption": "AI tool pairing that improves speed and quality.",
                    "cta": "Save this stack and follow for the next tool combo.",
                },
            ],
            "quick workflow": [
                {
                    "hook": "This 3-step AI workflow saves at least 20 minutes.",
                    "on_screen_hook": "3-STEP AI FLOW",
                    "thumbnail_text": "3 STEP FLOW",
                    "line1": "Step one: dump your notes. Step two: ask for a clean outline.",
                    "line2": "Step three: request a short action checklist and execute it.",
                    "caption": "Fast 3-step AI workflow for daily execution.",
                    "cta": "Try this workflow today and follow for more fast systems.",
                },
                {
                    "hook": "Run this AI workflow when you're short on time.",
                    "on_screen_hook": "FAST AI PROCESS",
                    "thumbnail_text": "RUN THIS FLOW",
                    "line1": "Collect messy inputs, summarize to key points, then extract top actions.",
                    "line2": "Keep each step to two minutes so you stay moving.",
                    "caption": "Quick AI process for busy schedules.",
                    "cta": "Save this process and follow for more short workflows.",
                },
            ],
            "time-saving trick": [
                {
                    "hook": "Time-saving AI trick: reuse one prompt skeleton daily.",
                    "on_screen_hook": "REUSE PROMPTS",
                    "thumbnail_text": "SAVE 20 MIN",
                    "line1": "Store one prompt format with slots you can swap in seconds.",
                    "line2": "You skip setup time and get cleaner outputs every day.",
                    "caption": "Prompt reuse trick that saves time every session.",
                    "cta": "Follow for more practical AI shortcuts.",
                },
                {
                    "hook": "Want faster AI results? Build one reusable prompt card.",
                    "on_screen_hook": "PROMPT CARD",
                    "thumbnail_text": "FASTER AI NOW",
                    "line1": "Keep your role, output style, and constraints in one template.",
                    "line2": "Paste and run instead of rewriting prompts from scratch.",
                    "caption": "Reusable prompt card trick for faster AI output.",
                    "cta": "Save this trick and follow for the next speed boost.",
                },
            ],
        },
    },
    "student_productivity": {
        "scene_description": "Student planning study blocks with laptop, notebook, and timer",
        "scene_query": "student productivity study planning",
        "angles": {
            "beginner tip": [
                {
                    "hook": "Beginner productivity tip: plan only your next 2 hours.",
                    "on_screen_hook": "PLAN 2 HOURS",
                    "thumbnail_text": "2-HOUR PLAN",
                    "line1": "Write three tasks max and rank them by deadline impact.",
                    "line2": "Start the smallest high-impact task first to build momentum.",
                    "caption": "Simple student planning method that actually sticks.",
                    "cta": "Try this today and follow for student productivity systems.",
                },
                {
                    "hook": "If your study day feels messy, start with this.",
                    "on_screen_hook": "FIX YOUR DAY",
                    "thumbnail_text": "RESET TODAY",
                    "line1": "Set one clear outcome for this session before opening apps.",
                    "line2": "Then time-block 25 minutes with one specific target.",
                    "caption": "Easy daily reset for students with packed schedules.",
                    "cta": "Save this reset and follow for practical study routines.",
                },
            ],
            "mistake to avoid": [
                {
                    "hook": "Student mistake: making a to-do list with no time blocks.",
                    "on_screen_hook": "BLOCK YOUR TIME",
                    "thumbnail_text": "STOP LIST ONLY",
                    "line1": "A task list without schedule slots usually gets delayed.",
                    "line2": "Attach each task to a clock time before your day starts.",
                    "caption": "Avoid this planning mistake to finish more work.",
                    "cta": "Use this fix today and follow for better study execution.",
                },
                {
                    "hook": "You're not lazy. Your task list is too vague.",
                    "on_screen_hook": "BE SPECIFIC",
                    "thumbnail_text": "TASKS TOO VAGUE",
                    "line1": "Replace 'study chemistry' with 'review chapter 4 questions 1-10'.",
                    "line2": "Specific tasks reduce friction and increase completion.",
                    "caption": "The productivity mistake most students miss.",
                    "cta": "Save this and follow for no-fluff student productivity tips.",
                },
            ],
            "tool recommendation": [
                {
                    "hook": "Use this student productivity stack for cleaner study days.",
                    "on_screen_hook": "STUDENT STACK",
                    "thumbnail_text": "USE THIS STACK",
                    "line1": "One app for tasks, one timer app, one notes app.",
                    "line2": "Keep each tool single-purpose so your system stays simple.",
                    "caption": "Student tool stack for focus and execution.",
                    "cta": "Follow for more tool setups that save time.",
                },
                {
                    "hook": "Best student setup: timer + checklist + weekly review.",
                    "on_screen_hook": "3 TOOL SETUP",
                    "thumbnail_text": "BEST STUDENT SETUP",
                    "line1": "Track daily tasks in one list and run focused timer blocks.",
                    "line2": "End each week with a short review to reset priorities.",
                    "caption": "Simple productivity setup for students.",
                    "cta": "Save this setup and follow for the next improvement.",
                },
            ],
            "quick workflow": [
                {
                    "hook": "Quick workflow: plan, sprint, review in 30 minutes.",
                    "on_screen_hook": "PLAN SPRINT REVIEW",
                    "thumbnail_text": "30-MIN FLOW",
                    "line1": "Five minutes plan, twenty minutes focused work, five minutes review.",
                    "line2": "Repeat this cycle and your output climbs fast.",
                    "caption": "30-minute productivity loop for students.",
                    "cta": "Try one loop now and follow for more fast workflows.",
                },
                {
                    "hook": "Use this class-day workflow to stay ahead.",
                    "on_screen_hook": "CLASS-DAY FLOW",
                    "thumbnail_text": "STAY AHEAD",
                    "line1": "Capture tasks after class, prioritize top two, then run one focus sprint.",
                    "line2": "Close with a quick tomorrow plan before you stop.",
                    "caption": "Class-day workflow for consistent progress.",
                    "cta": "Save this flow and follow for better student systems.",
                },
            ],
            "time-saving trick": [
                {
                    "hook": "Time-saving trick: batch similar tasks in one block.",
                    "on_screen_hook": "BATCH TASKS",
                    "thumbnail_text": "SAVE STUDY TIME",
                    "line1": "Group readings together, then group practice questions together.",
                    "line2": "You reduce context-switch cost and finish faster.",
                    "caption": "Batching trick to save time during study sessions.",
                    "cta": "Use this trick tonight and follow for more time savers.",
                },
                {
                    "hook": "Stop switching subjects every 10 minutes.",
                    "on_screen_hook": "STOP SWITCHING",
                    "thumbnail_text": "LESS SWITCHING",
                    "line1": "Run one deep block per subject before moving to the next.",
                    "line2": "You keep focus high and retain more with less effort.",
                    "caption": "Simple focus trick to save study time.",
                    "cta": "Save this and follow for student efficiency tips.",
                },
            ],
        },
    },
    "study_hacks": {
        "scene_description": "Student using notebook and AI assistant to prepare exam notes",
        "scene_query": "study hacks exam prep ai",
        "angles": {
            "beginner tip": [
                {
                    "hook": "Beginner study hack: turn one chapter into 10 flash prompts.",
                    "on_screen_hook": "START WITH 10",
                    "thumbnail_text": "10 FLASH PROMPTS",
                    "line1": "Summarize one chapter, then generate ten recall questions.",
                    "line2": "Practice answering without looking to train active memory.",
                    "caption": "Beginner study hack you can use today.",
                    "cta": "Try this tonight and follow for more study hacks.",
                },
                {
                    "hook": "If you forget fast, use this first study hack.",
                    "on_screen_hook": "REMEMBER MORE",
                    "thumbnail_text": "USE THIS FIRST",
                    "line1": "Read once, close notes, then explain the topic in plain words.",
                    "line2": "Check gaps and repeat only weak sections.",
                    "caption": "Starter study method for stronger recall.",
                    "cta": "Save this and follow for practical exam prep tricks.",
                },
            ],
            "mistake to avoid": [
                {
                    "hook": "Study mistake: rereading notes without testing yourself.",
                    "on_screen_hook": "STOP REREADING",
                    "thumbnail_text": "TEST YOURSELF",
                    "line1": "Passive rereading feels productive but fades fast.",
                    "line2": "Swap one reread block for active recall questions.",
                    "caption": "Avoid this study trap before your next exam.",
                    "cta": "Save this reminder and follow for better study methods.",
                },
                {
                    "hook": "Most exam prep fails because review is too late.",
                    "on_screen_hook": "REVIEW EARLIER",
                    "thumbnail_text": "DON'T WAIT",
                    "line1": "Start short daily review blocks instead of one late marathon.",
                    "line2": "Spaced repetition beats last-minute cramming every time.",
                    "caption": "Common exam prep mistake and the easy fix.",
                    "cta": "Use this fix now and follow for more study strategy tips.",
                },
            ],
            "tool recommendation": [
                {
                    "hook": "Use this AI + flashcard combo for exam prep.",
                    "on_screen_hook": "AI + FLASHCARDS",
                    "thumbnail_text": "BEST COMBO",
                    "line1": "Ask AI for key points, then convert them into recall cards.",
                    "line2": "Review cards in short sessions across the week.",
                    "caption": "Study tool combo that improves retention.",
                    "cta": "Follow for more tool combos for faster learning.",
                },
                {
                    "hook": "One tool for summaries, one for recall. That's it.",
                    "on_screen_hook": "SUMMARY + RECALL",
                    "thumbnail_text": "2 TOOL METHOD",
                    "line1": "Generate concise notes first, then train memory with question cards.",
                    "line2": "Keep the stack simple so you actually use it daily.",
                    "caption": "Simple study stack for exams and quizzes.",
                    "cta": "Save this stack and follow for the next study setup.",
                },
            ],
            "quick workflow": [
                {
                    "hook": "Quick study workflow: summarize, quiz, teach.",
                    "on_screen_hook": "3-STEP STUDY FLOW",
                    "thumbnail_text": "SUMMARIZE QUIZ TEACH",
                    "line1": "Summarize topic in bullet points, quiz yourself, then explain aloud.",
                    "line2": "Teaching reveals weak spots in minutes.",
                    "caption": "Fast study workflow for stronger recall.",
                    "cta": "Try this workflow today and follow for more study wins.",
                },
                {
                    "hook": "Use this 25-minute study loop before every class.",
                    "on_screen_hook": "25-MIN LOOP",
                    "thumbnail_text": "DO THIS DAILY",
                    "line1": "Ten minutes review, ten minutes questions, five minutes recap.",
                    "line2": "Repeat across topics for steady progress.",
                    "caption": "Daily 25-minute loop for exam prep.",
                    "cta": "Save this loop and follow for more quick workflows.",
                },
            ],
            "time-saving trick": [
                {
                    "hook": "Time-saving study trick: prebuild your question bank once.",
                    "on_screen_hook": "BUILD ONCE",
                    "thumbnail_text": "SAVE HOURS",
                    "line1": "Create a reusable bank of likely exam questions per chapter.",
                    "line2": "Then run short daily recall sessions instead of full rewrites.",
                    "caption": "Question-bank trick that saves serious study time.",
                    "cta": "Follow for more time-saving study systems.",
                },
                {
                    "hook": "Save time by reviewing only weak-topic cards first.",
                    "on_screen_hook": "FOCUS WEAK TOPICS",
                    "thumbnail_text": "PRIORITIZE WEAK",
                    "line1": "Tag each card easy, medium, or hard after each session.",
                    "line2": "Spend most time on hard cards until they become easy.",
                    "caption": "Targeted review trick for faster exam prep.",
                    "cta": "Use this tonight and follow for more exam prep shortcuts.",
                },
            ],
        },
    },
}


class ScriptGenerator:
    def __init__(self, config: dict, niche_config: dict):
        self.config = config
        self.niche_config = niche_config

        self.openai_api_key = str(config.get("OPENAI_API_KEY", "") or "").strip()
        self.script_openai_api_key = (
            str(config.get("SCRIPT_OPENAI_API_KEY", "") or "").strip()
            or os.getenv("SCRIPT_OPENAI_API_KEY", "").strip()
            or self.openai_api_key
        )
        self.script_openai_base_url = (
            str(config.get("SCRIPT_OPENAI_BASE_URL", "") or "").strip()
            or os.getenv("SCRIPT_OPENAI_BASE_URL", "").strip()
        )
        self.openrouter_api_key = (
            str(config.get("OPENROUTER_API_KEY", "") or "").strip()
            or os.getenv("OPENROUTER_API_KEY", "").strip()
        )
        self.openrouter_model = (
            str(config.get("OPENROUTER_MODEL", "") or "").strip()
            or os.getenv("OPENROUTER_MODEL", "").strip()
            or "openai/gpt-4o-mini"
        )
        self.openrouter_base_url = (
            str(config.get("OPENROUTER_BASE_URL", "") or "").strip()
            or os.getenv("OPENROUTER_BASE_URL", "").strip()
            or "https://openrouter.ai/api/v1"
        )
        self.anthropic_api_key = str(config.get("ANTHROPIC_API_KEY", "") or "").strip()
        self.script_provider = (
            str(config.get("SCRIPT_PROVIDER", "") or "").strip().lower()
            or os.getenv("SCRIPT_PROVIDER", "").strip().lower()
        )
        self.explicit_script_openai_model = (
            str(config.get("SCRIPT_OPENAI_MODEL", "") or "").strip()
            or os.getenv("SCRIPT_OPENAI_MODEL", "").strip()
        )
        self.script_writer_model = (
            str(config.get("SCRIPT_WRITER_MODEL", "") or "").strip()
            or os.getenv("SCRIPT_WRITER_MODEL", "").strip()
        )
        self.script_judge_model = (
            str(config.get("SCRIPT_JUDGE_MODEL", "") or "").strip()
            or os.getenv("SCRIPT_JUDGE_MODEL", "").strip()
        )
        self.script_judge_enabled = _parse_bool(
            str(config.get("SCRIPT_JUDGE_ENABLED", "") or "").strip()
            or os.getenv("SCRIPT_JUDGE_ENABLED", "").strip(),
            default=True,
        )
        self.script_live_rewrite_enabled = _parse_bool(
            str(config.get("SCRIPT_LIVE_REWRITE_ENABLED", "") or "").strip()
            or os.getenv("SCRIPT_LIVE_REWRITE_ENABLED", "").strip(),
            default=False,
        )
        # Live generation mode. "independent" writes a fresh suspense story
        # from the topic alone; "polish" keeps the legacy anchor-preserving
        # rewrite of the fallback. CLAUDE.md priority 1: the polish/rewrite
        # mode only paraphrases the fallback and lost to it on every benchmark
        # topic, so independent is the default.
        mode_raw = (
            str(config.get("SCRIPT_LIVE_GENERATION_MODE", "") or "").strip().lower()
            or os.getenv("SCRIPT_LIVE_GENERATION_MODE", "").strip().lower()
        )
        self.script_live_generation_mode = (
            LIVE_GENERATION_MODE_POLISH
            if mode_raw in {"polish", "rewrite", LIVE_GENERATION_MODE_POLISH}
            else LIVE_GENERATION_MODE_INDEPENDENT
        )
        self.approved_writer_models_raw = (
            str(config.get("APPROVED_WRITER_MODELS", "") or "").strip()
            or os.getenv("APPROVED_WRITER_MODELS", "").strip()
        )
        self.approved_writer_models = _parse_csv_list(self.approved_writer_models_raw)
        self.openai_model = self.explicit_script_openai_model or "gpt-4o-mini"
        self.explicit_script_anthropic_model = (
            str(config.get("SCRIPT_ANTHROPIC_MODEL", "") or "").strip()
            or os.getenv("SCRIPT_ANTHROPIC_MODEL", "").strip()
        )
        self.anthropic_model = self.explicit_script_anthropic_model or "claude-opus-4-5"
        self.anthropic_base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()

        # Initialize Anthropic client only when a key exists.
        self.client = (
            anthropic.Anthropic(api_key=self.anthropic_api_key)
            if self.anthropic_api_key
            else None
        )

    def generate(self, topic: dict, duration_target: int = 45, style: str = "hook_then_value") -> dict:
        """
        Generates a complete short script with all metadata.
        Returns a rich dict with script, scenes, metadata.
        """
        logger.info(f"✍️  Generating script for: {topic['title']}")

        content_lane = self._detect_content_lane(topic)
        topic_is_ai = self._topic_mentions_ai(topic)
        hook_examples = "\n".join(
            self._lane_hook_examples(
                lane=content_lane,
                default_examples=self.niche_config.get("hook_templates", [])[:3],
            )
        )
        lane_hook_guidance = self._lane_hook_guidance(content_lane)
        lane_body_guidance = self._lane_body_guidance(content_lane, topic_is_ai=topic_is_ai)

        prompt = f"""Create a viral YouTube Short script for this topic: "{topic['title']}"

Default repo niche (secondary context): {self.config.get('NICHE', 'ai_tools')}
Primary lane (topic-derived, highest priority): {content_lane}
Target duration: {duration_target} seconds
Style: {style}

Hook inspiration (do not copy, just use as style reference):
{hook_examples}

Lane-specific hook guidance:
{lane_hook_guidance}

Lane-specific narration/content guidance (apply across full script):
{lane_body_guidance}

Additional context about this topic:
{topic.get('summary', 'No additional context available.')}

Return this exact JSON structure:
{{
    "title": "SEO-optimized video title (under 60 chars, must include primary keyword)",
    "hook": "First 1-2 sentences that stop the scroll (under 20 words total)",
    "narration": "Full narration text (no stage directions, just spoken words, 80-120 words)",
    "scenes": [
        {{
            "timestamp_start": 0,
            "timestamp_end": 8,
            "description": "What's happening on screen",
            "stock_query": "3-4 word query for stock footage search"
        }}
    ],
    "estimated_duration": 47,
    "words": 95,
    "visual_queries": ["query1", "query2", "query3", "query4", "query5"],
    "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8"],
    "description_snippet": "First 125 chars of video description (shown before 'more' fold)",
    "cta": "Follow for more [niche] content"
}}

The narration MUST:
- Start with the hook immediately (no intro)
- Use short punchy spoken sentences (mostly under 12 words)
- Have natural speech rhythm (read it aloud as a test)
- Build genuine curiosity or deliver real value
- Avoid generic first lines like "This...", "Here are...", or "Imagine..."
- Follow the PRIMARY lane above across hook + full body, even if default niche differs """

        fallback_script = self._get_fallback_script(
            topic=topic,
            lane=content_lane,
            topic_is_ai=topic_is_ai,
            log_warning=False,
        )
        fallback_script["is_fallback"] = True
        live_generation_mode = self.script_live_generation_mode
        live_rewrite_mode = live_generation_mode == LIVE_GENERATION_MODE_POLISH
        if live_rewrite_mode:
            live_prompt = self._build_live_polish_prompt(
                base_prompt=prompt,
                topic=topic,
                fallback_script=fallback_script,
                lane=content_lane,
                topic_is_ai=topic_is_ai,
            )
        else:
            live_prompt = self._build_live_independent_prompt(
                base_prompt=prompt,
                topic=topic,
                lane=content_lane,
                topic_is_ai=topic_is_ai,
            )

        provider_order = self._resolve_provider_order()
        live_rewrite_gate = self._evaluate_live_rewrite_gate(
            provider_order=provider_order,
            lane=content_lane,
        )
        if bool(live_rewrite_gate.get("skip")):
            reason = str(live_rewrite_gate.get("reason", "") or "").strip()
            live_provider = str(live_rewrite_gate.get("provider", "") or "").strip()
            live_model = str(live_rewrite_gate.get("model", "") or "").strip()
            fallback_total = float(
                self._score_script_candidate(
                    script=fallback_script,
                    topic=topic,
                    lane=content_lane,
                    topic_is_ai=topic_is_ai,
                ).get("total", 0.0)
            )
            judge_payload = {
                "winner": "fallback",
                "winner_reason": reason,
                "judge_summary": "Live rewrite skipped by production gate; fallback selected.",
                "scores": {
                    "live_total": 0.0,
                    "fallback_total": fallback_total,
                },
                "judge_model": "none",
                "live_rewrite_skipped": True,
                "live_rewrite_skip_reason": reason,
                "live_rewrite_noop": False,
                "live_rewrite_noop_reason": "",
                "live_rewrite_anchor_coverage_score": None,
                "live_rewrite_anchor_loss": False,
                "live_rewrite_anchor_loss_reason": "",
            }
            logger.info("Live rewrite skipped by gate: %s", reason)
            logger.warning("Script arbitration winner: fallback (%s)", reason)
            return self._apply_arbitration_metadata(
                winner_script=fallback_script,
                winner="fallback",
                winner_reason=reason,
                live_provider=live_provider,
                live_model=live_model,
                judge_model="none",
                judge_summary="Live rewrite skipped by production gate; fallback selected.",
                scores=judge_payload.get("scores", {}),
                live_candidate={},
                fallback_candidate=self._script_candidate_payload(fallback_script),
                judge_result=judge_payload,
                live_generation_mode=live_generation_mode,
                live_rewrite_skipped=True,
                live_rewrite_skip_reason=reason,
                live_rewrite_noop=False,
                live_rewrite_similarity_score=None,
                live_rewrite_noop_reason="",
                live_rewrite_anchor_coverage_score=None,
                live_rewrite_anchor_loss=False,
                live_rewrite_anchor_loss_reason="",
            )
        live_script, live_meta = self._generate_best_live_script(
            provider_order=provider_order,
            prompt=live_prompt,
            topic=topic,
            lane=content_lane,
            topic_is_ai=topic_is_ai,
            rewrite_mode=live_rewrite_mode,
        )
        live_provider = str(live_meta.get("provider", "") or "").strip()
        live_model = str(live_meta.get("model", "") or "").strip()
        live_error = str(live_meta.get("error", "") or "").strip()

        if not live_script:
            reason = live_error or "No live script candidate available."
            fallback_total = float(
                self._score_script_candidate(
                    script=fallback_script,
                    topic=topic,
                    lane=content_lane,
                    topic_is_ai=topic_is_ai,
                ).get("total", 0.0)
            )
            judge_payload = {
                "winner": "fallback",
                "winner_reason": reason,
                "judge_summary": "Live draft unavailable; fallback selected.",
                "scores": {
                    "live_total": 0.0,
                    "fallback_total": fallback_total,
                },
                "judge_model": "none",
                "live_rewrite_noop": True,
                "live_rewrite_noop_reason": "No live rewrite candidate generated.",
                "live_rewrite_anchor_coverage_score": 0.0,
                "live_rewrite_anchor_loss": True,
                "live_rewrite_anchor_loss_reason": "No live rewrite candidate generated.",
            }
            logger.warning("Script arbitration winner: fallback (%s)", reason)
            return self._apply_arbitration_metadata(
                winner_script=fallback_script,
                winner="fallback",
                winner_reason=reason,
                live_provider=live_provider,
                live_model=live_model,
                judge_model="none",
                judge_summary="Live draft unavailable; fallback selected.",
                scores=judge_payload.get("scores", {}),
                live_candidate={},
                fallback_candidate=self._script_candidate_payload(fallback_script),
                judge_result=judge_payload,
                live_generation_mode=live_generation_mode,
                live_rewrite_noop=True,
                live_rewrite_similarity_score=1.0,
                live_rewrite_noop_reason="No live rewrite candidate generated.",
                live_rewrite_anchor_coverage_score=0.0,
                live_rewrite_anchor_loss=True,
                live_rewrite_anchor_loss_reason="No live rewrite candidate generated.",
            )

        live_script["is_fallback"] = False
        noop_diagnostics = self._assess_live_rewrite_noop(
            live_script=live_script,
            fallback_script=fallback_script,
        )
        anchor_diagnostics = self._assess_live_rewrite_anchor_coverage(
            live_script=live_script,
            fallback_script=fallback_script,
        )
        rewrite_diagnostics = {**noop_diagnostics, **anchor_diagnostics}
        logger.info(
            "Live rewrite no-op check: noop=%s similarity=%.3f reason=%s",
            "yes" if bool(rewrite_diagnostics.get("live_rewrite_noop")) else "no",
            float(rewrite_diagnostics.get("live_rewrite_similarity_score", 0.0)),
            str(rewrite_diagnostics.get("live_rewrite_noop_reason", "") or "none"),
        )
        logger.info(
            "Live rewrite anchor check: loss=%s coverage=%.3f reason=%s",
            "yes" if bool(rewrite_diagnostics.get("live_rewrite_anchor_loss")) else "no",
            float(rewrite_diagnostics.get("live_rewrite_anchor_coverage_score", 0.0)),
            str(rewrite_diagnostics.get("live_rewrite_anchor_loss_reason", "") or "none"),
        )
        arbitration = self._arbitrate_live_vs_fallback(
            topic=topic,
            lane=content_lane,
            topic_is_ai=topic_is_ai,
            live_script=live_script,
            fallback_script=fallback_script,
            live_provider=live_provider,
            live_model=live_model,
            live_rewrite_noop=bool(rewrite_diagnostics.get("live_rewrite_noop")),
            live_rewrite_noop_reason=str(rewrite_diagnostics.get("live_rewrite_noop_reason", "")).strip(),
            live_rewrite_anchor_coverage_score=float(rewrite_diagnostics.get("live_rewrite_anchor_coverage_score", 0.0)),
            # Anchor-loss measures whether the live script kept the FALLBACK's
            # suspense anchors - a rewrite-mode concept. An independent draft
            # has no obligation to reuse fallback anchors, so the disqualifier
            # only applies in polish/rewrite mode. The raw coverage score is
            # still assessed and recorded in the manifest either way.
            live_rewrite_anchor_loss=(
                bool(rewrite_diagnostics.get("live_rewrite_anchor_loss")) and live_rewrite_mode
            ),
            live_rewrite_anchor_loss_reason=str(rewrite_diagnostics.get("live_rewrite_anchor_loss_reason", "")).strip(),
        )
        winner = str(arbitration.get("winner", "fallback"))
        winner_reason = str(arbitration.get("winner_reason", ""))
        judge_model = str(arbitration.get("judge_model", "heuristic"))
        judge_summary = str(arbitration.get("judge_summary", ""))
        scores = arbitration.get("scores", {})

        winner_script = live_script if winner == "live" else fallback_script
        logger.info(
            "Script arbitration winner: %s (live_provider=%s, live_model=%s, judge_model=%s)",
            winner,
            live_provider or "none",
            live_model or "none",
            judge_model,
        )
        if winner_reason:
            logger.info("Script arbitration reason: %s", winner_reason)
        judge_payload = {
            "winner": winner,
            "winner_reason": winner_reason,
            "judge_summary": judge_summary,
            "scores": scores if isinstance(scores, dict) else {},
            "judge_model": judge_model,
            "live_rewrite_noop": bool(rewrite_diagnostics.get("live_rewrite_noop")),
            "live_rewrite_similarity_score": float(rewrite_diagnostics.get("live_rewrite_similarity_score", 0.0)),
            "live_rewrite_noop_reason": str(rewrite_diagnostics.get("live_rewrite_noop_reason", "")).strip(),
            "live_rewrite_anchor_coverage_score": float(rewrite_diagnostics.get("live_rewrite_anchor_coverage_score", 0.0)),
            "live_rewrite_anchor_loss": bool(rewrite_diagnostics.get("live_rewrite_anchor_loss")),
            "live_rewrite_anchor_loss_reason": str(rewrite_diagnostics.get("live_rewrite_anchor_loss_reason", "")).strip(),
        }
        return self._apply_arbitration_metadata(
            winner_script=winner_script,
            winner=winner,
            winner_reason=winner_reason,
            live_provider=live_provider,
            live_model=live_model,
            judge_model=judge_model,
            judge_summary=judge_summary,
            scores=scores if isinstance(scores, dict) else {},
            live_candidate=self._script_candidate_payload(live_script),
            fallback_candidate=self._script_candidate_payload(fallback_script),
            judge_result=judge_payload,
            live_generation_mode=live_generation_mode,
            live_rewrite_noop=bool(rewrite_diagnostics.get("live_rewrite_noop")),
            live_rewrite_similarity_score=float(rewrite_diagnostics.get("live_rewrite_similarity_score", 0.0)),
            live_rewrite_noop_reason=str(rewrite_diagnostics.get("live_rewrite_noop_reason", "")).strip(),
            live_rewrite_anchor_coverage_score=float(rewrite_diagnostics.get("live_rewrite_anchor_coverage_score", 0.0)),
            live_rewrite_anchor_loss=bool(rewrite_diagnostics.get("live_rewrite_anchor_loss")),
            live_rewrite_anchor_loss_reason=str(rewrite_diagnostics.get("live_rewrite_anchor_loss_reason", "")).strip(),
        )

    def _resolve_provider_order(self) -> list[str]:
        hint = self.script_provider
        if hint in {"fallback", "offline"}:
            return []

        default_order = ["openrouter", "openai", "anthropic"]
        ordered: list[str]
        if hint == "local":
            ordered = ["anthropic", *default_order]
        elif hint in {"openrouter", "openai", "anthropic"}:
            ordered = [hint, *default_order]
        elif hint not in {"", "auto"}:
            logger.warning(
                "Unknown SCRIPT_PROVIDER '%s'; using auto order: openrouter -> openai -> anthropic.",
                hint,
            )
            ordered = default_order
        else:
            ordered = default_order

        providers: list[str] = []
        for provider in ordered:
            if provider == "openrouter" and not self.openrouter_api_key:
                continue
            if provider == "openai" and not self.script_openai_api_key:
                continue
            if provider == "anthropic" and not self.client:
                continue
            if provider not in providers:
                providers.append(provider)
        return providers

    def _build_live_polish_prompt(
        self,
        *,
        base_prompt: str,
        topic: dict,
        fallback_script: dict,
        lane: str,
        topic_is_ai: bool,
    ) -> str:
        fallback_payload = {
            "title": str(fallback_script.get("title", "") or "").strip(),
            "hook": str(fallback_script.get("hook", "") or "").strip(),
            "narration": str(fallback_script.get("narration", "") or "").strip(),
        }
        if lane == "storytelling" and not topic_is_ai:
            preservation_rules = [
                "Keep the same core story and event order from fallback.",
                "Preserve the strongest hook idea from fallback. You may tighten wording, but keep the idea.",
                "Preserve the strongest impossible detail/anomaly from fallback; do not replace it with vague creepiness.",
                "Preserve or strengthen the reveal.",
                "Preserve or strengthen the ending consequence.",
                "Do not replace a personal threat with vague creepiness.",
                "Do not replace a hard twist with atmosphere-only writing.",
                "Preserve the escalation ladder. Do not flatten the suspense progression.",
                "Do not make the script safer, vaguer, or more generic than fallback.",
            ]
        else:
            preservation_rules = [
                "Keep the same core structure and progression from fallback.",
                "Preserve the strongest opening idea and concrete details from fallback.",
                "Preserve or strengthen the main payoff/conclusion.",
                "Do not replace specifics with generic phrasing.",
                "Do not make the script safer, vaguer, or more generic than fallback.",
            ]

        quality_rules = [
            "Sentence-level polish only: tighten phrasing, improve rhythm, and increase vividness.",
            "Keep wording grounded, specific, personal, and publishable for short-form suspense.",
            "Improve line quality without changing story events or event order.",
            "Remove repetition and awkward phrasing; avoid clipped or broken lines.",
            "Do not introduce unrelated new plot turns; only extremely minor compatible micro-details are allowed.",
            "Do not do cosmetic title-only rewrites.",
            "Do not return a near-copy unless no genuine sentence-level improvement is possible.",
            "Keep the same story events and order. Improve phrasing, vividness, and tension, but do not remove or replace the core hook, impossible detail, reveal, or consequence.",
            "Return ONLY the JSON structure requested above.",
        ]

        return (
            f"{base_prompt}\n\n"
            f"LIVE GENERATION MODE: {LIVE_GENERATION_MODE_POLISH}\n"
            "Task: Anchor-preserving polish of the fallback script below.\n"
            "Important: This is narrow polish, not a blank-page rewrite from topic.\n\n"
            "Fallback script scaffold (canonical structure to preserve):\n"
            + json.dumps(fallback_payload, ensure_ascii=False, indent=2)
            + "\n\nPreservation rules:\n- "
            + "\n- ".join(preservation_rules)
            + "\n\nQuality upgrade goals:\n- "
            + "\n- ".join(quality_rules)
        )

    def _build_live_independent_prompt(
        self,
        *,
        base_prompt: str,
        topic: dict,
        lane: str,
        topic_is_ai: bool,
    ) -> str:
        """Blank-page suspense generation from the topic - no fallback to polish.

        This is the CLAUDE.md priority-1 mode: the writer model competes by
        generating its own story, not by paraphrasing the fallback (which only
        ever lost anchors). No fallback scaffold is shown to the model.
        """
        if lane == "storytelling" and not topic_is_ai:
            craft_rules = [
                "Write a fresh first-person suspense story from the topic alone. Tell it - do not summarize or explain it.",
                "Hook in the first 3 seconds: open on a concrete unsettling action or a heard detail, never a setup sentence.",
                "Land exactly one concrete impossible detail or anomaly - something specific, not diffuse creepiness.",
                "Escalate: every sentence raises the stakes or narrows the narrator's options. No flat atmosphere padding.",
                "Deliver one clear reveal, then an ending consequence the narrator actually lives with.",
                "Voice: a real person recounting something that happened to them. Plain words, short spoken sentences.",
                "Banned cliches: 'little did I know', 'to my horror', 'sent chills', 'unbeknownst', 'or so I thought'.",
            ]
        else:
            craft_rules = [
                "Write a fresh script from the topic alone - an independent draft, not a rewrite of anything.",
                "Hook in the first 3 seconds with a concrete, specific opening - no setup or throat-clearing.",
                "Keep one clear through-line; every sentence earns its place and advances it.",
                "Deliver a real payoff and a concrete takeaway, not a vague summary.",
                "Voice: direct and human. Short spoken sentences. No corporate explainer tone.",
            ]
        return (
            f"{base_prompt}\n\n"
            f"LIVE GENERATION MODE: {LIVE_GENERATION_MODE_INDEPENDENT}\n"
            "Task: Generate an ORIGINAL script from the topic above. There is no\n"
            "draft to preserve or polish - this is a blank-page write.\n\n"
            "Suspense craft rules:\n- "
            + "\n- ".join(craft_rules)
            + "\n\nReturn ONLY the JSON structure requested above."
        )

    # Backward-compatible alias for older call sites/tests.
    def _build_live_rewrite_prompt(
        self,
        *,
        base_prompt: str,
        topic: dict,
        fallback_script: dict,
        lane: str,
        topic_is_ai: bool,
    ) -> str:
        return self._build_live_polish_prompt(
            base_prompt=base_prompt,
            topic=topic,
            fallback_script=fallback_script,
            lane=lane,
            topic_is_ai=topic_is_ai,
        )

    def _resolve_writer_model_for_provider(self, provider: str, lane: str) -> tuple[str, str]:
        if self.script_writer_model:
            return self.script_writer_model, "explicit override (SCRIPT_WRITER_MODEL)"

        if provider == "openrouter":
            if str(os.getenv("OPENROUTER_MODEL", "")).strip():
                return self.openrouter_model, "explicit override (OPENROUTER_MODEL)"
            return self.openrouter_model, "openrouter default model"
        if provider == "openai":
            return self._resolve_script_openai_model_for_lane(lane)
        if provider == "anthropic":
            if self.explicit_script_anthropic_model:
                return self.anthropic_model, "explicit override (SCRIPT_ANTHROPIC_MODEL)"
            return self.anthropic_model, "anthropic default model"
        return "", "unknown provider"

    def _provider_order_from_hint(self) -> list[str]:
        hint = self.script_provider
        default_order = ["openrouter", "openai", "anthropic"]
        if hint == "local":
            return ["anthropic", *default_order]
        if hint in {"openrouter", "openai", "anthropic"}:
            return [hint, *default_order]
        return default_order

    def _resolve_primary_writer_target(self, provider_order: list[str], lane: str) -> dict[str, str]:
        candidate_providers = provider_order or self._provider_order_from_hint()
        seen: set[str] = set()
        for provider in candidate_providers:
            provider_key = str(provider or "").strip()
            if not provider_key or provider_key in seen:
                continue
            seen.add(provider_key)
            model_name, _ = self._resolve_writer_model_for_provider(provider=provider, lane=lane)
            model = str(model_name or "").strip()
            if not model:
                continue
            return {
                "provider": provider_key,
                "model": model,
            }
        return {"provider": "", "model": ""}

    def _evaluate_live_rewrite_gate(self, provider_order: list[str], lane: str) -> dict[str, Any]:
        primary_target = self._resolve_primary_writer_target(provider_order=provider_order, lane=lane)
        active_provider = str(primary_target.get("provider", "") or "").strip()
        active_model = str(primary_target.get("model", "") or "").strip()

        if not self.script_live_rewrite_enabled:
            return {
                "skip": True,
                "reason": "SCRIPT_LIVE_REWRITE_ENABLED is disabled.",
                "provider": active_provider,
                "model": active_model,
            }

        # Allowlist is optional; when configured, exact model match is required.
        if self.approved_writer_models_raw and not self.approved_writer_models:
            return {
                "skip": True,
                "reason": "No approved writer models configured.",
                "provider": active_provider,
                "model": active_model,
            }
        if self.approved_writer_models and active_model not in self.approved_writer_models:
            rejected = active_model or "unknown"
            return {
                "skip": True,
                "reason": f"Writer model not approved for live rewrite: {rejected}",
                "provider": active_provider,
                "model": active_model,
            }

        return {
            "skip": False,
            "reason": "",
            "provider": active_provider,
            "model": active_model,
        }

    def _generate_best_live_script(
        self,
        provider_order: list[str],
        prompt: str,
        topic: dict,
        lane: str,
        topic_is_ai: bool,
        rewrite_mode: bool = False,
    ) -> tuple[dict | None, dict[str, str]]:
        if not provider_order:
            return None, {"provider": "", "model": "", "error": "No live script provider configured."}

        attempted_provider = False
        last_error = ""
        for provider in provider_order:
            attempted_provider = True
            model_name, model_reason = self._resolve_writer_model_for_provider(provider=provider, lane=lane)

            if provider == "anthropic" and self._is_local_anthropic_target():
                if not self._local_model_available(model_name):
                    last_error = f"Local model unavailable: {model_name} at {self.anthropic_base_url}"
                    logger.warning(last_error)
                    continue

            target = f"{provider}/{model_name}" if model_name else provider
            detail = model_reason
            if provider == "openrouter":
                detail = f"{model_reason}; base_url={self.openrouter_base_url}"
            elif provider == "openai" and self.script_openai_base_url:
                detail = f"{model_reason}; custom base_url={self.script_openai_base_url}"

            logger.info("🔌 Trying live script model: %s (%s)", target, detail)
            try:
                candidates = self._generate_live_candidates(
                    provider=provider,
                    base_prompt=prompt,
                    topic=topic,
                    lane=lane,
                    openai_model=model_name if provider == "openai" else None,
                    openrouter_model=model_name if provider == "openrouter" else None,
                    anthropic_model=model_name if provider == "anthropic" else None,
                    rewrite_mode=rewrite_mode,
                    topic_is_ai=topic_is_ai,
                )
                script = self._select_best_candidate(
                    candidates=candidates,
                    topic=topic,
                    lane=lane,
                    topic_is_ai=topic_is_ai,
                )
                logger.info("✅ Live model used successfully: %s (%s)", target, detail)
                logger.info("  ✅ Script: '%s' (~%ss)", script.get("title", ""), script.get("estimated_duration", "?"))
                return script, {
                    "provider": provider,
                    "model": model_name,
                    "detail": detail,
                    "error": "",
                }
            except Exception as e:
                last_error = f"{provider} script generation failed: {e}"
                logger.warning(last_error)

        if attempted_provider:
            logger.warning("All live script providers failed. Falling back to local script generation.")
        return None, {"provider": "", "model": "", "error": last_error or "All live script providers failed."}

    def _apply_arbitration_metadata(
        self,
        winner_script: dict,
        winner: str,
        winner_reason: str,
        live_provider: str,
        live_model: str,
        judge_model: str,
        judge_summary: str,
        scores: dict[str, float | int | str | dict],
        live_candidate: dict[str, Any] | None = None,
        fallback_candidate: dict[str, Any] | None = None,
        judge_result: dict[str, Any] | None = None,
        live_generation_mode: str = "",
        live_rewrite_skipped: bool = False,
        live_rewrite_skip_reason: str = "",
        live_rewrite_noop: bool = False,
        live_rewrite_similarity_score: float | None = None,
        live_rewrite_noop_reason: str = "",
        live_rewrite_anchor_coverage_score: float | None = None,
        live_rewrite_anchor_loss: bool = False,
        live_rewrite_anchor_loss_reason: str = "",
    ) -> dict:
        script = dict(winner_script or {})
        script["script_source_winner"] = winner
        script["selected_script_source"] = winner
        script["winner_reason"] = winner_reason
        script["judge_summary"] = judge_summary
        script["live_script_provider"] = live_provider
        script["live_script_model"] = live_model
        script["judge_model"] = judge_model
        script["script_arbitration_scores"] = scores
        script["live_generation_mode"] = live_generation_mode
        script["live_rewrite_skipped"] = bool(live_rewrite_skipped)
        script["live_rewrite_skip_reason"] = str(live_rewrite_skip_reason or "").strip()
        script["live_rewrite_noop"] = bool(live_rewrite_noop)
        script["live_rewrite_similarity_score"] = (
            round(float(live_rewrite_similarity_score), 3)
            if live_rewrite_similarity_score is not None
            else None
        )
        script["live_rewrite_noop_reason"] = str(live_rewrite_noop_reason or "").strip()
        script["live_rewrite_anchor_coverage_score"] = (
            round(float(live_rewrite_anchor_coverage_score), 3)
            if live_rewrite_anchor_coverage_score is not None
            else None
        )
        script["live_rewrite_anchor_loss"] = bool(live_rewrite_anchor_loss)
        script["live_rewrite_anchor_loss_reason"] = str(live_rewrite_anchor_loss_reason or "").strip()
        script["live_script_candidate"] = live_candidate or {}
        script["live_rewrite_candidate"] = live_candidate or {}
        script["fallback_script_candidate"] = fallback_candidate or {}
        script["judge_result"] = judge_result or {
            "winner": winner,
            "winner_reason": winner_reason,
            "judge_summary": judge_summary,
            "scores": scores,
            "judge_model": judge_model,
        }
        script["is_fallback"] = winner != "live"
        return script

    def _script_candidate_payload(self, script: dict | None) -> dict[str, Any]:
        if not isinstance(script, dict):
            return {}
        return {
            "title": str(script.get("title", "") or "").strip(),
            "hook": str(script.get("hook", "") or "").strip(),
            "narration": str(script.get("narration", "") or "").strip(),
            "estimated_duration": script.get("estimated_duration"),
            "words": script.get("words"),
        }

    def _assess_live_rewrite_noop(self, live_script: dict, fallback_script: dict) -> dict[str, Any]:
        live_title = str(live_script.get("title", "") or "").strip()
        fallback_title = str(fallback_script.get("title", "") or "").strip()
        live_hook = str(live_script.get("hook", "") or "").strip()
        fallback_hook = str(fallback_script.get("hook", "") or "").strip()
        live_narration = str(live_script.get("narration", "") or "").strip()
        fallback_narration = str(fallback_script.get("narration", "") or "").strip()

        title_norm_live = self._normalize_similarity_text(live_title)
        title_norm_fallback = self._normalize_similarity_text(fallback_title)
        hook_norm_live = self._normalize_similarity_text(live_hook)
        hook_norm_fallback = self._normalize_similarity_text(fallback_hook)
        narration_norm_live = self._normalize_similarity_text(live_narration)
        narration_norm_fallback = self._normalize_similarity_text(fallback_narration)

        title_similarity = (
            SequenceMatcher(None, title_norm_fallback, title_norm_live).ratio()
            if title_norm_fallback or title_norm_live
            else 1.0
        )
        hook_similarity = (
            SequenceMatcher(None, hook_norm_fallback, hook_norm_live).ratio()
            if hook_norm_fallback or hook_norm_live
            else 1.0
        )
        narration_similarity = (
            SequenceMatcher(None, narration_norm_fallback, narration_norm_live).ratio()
            if narration_norm_fallback or narration_norm_live
            else 1.0
        )
        narration_jaccard = self._token_jaccard(narration_norm_fallback, narration_norm_live)

        fallback_tokens = set(narration_norm_fallback.split())
        live_tokens = set(narration_norm_live.split())
        token_delta = len(fallback_tokens.symmetric_difference(live_tokens))
        word_count_gap = abs(len(live_narration.split()) - len(fallback_narration.split()))
        sentence_gap = abs(
            len(self._sentence_list(live_narration)) - len(self._sentence_list(fallback_narration))
        )

        near_identical_hook = hook_similarity >= 0.96 or hook_norm_live == hook_norm_fallback
        high_narration_overlap = narration_similarity >= 0.93 or narration_jaccard >= 0.86
        only_cosmetic_title_change = (
            title_similarity >= 0.96
            and hook_similarity >= 0.95
            and narration_similarity >= 0.95
        )
        minimal_structural_change = (
            token_delta <= max(6, int(len(fallback_tokens) * 0.08))
            and word_count_gap <= 8
            and sentence_gap <= 1
        )
        near_copy = high_narration_overlap and minimal_structural_change

        noop = bool(
            only_cosmetic_title_change
            or (near_identical_hook and near_copy)
            or (narration_similarity >= 0.97 and hook_similarity >= 0.96)
        )
        similarity_score = round(
            min(
                1.0,
                max(
                    0.0,
                    (hook_similarity * 0.30)
                    + (narration_similarity * 0.50)
                    + (narration_jaccard * 0.20),
                ),
            ),
            3,
        )

        reason = ""
        if noop:
            if only_cosmetic_title_change:
                reason = "Only cosmetic title changes; hook and narration are near-identical."
            elif near_identical_hook and near_copy:
                reason = "Hook is nearly identical and narration overlap is very high with minimal structural change."
            else:
                reason = "Rewrite is near-copy with minimal meaningful improvement."

        return {
            "live_rewrite_noop": noop,
            "live_rewrite_similarity_score": similarity_score,
            "live_rewrite_noop_reason": reason,
        }

    def _normalize_similarity_text(self, text: str) -> str:
        lowered = str(text or "").lower()
        alnum = re.sub(r"[^a-z0-9\s]", " ", lowered)
        return re.sub(r"\s+", " ", alnum).strip()

    def _token_jaccard(self, left: str, right: str) -> float:
        left_set = {token for token in str(left or "").split() if token}
        right_set = {token for token in str(right or "").split() if token}
        if not left_set and not right_set:
            return 1.0
        if not left_set or not right_set:
            return 0.0
        return len(left_set & right_set) / max(1, len(left_set | right_set))

    def _extract_live_fallback_totals(self, scores: Any) -> tuple[float | None, float | None]:
        if not isinstance(scores, dict):
            return None, None

        def _as_float(value: Any) -> float | None:
            try:
                if value is None:
                    return None
                return float(value)
            except (TypeError, ValueError):
                return None

        live_total = _as_float(scores.get("live_total"))
        fallback_total = _as_float(scores.get("fallback_total"))
        if live_total is not None and fallback_total is not None:
            return live_total, fallback_total

        live_nested = scores.get("live", {}) if isinstance(scores.get("live", {}), dict) else {}
        fallback_nested = (
            scores.get("fallback", {}) if isinstance(scores.get("fallback", {}), dict) else {}
        )
        live_total_nested = _as_float(live_nested.get("total"))
        fallback_total_nested = _as_float(fallback_nested.get("total"))
        return live_total_nested, fallback_total_nested

    def _assess_live_rewrite_anchor_coverage(self, live_script: dict, fallback_script: dict) -> dict[str, Any]:
        anchors = self._extract_fallback_structural_anchors(fallback_script=fallback_script)
        if not anchors:
            return {
                "live_rewrite_anchor_coverage_score": 1.0,
                "live_rewrite_anchor_loss": False,
                "live_rewrite_anchor_loss_reason": "",
            }

        live_hook = str(live_script.get("hook", "") or "").strip()
        live_narration = str(live_script.get("narration", "") or "").strip()
        live_text = self._normalize_similarity_text(f"{live_hook}. {live_narration}".strip())
        live_sentences = [
            self._normalize_similarity_text(sentence)
            for sentence in self._sentence_list(f"{live_hook}. {live_narration}".strip())
        ]
        live_sentences = [sentence for sentence in live_sentences if sentence]

        hit_count = 0
        missing: list[dict[str, str]] = []
        type_hits: dict[str, bool] = {}
        type_present: dict[str, bool] = {}
        coverage_thresholds = {
            "hook_anchor": 0.66,
            "impossible_detail": 0.58,
            "reveal_beat": 0.58,
            "ending_consequence": 0.58,
        }

        for anchor in anchors:
            anchor_text = str(anchor.get("text", "") or "").strip()
            anchor_type = str(anchor.get("type", "") or "detail").strip() or "detail"
            anchor_norm = self._normalize_similarity_text(anchor_text)
            if not anchor_norm:
                continue
            type_present[anchor_type] = True

            sentence_similarity = max(
                (
                    SequenceMatcher(None, anchor_norm, live_sentence).ratio()
                    for live_sentence in live_sentences
                ),
                default=0.0,
            )

            key_tokens = self._anchor_key_tokens(anchor_norm)
            key_token_coverage = 0.0
            if key_tokens:
                matched = sum(1 for token in key_tokens if token in live_text.split())
                key_token_coverage = matched / max(1, len(key_tokens))

            threshold = float(coverage_thresholds.get(anchor_type, 0.56))
            anchor_hit = bool(sentence_similarity >= 0.74 or key_token_coverage >= threshold)
            if anchor_hit:
                hit_count += 1
                type_hits[anchor_type] = True
                continue

            missing.append({"type": anchor_type, "text": anchor_text})
            type_hits.setdefault(anchor_type, False)

        total = len(anchors)
        coverage_score = float(hit_count / max(1, total))
        critical_types = {"impossible_detail", "reveal_beat", "ending_consequence"}
        missing_critical = [
            anchor_type
            for anchor_type in critical_types
            if type_present.get(anchor_type) and not type_hits.get(anchor_type, False)
        ]

        anchor_loss = bool(
            (total >= 3 and coverage_score < 0.67)
            or len(missing_critical) >= 2
            or (len(missing_critical) >= 1 and coverage_score < 0.75)
        )

        reason = ""
        if anchor_loss:
            missing_types = ", ".join(sorted(set(missing_critical))) if missing_critical else ""
            if missing_types:
                reason = f"Missing critical suspense anchors: {missing_types}."
            elif missing:
                reason = f"Anchor coverage too low ({coverage_score:.2f}); key fallback beats were dropped."
            else:
                reason = f"Anchor coverage too low ({coverage_score:.2f})."

        return {
            "live_rewrite_anchor_coverage_score": round(coverage_score, 3),
            "live_rewrite_anchor_loss": anchor_loss,
            "live_rewrite_anchor_loss_reason": reason,
        }

    def _extract_fallback_structural_anchors(self, fallback_script: dict) -> list[dict[str, str]]:
        hook = str(fallback_script.get("hook", "") or "").strip()
        narration = str(fallback_script.get("narration", "") or "").strip()
        body = self._strip_hook_prefix_from_narration(narration=narration, hook=hook)
        body_sentences = self._sentence_list(body)

        anchors: list[dict[str, str]] = []
        if hook:
            anchors.append({"type": "hook_anchor", "text": hook})

        scored_sentences: list[dict[str, Any]] = []
        for idx, sentence in enumerate(body_sentences):
            sentence_text = str(sentence or "").strip()
            if not sentence_text:
                continue
            sentence_type, score = self._anchor_sentence_type_and_score(
                sentence=sentence_text,
                is_last=idx == (len(body_sentences) - 1),
                is_last_two=idx >= max(0, len(body_sentences) - 2),
            )
            scored_sentences.append(
                {
                    "type": sentence_type,
                    "text": sentence_text,
                    "score": score,
                    "index": idx,
                }
            )

        def _best_of_type(anchor_type: str) -> dict[str, Any] | None:
            candidates = [row for row in scored_sentences if row["type"] == anchor_type]
            if not candidates:
                return None
            return max(candidates, key=lambda row: (float(row["score"]), -int(row["index"])))

        for anchor_type in ("impossible_detail", "reveal_beat", "ending_consequence"):
            best = _best_of_type(anchor_type)
            if best and float(best["score"]) > 0:
                anchors.append({"type": anchor_type, "text": str(best["text"])})

        if len(anchors) < 4:
            extras = sorted(
                scored_sentences,
                key=lambda row: (float(row["score"]), -int(row["index"])),
                reverse=True,
            )
            existing = {
                self._normalize_similarity_text(str(anchor.get("text", "") or ""))
                for anchor in anchors
            }
            for row in extras:
                norm_text = self._normalize_similarity_text(str(row.get("text", "") or ""))
                if not norm_text or norm_text in existing:
                    continue
                anchors.append({"type": str(row.get("type", "detail")), "text": str(row.get("text", ""))})
                existing.add(norm_text)
                if len(anchors) >= 4:
                    break

        deduped: list[dict[str, str]] = []
        seen: set[str] = set()
        for anchor in anchors:
            text = str(anchor.get("text", "") or "").strip()
            if not text:
                continue
            normalized = self._normalize_similarity_text(text)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(
                {
                    "type": str(anchor.get("type", "detail") or "detail"),
                    "text": text,
                }
            )
        return deduped

    def _anchor_sentence_type_and_score(
        self,
        sentence: str,
        *,
        is_last: bool,
        is_last_two: bool,
    ) -> tuple[str, float]:
        lowered = str(sentence or "").lower()
        impossible_cues = {
            "wrong",
            "nobody",
            "no one",
            "changed places",
            "turned before",
            "blinked late",
            "same time",
            "impossible",
            "pointed the wrong",
            "not where",
        }
        reveal_cues = {
            "then",
            "when",
            "but",
            "revealed",
            "realized",
            "noticed",
            "the second",
            "used",
            "repeated",
            "turned",
            "changed",
        }
        consequence_cues = {
            "ran",
            "left",
            "never",
            "called",
            "waited",
            "outside until",
            "didn't open",
            "did not open",
            "exit",
            "door",
            "sunrise",
        }

        impossible_score = float(sum(1 for cue in impossible_cues if cue in lowered))
        reveal_score = float(sum(1 for cue in reveal_cues if cue in lowered))
        consequence_score = float(sum(1 for cue in consequence_cues if cue in lowered))
        if is_last_two:
            consequence_score += 0.75
        if is_last:
            consequence_score += 0.75

        if consequence_score >= max(impossible_score, reveal_score) and consequence_score > 0:
            return "ending_consequence", consequence_score
        if impossible_score >= max(reveal_score, consequence_score) and impossible_score > 0:
            return "impossible_detail", impossible_score
        if reveal_score > 0:
            return "reveal_beat", reveal_score
        return "detail", 0.25

    def _anchor_key_tokens(self, normalized_text: str) -> list[str]:
        stopwords = {
            "the",
            "a",
            "an",
            "and",
            "or",
            "but",
            "to",
            "of",
            "in",
            "on",
            "at",
            "for",
            "with",
            "my",
            "your",
            "his",
            "her",
            "their",
            "our",
            "was",
            "were",
            "is",
            "are",
            "it",
            "that",
            "this",
            "then",
            "when",
            "i",
        }
        tokens = [token for token in str(normalized_text or "").split() if token]
        filtered = [token for token in tokens if len(token) > 2 and token not in stopwords]
        if not filtered:
            return tokens[:4]
        return filtered[:8]

    def _arbitrate_live_vs_fallback(
        self,
        topic: dict,
        lane: str,
        topic_is_ai: bool,
        live_script: dict,
        fallback_script: dict,
        live_provider: str,
        live_model: str,
        live_rewrite_noop: bool = False,
        live_rewrite_noop_reason: str = "",
        live_rewrite_anchor_coverage_score: float = 0.0,
        live_rewrite_anchor_loss: bool = False,
        live_rewrite_anchor_loss_reason: str = "",
    ) -> dict[str, Any]:
        heuristic = self._heuristic_script_arbitration(
            topic=topic,
            lane=lane,
            topic_is_ai=topic_is_ai,
            live_script=live_script,
            fallback_script=fallback_script,
            live_rewrite_noop=live_rewrite_noop,
            live_rewrite_noop_reason=live_rewrite_noop_reason,
            live_rewrite_anchor_coverage_score=live_rewrite_anchor_coverage_score,
            live_rewrite_anchor_loss=live_rewrite_anchor_loss,
            live_rewrite_anchor_loss_reason=live_rewrite_anchor_loss_reason,
        )
        if not self.script_judge_enabled:
            heuristic["judge_model"] = "disabled-heuristic"
            heuristic["judge_summary"] = "LLM judge disabled; heuristic arbitration used."
            return heuristic

        judge_runtime = self._resolve_judge_provider_and_model(preferred_provider=live_provider, lane=lane)
        judge_provider = judge_runtime.get("provider", "")
        judge_model = judge_runtime.get("model", "")
        if not judge_provider or not judge_model:
            heuristic["judge_model"] = "heuristic"
            heuristic["judge_summary"] = "No live judge provider available; heuristic arbitration used."
            return heuristic

        judge_prompt = self._build_script_judge_prompt(
            topic=topic,
            live_script=live_script,
            fallback_script=fallback_script,
        )
        try:
            raw = self._request_judge_decision(
                provider=judge_provider,
                model_name=judge_model,
                prompt=judge_prompt,
            )
            parsed = self._parse_judge_response(raw)
            winner = str(parsed.get("winner", "")).strip().lower()
            if winner not in {"live", "fallback"}:
                raise ValueError("Judge output winner was missing or invalid.")
            parsed_scores = parsed.get("scores", heuristic.get("scores", {}))
            if live_rewrite_anchor_loss and winner == "live":
                live_total, fallback_total = self._extract_live_fallback_totals(parsed_scores)
                clear_live_preference = (
                    live_total is not None
                    and fallback_total is not None
                    and (live_total - fallback_total) >= 4.0
                )
                if not clear_live_preference:
                    anchor_reason = (
                        str(live_rewrite_anchor_loss_reason or "").strip()
                        or "Live rewrite dropped critical fallback suspense anchors."
                    )
                    return {
                        "winner": "fallback",
                        "winner_reason": f"{anchor_reason} Anchor-loss guardrail kept fallback.",
                        "judge_summary": "Anchor-loss guardrail rejected a polished-but-weaker rewrite.",
                        "scores": parsed_scores,
                        "judge_model": f"{judge_provider}/{judge_model}",
                    }
            if live_rewrite_noop and winner == "live":
                live_total, fallback_total = self._extract_live_fallback_totals(parsed_scores)
                clear_live_preference = (
                    live_total is not None
                    and fallback_total is not None
                    and (live_total - fallback_total) >= 2.0
                )
                if not clear_live_preference:
                    noop_reason = (
                        str(live_rewrite_noop_reason or "").strip()
                        or "Live rewrite was near-no-op."
                    )
                    return {
                        "winner": "fallback",
                        "winner_reason": f"{noop_reason} Judge did not clearly prefer live rewrite.",
                        "judge_summary": "No-op guardrail kept fallback as winner.",
                        "scores": parsed_scores,
                        "judge_model": f"{judge_provider}/{judge_model}",
                    }
            return {
                "winner": winner,
                "winner_reason": str(parsed.get("winner_reason", "")).strip()
                or str(heuristic.get("winner_reason", "")),
                "judge_summary": str(parsed.get("judge_summary", "")).strip()
                or "LLM arbitration completed.",
                "scores": parsed_scores,
                "judge_model": f"{judge_provider}/{judge_model}",
            }
        except Exception as exc:
            logger.warning("Script LLM judge failed (%s). Falling back to heuristic arbitration.", exc)
            heuristic["judge_model"] = f"{judge_provider}/{judge_model} (failed -> heuristic)"
            heuristic["judge_summary"] = "LLM judge failed; heuristic arbitration used."
            return heuristic

    def _resolve_judge_provider_and_model(self, preferred_provider: str, lane: str) -> dict[str, str]:
        ordered = [preferred_provider, "openrouter", "openai", "anthropic"]
        providers: list[str] = []
        for provider in ordered:
            normalized = str(provider or "").strip().lower()
            if normalized and normalized not in providers:
                providers.append(normalized)

        for provider in providers:
            if provider == "openrouter" and self.openrouter_api_key:
                model = self.script_judge_model or self.script_writer_model or self.openrouter_model
                return {"provider": provider, "model": model}
            if provider == "openai" and self.script_openai_api_key:
                default_openai_model, _ = self._resolve_script_openai_model_for_lane(lane)
                model = self.script_judge_model or self.script_writer_model or default_openai_model
                return {"provider": provider, "model": model}
            if provider == "anthropic" and self.client:
                model = self.script_judge_model or self.script_writer_model or self.anthropic_model
                return {"provider": provider, "model": model}
        return {"provider": "", "model": ""}

    def _build_script_judge_prompt(
        self,
        topic: dict,
        live_script: dict,
        fallback_script: dict,
    ) -> str:
        topic_title = str(topic.get("title", "") or "").strip()
        topic_summary = str(topic.get("summary", "") or "").strip()
        live_payload = {
            "title": str(live_script.get("title", "") or "").strip(),
            "hook": str(live_script.get("hook", "") or "").strip(),
            "narration": str(live_script.get("narration", "") or "").strip(),
        }
        fallback_payload = {
            "title": str(fallback_script.get("title", "") or "").strip(),
            "hook": str(fallback_script.get("hook", "") or "").strip(),
            "narration": str(fallback_script.get("narration", "") or "").strip(),
        }
        return (
            f"Topic title: {topic_title}\n"
            f"Topic summary: {topic_summary}\n\n"
            "Rubric (0-10 each): hook_strength, reveal_clarity, ending_consequence, "
            "non_repetition, line_quality, topic_faithfulness, planner_friendliness, publishability.\n\n"
            "Candidate live:\n"
            + json.dumps(live_payload, ensure_ascii=False, indent=2)
            + "\n\nCandidate fallback:\n"
            + json.dumps(fallback_payload, ensure_ascii=False, indent=2)
            + "\n\nReturn JSON only."
        )

    def _request_judge_decision(self, provider: str, model_name: str, prompt: str) -> str:
        if provider == "openrouter":
            return self._request_openrouter_script(
                prompt=prompt,
                model_name=model_name,
                system_prompt=JUDGE_SYSTEM_PROMPT,
            )
        if provider == "openai":
            return self._request_openai_script(
                prompt=prompt,
                model_name=model_name,
                system_prompt=JUDGE_SYSTEM_PROMPT,
            )
        if provider == "anthropic":
            return self._request_anthropic_script(
                prompt=prompt,
                model_name=model_name,
                system_prompt=JUDGE_SYSTEM_PROMPT,
            )
        raise RuntimeError(f"Unsupported judge provider: {provider}")

    def _parse_judge_response(self, raw_response: str) -> dict[str, Any]:
        cleaned = re.sub(r"```json\s*", "", raw_response or "")
        cleaned = re.sub(r"```\s*", "", cleaned).strip()
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise ValueError("Judge response was not a JSON object.")
        return parsed

    def _heuristic_script_arbitration(
        self,
        topic: dict,
        lane: str,
        topic_is_ai: bool,
        live_script: dict,
        fallback_script: dict,
        live_rewrite_noop: bool = False,
        live_rewrite_noop_reason: str = "",
        live_rewrite_anchor_coverage_score: float = 0.0,
        live_rewrite_anchor_loss: bool = False,
        live_rewrite_anchor_loss_reason: str = "",
    ) -> dict[str, Any]:
        live_metrics = self._candidate_arbitration_metrics(
            script=live_script,
            topic=topic,
            lane=lane,
            topic_is_ai=topic_is_ai,
        )
        fallback_metrics = self._candidate_arbitration_metrics(
            script=fallback_script,
            topic=topic,
            lane=lane,
            topic_is_ai=topic_is_ai,
        )

        live_total = float(live_metrics.get("total", 0.0))
        fallback_total = float(fallback_metrics.get("total", 0.0))
        live_noop_penalty = 6.0 if live_rewrite_noop else 0.0
        live_anchor_penalty = 18.0 if live_rewrite_anchor_loss else 0.0
        live_total_adjusted = max(0.0, live_total - live_noop_penalty - live_anchor_penalty)
        severe_anchor_loss = bool(
            live_rewrite_anchor_loss and float(live_rewrite_anchor_coverage_score) <= 0.55
        )
        winner = "fallback" if severe_anchor_loss else (
            "live" if live_total_adjusted >= (fallback_total + 1.0) else "fallback"
        )
        winner_reason = (
            f"Heuristic score favored {winner} "
            f"(live={live_total_adjusted:.1f}, fallback={fallback_total:.1f})."
        )
        if severe_anchor_loss:
            anchor_reason = (
                str(live_rewrite_anchor_loss_reason or "").strip()
                or "Live rewrite dropped critical fallback anchors."
            )
            winner_reason = (
                f"{anchor_reason} Severe anchor loss triggered fallback guardrail "
                f"(coverage={float(live_rewrite_anchor_coverage_score):.2f})."
            )
        elif live_rewrite_anchor_loss:
            anchor_reason = (
                str(live_rewrite_anchor_loss_reason or "").strip()
                or "Live rewrite dropped critical fallback anchors."
            )
            winner_reason = (
                f"{anchor_reason} Anchor-loss penalty applied "
                f"(live={live_total:.1f}->{live_total_adjusted:.1f}, fallback={fallback_total:.1f})."
            )
        elif live_rewrite_noop:
            noop_reason = str(live_rewrite_noop_reason or "").strip() or "Live rewrite appears near-identical."
            winner_reason = (
                f"{noop_reason} No-op penalty applied "
                f"(live={live_total:.1f}->{live_total_adjusted:.1f}, fallback={fallback_total:.1f})."
            )
        elif winner == "fallback" and fallback_total >= live_total_adjusted:
            winner_reason = (
                f"Fallback scored higher quality for suspense planning "
                f"(live={live_total_adjusted:.1f}, fallback={fallback_total:.1f})."
            )
        return {
            "winner": winner,
            "winner_reason": winner_reason,
            "judge_summary": winner_reason,
            "scores": {
                "live": live_metrics,
                "fallback": fallback_metrics,
                "live_total": round(live_total_adjusted, 1),
                "live_total_before_noop_penalty": round(live_total, 1),
                "fallback_total": round(fallback_total, 1),
                "live_noop_penalty": round(live_noop_penalty, 1),
                "live_anchor_loss_penalty": round(live_anchor_penalty, 1),
                "live_anchor_coverage_score": round(float(live_rewrite_anchor_coverage_score), 3),
            },
            "judge_model": "heuristic",
        }

    def _candidate_arbitration_metrics(
        self,
        script: dict,
        topic: dict,
        lane: str,
        topic_is_ai: bool,
    ) -> dict[str, float]:
        score = self._score_script_candidate(script=script, topic=topic, lane=lane, topic_is_ai=topic_is_ai)
        hook = str(script.get("hook", "") or "").strip()
        narration = str(script.get("narration", "") or "").strip()
        sentences = self._sentence_list(self._strip_hook_prefix_from_narration(narration=narration, hook=hook))
        final_line = sentences[-1] if sentences else ""
        archetype = self._storytelling_topic_context(topic).get("archetype", "generic_story")

        clipped_penalty, _ = self._clipped_line_penalty(text=f"{hook} {narration}")
        reveal_score = float(score.get("story_reveal_strength", 0.0)) if lane == "storytelling" and not topic_is_ai else float(score.get("body", 0.0))
        ending_score = 9.0 if (
            self._story_line_has_consequence(final_line)
            or self._story_line_has_reveal(final_line, archetype=archetype)
            or self._story_line_has_final_image(final_line)
        ) else 4.5
        metrics = {
            "hook_strength": max(0.0, min(10.0, float(score.get("hook", 0.0)) / 10.0)),
            "reveal_clarity": max(0.0, min(10.0, reveal_score / 10.0)),
            "ending_consequence": ending_score,
            "non_repetition": max(0.0, min(10.0, 10.0 - (float(score.get("repetition_penalty", 0.0)) / 2.0))),
            "line_quality": max(
                0.0,
                min(
                    10.0,
                    10.0 - (clipped_penalty / 3.0) - (float(score.get("generic_penalty", 0.0)) / 4.0),
                ),
            ),
            "topic_faithfulness": max(0.0, min(10.0, float(score.get("lane", 0.0)) / 10.0)),
            "planner_friendliness": max(
                0.0,
                min(
                    10.0,
                    ((float(score.get("concrete", 0.0)) + float(score.get("specificity", 0.0))) / 20.0),
                ),
            ),
            "publishability": max(
                0.0,
                min(
                    10.0,
                    (float(score.get("total", 0.0)) / 10.0) - (clipped_penalty / 10.0),
                ),
            ),
        }
        total = (
            (metrics["hook_strength"] * 1.8)
            + (metrics["reveal_clarity"] * 1.6)
            + (metrics["ending_consequence"] * 1.4)
            + (metrics["non_repetition"] * 1.0)
            + (metrics["line_quality"] * 1.2)
            + (metrics["topic_faithfulness"] * 1.2)
            + (metrics["planner_friendliness"] * 1.0)
            + (metrics["publishability"] * 1.8)
        )
        metrics["total"] = round(max(0.0, min(100.0, total)), 1)
        return {key: round(float(value), 1) for key, value in metrics.items()}

    def _clipped_line_penalty(self, text: str) -> tuple[float, int]:
        dangling_tokens = {
            "my",
            "your",
            "his",
            "her",
            "their",
            "our",
            "in",
            "on",
            "at",
            "to",
            "from",
            "with",
            "behind",
            "under",
            "until",
            "when",
            "and",
            "or",
            "but",
            "old",
        }
        penalty = 0.0
        broken_lines = 0
        for sentence in self._sentence_list(text):
            tokens = re.findall(r"[a-z0-9']+", sentence.lower())
            if not tokens:
                continue
            if tokens[-1] in dangling_tokens:
                penalty += 7.0
                broken_lines += 1
        stripped = str(text or "").strip()
        if stripped and stripped[-1] not in ".!?":
            penalty += 3.0
        return min(28.0, penalty), broken_lines

    def _resolve_script_openai_model_for_lane(self, lane: str) -> tuple[str, str]:
        if self.script_writer_model:
            return self.script_writer_model, "explicit override (SCRIPT_WRITER_MODEL)"
        if self.explicit_script_openai_model:
            return self.explicit_script_openai_model, "explicit override (SCRIPT_OPENAI_MODEL)"

        lane_defaults = {
            "ai_productivity": "gpt-4o",
            "storytelling": "gpt-4o-mini",
        }
        lane_key = str(lane or "").strip().lower()
        if lane_key in lane_defaults:
            return lane_defaults[lane_key], f"lane-routed default ({lane_key})"
        return "gpt-4o-mini", f"lane-routed fallback ({lane_key or 'unknown'})"

    def _request_openai_script(
        self,
        prompt: str,
        model_name: str,
        system_prompt: str | None = None,
    ) -> str:
        import openai

        client_kwargs = {"api_key": self.script_openai_api_key}
        if self.script_openai_base_url:
            client_kwargs["base_url"] = self.script_openai_base_url
        client = openai.OpenAI(**client_kwargs)
        response = client.chat.completions.create(
            model=model_name,
            max_tokens=1500,
            temperature=0.7,
            messages=[
                {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )

        content = ""
        if response.choices:
            content = response.choices[0].message.content or ""
        if not content.strip():
            raise RuntimeError("OpenAI returned an empty script response")
        return content.strip()

    def _request_openrouter_script(
        self,
        prompt: str,
        model_name: str,
        system_prompt: str | None = None,
    ) -> str:
        import openai

        client = openai.OpenAI(
            api_key=self.openrouter_api_key,
            base_url=self.openrouter_base_url,
        )
        response = client.chat.completions.create(
            model=model_name,
            max_tokens=1500,
            temperature=0.7,
            messages=[
                {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )

        content = ""
        if response.choices:
            content = response.choices[0].message.content or ""
        if not content.strip():
            raise RuntimeError("OpenRouter returned an empty script response")
        return content.strip()

    def _request_anthropic_script(
        self,
        prompt: str,
        model_name: str | None = None,
        system_prompt: str | None = None,
    ) -> str:
        if not self.client:
            raise RuntimeError("Anthropic client is not configured")

        message = self.client.messages.create(
            model=model_name or self.anthropic_model,
            max_tokens=1500,
            system=system_prompt or SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        text_chunks: list[str] = []
        for block in getattr(message, "content", []) or []:
            if isinstance(block, dict):
                text = str(block.get("text", "")).strip()
            else:
                text = str(getattr(block, "text", "")).strip()
            if text:
                text_chunks.append(text)

        raw = "\n".join(text_chunks).strip()
        if not raw:
            raise RuntimeError("Anthropic returned an empty script response")
        return raw

    def _parse_script_response(self, raw_response: str, topic: dict) -> dict:
        cleaned = re.sub(r"```json\s*", "", raw_response or "")
        cleaned = re.sub(r"```\s*", "", cleaned)
        script = json.loads(cleaned)
        if not isinstance(script, dict):
            raise ValueError("Model response was not a JSON object")
        script = self._apply_quality_guardrails(script=script, topic=topic)
        script["topic"] = topic
        script["generated_at"] = datetime.now().isoformat()
        return script

    def _generate_live_candidates(
        self,
        provider: str,
        base_prompt: str,
        topic: dict,
        lane: str,
        openai_model: str | None = None,
        openrouter_model: str | None = None,
        anthropic_model: str | None = None,
        rewrite_mode: bool = False,
        topic_is_ai: bool = False,
    ) -> list[dict]:
        candidate_focuses = (
            self._rewrite_candidate_focuses(lane=lane, topic_is_ai=topic_is_ai)
            if rewrite_mode
            else self._candidate_focuses_for_lane(lane)
        )
        candidates: list[dict] = []

        for idx, focus in enumerate(candidate_focuses, start=1):
            candidate_prompt = (
                f"{base_prompt}\n\n"
                f"Candidate focus ({idx}/3): {focus}\n"
                "Treat this as the dominant angle for this candidate only."
            )
            try:
                if provider == "openrouter":
                    if not openrouter_model:
                        raise RuntimeError("OpenRouter model resolution missing for candidate generation")
                    raw_response = self._request_openrouter_script(
                        candidate_prompt,
                        model_name=openrouter_model,
                    )
                elif provider == "openai":
                    if not openai_model:
                        raise RuntimeError("OpenAI model resolution missing for candidate generation")
                    raw_response = self._request_openai_script(candidate_prompt, model_name=openai_model)
                elif provider == "anthropic":
                    raw_response = self._request_anthropic_script(
                        candidate_prompt,
                        model_name=anthropic_model,
                    )
                else:
                    raise RuntimeError(f"Unsupported provider for candidate generation: {provider}")

                candidate_script = self._parse_script_response(raw_response, topic)
                candidates.append(candidate_script)
            except Exception as e:
                raise RuntimeError(f"{provider} candidate {idx} failed: {e}") from e

        if len(candidates) != 3:
            raise RuntimeError(f"{provider} returned {len(candidates)} candidates (expected 3)")
        return candidates

    def _candidate_focuses_for_lane(self, lane: str) -> list[str]:
        focuses = {
            "ai_productivity": [
                "Painful manual task first: target repetitive hand-written/done-by-hand work with urgency.",
                "Wrong first use of AI: call out the common bad first use and show the better first move.",
                "Concrete practical workflow: clear short sequence tied to one real task and outcome.",
            ],
            "storytelling": [
                "Whisper/overheard opener only: first sentence must start with a heard voice, whisper, or name being called before any visual detail.",
                "Wrong-detail visual opener only: first sentence must show a normal scene and then one object moved or impossible visual anomaly.",
                "Consequence/escape opener only: first sentence must frame leaving, backing away, running, or turning around as the immediate choice.",
            ],
            "commentary": [
                "Hard stance with disagreement tension.",
                "Counter-intuitive opinion with fast defense.",
                "Blunt argument against common advice.",
            ],
            "motivation": [
                "Blunt callout of excuses and avoidance.",
                "Discipline over feelings framing.",
                "Consequence-driven push to act now.",
            ],
        }
        return focuses.get(lane, focuses["ai_productivity"])

    def _rewrite_candidate_focuses(self, lane: str, topic_is_ai: bool) -> list[str]:
        if lane == "storytelling" and not topic_is_ai:
            return [
                "Preserve all core beats and escalation order; tighten rhythm and spoken flow.",
                "Preserve hook and impossible detail; sharpen reveal clarity and ending consequence.",
                "Preserve twist structure and personal specificity; improve cinematic line quality without generic creepiness.",
            ]
        return [
            "Preserve core structure and outcome; tighten rhythm and line clarity.",
            "Preserve strongest hook/promise and concrete details; reduce repetition and awkward phrasing.",
            "Preserve progression and payoff; improve voice, specificity, and publishability without becoming generic.",
        ]

    def _select_best_candidate(self, candidates: list[dict], topic: dict, lane: str, topic_is_ai: bool) -> dict:
        if not candidates:
            raise RuntimeError("No candidates to score")

        scored: list[tuple[int, dict, dict]] = []
        for idx, candidate in enumerate(candidates, start=1):
            score_breakdown = self._score_script_candidate(
                script=candidate,
                topic=topic,
                lane=lane,
                topic_is_ai=topic_is_ai,
            )
            scored.append((idx, candidate, score_breakdown))

        self._apply_novelty_adjustments(scored)
        if lane == "storytelling" and not topic_is_ai:
            self._apply_storytelling_diversity_adjustments(scored=scored)

        for idx, _, score_breakdown in scored:
            logger.info(
                "Candidate %s score:\n"
                "- total=%.1f\n"
                "- hook=%.1f\n"
                "- body=%.1f\n"
                "- lane=%.1f\n"
                "- concrete=%.1f\n"
                "- specificity=%.1f\n"
                "- spoken=%.1f\n"
                "- novelty=%.1f\n"
                "- repetition_penalty=%.1f\n"
                "- generic_penalty=%.1f",
                idx,
                score_breakdown["total"],
                score_breakdown["hook"],
                score_breakdown["body"],
                score_breakdown["lane"],
                score_breakdown["concrete"],
                score_breakdown["specificity"],
                score_breakdown["spoken"],
                score_breakdown["novelty"],
                score_breakdown["repetition_penalty"],
                score_breakdown["generic_penalty"],
            )

        winner_idx, winner_script, winner_scores = max(
            scored,
            key=lambda item: (
                item[2]["total"],
                item[2]["hook"],
                item[2]["specificity"],
                item[2]["concrete"],
                item[2]["novelty"],
            ),
        )
        logger.info(
            "Selected candidate: %s (total=%.1f, hook=%.1f, specificity=%.1f, novelty=%.1f)",
            winner_idx,
            winner_scores["total"],
            winner_scores["hook"],
            winner_scores["specificity"],
            winner_scores["novelty"],
        )
        if lane == "storytelling" and not topic_is_ai:
            story_report = self._storytelling_anchor_report(
                hook=str(winner_script.get("hook", "") or ""),
                narration=str(winner_script.get("narration", "") or ""),
                topic=topic,
            )
            guardrail_fired = bool(winner_script.get("_story_anchor_guardrail_fired", False))
            logger.info(
                "Story debug: archetype=%s guardrail=%s anchors=hook:%s body:%s repeats=s2:%s,s3:%s progression_penalty=%.1f selected_total=%.1f",
                story_report.get("archetype", "generic_story"),
                "yes" if guardrail_fired else "no",
                story_report.get("hook_anchor_hits", 0),
                story_report.get("first_two_anchor_hits", 0),
                "yes" if story_report.get("repeat_s2_hook") else "no",
                "yes" if story_report.get("repeat_s3_s2") else "no",
                float(story_report.get("progression_penalty", 0.0)),
                float(winner_scores.get("total", 0.0)),
            )
            winner_script.pop("_story_anchor_guardrail_fired", None)
        return winner_script

    def _score_script_candidate(self, script: dict, topic: dict, lane: str, topic_is_ai: bool) -> dict:
        hook = str(script.get("hook", "") or "").strip()
        narration = str(script.get("narration", "") or "").strip()
        body = self._strip_hook_prefix_from_narration(narration=narration, hook=hook)

        hook_score = float(self._hook_quality_score(hook=hook, lane=lane, topic=topic))
        body_score = float(self._body_quality_score(body=body, topic=topic, lane=lane, topic_is_ai=topic_is_ai))
        lane_score = float(
            self._lane_coherence_score(
                hook=hook,
                narration=narration,
                lane=lane,
                topic=topic,
                topic_is_ai=topic_is_ai,
            )
        )
        concrete_score = float(self._concreteness_score(body=body, lane=lane))
        specificity_score = float(self._specificity_score(body=body, lane=lane, topic=topic, topic_is_ai=topic_is_ai))
        spoken_score = float(self._spoken_naturalness_score(narration=narration))
        generic_penalty = float(self._generic_language_penalty(hook=hook, body=body))
        repetition_penalty = float(self._repetition_penalty(hook=hook, narration=narration))
        storytelling_anchor_penalty = 0.0
        storytelling_dimensions: dict | None = None
        storytelling_report: dict | None = None
        if lane == "storytelling" and not topic_is_ai:
            storytelling_dimensions = self._storytelling_dimension_scores(hook=hook, narration=narration, topic=topic)
            storytelling_report = storytelling_dimensions.get("report", {})
            storytelling_anchor_penalty = float(
                self._storytelling_anchor_penalty(hook=hook, narration=narration, topic=topic)
            )

        total = (
            (hook_score * 0.30)
            + (specificity_score * 0.20)
            + (concrete_score * 0.18)
            + (body_score * 0.16)
            + (lane_score * 0.10)
            + (spoken_score * 0.06)
            - generic_penalty
            - repetition_penalty
            - storytelling_anchor_penalty
        )
        if storytelling_dimensions and storytelling_report:
            dimension_composite = float(storytelling_dimensions.get("composite", 0.0))
            total = (total * 0.50) + (dimension_composite * 0.50)

            floor = 0.0
            has_anchor = bool(
                storytelling_report.get("hook_hits", 0) > 0
                or storytelling_report.get("first_two_anchor_hits", 0) > 0
            )
            has_sensory_or_detail = bool(
                storytelling_report.get("s2_has_sensory")
                or self._has_story_sensory_detail(storytelling_report.get("sentence_1", ""))
            )
            has_reveal_or_landing = bool(
                storytelling_report.get("s3_has_reveal")
                or storytelling_report.get("s4_has_landing")
            )
            structurally_valid = (
                has_anchor
                and has_sensory_or_detail
                and has_reveal_or_landing
                and not storytelling_report.get("s4_dead_end")
            )
            if (
                structurally_valid
                and storytelling_report.get("s2_has_sensory")
                and storytelling_report.get("s3_has_reveal")
                and storytelling_report.get("s4_has_landing")
                and not storytelling_report.get("repeat_s2_hook")
                and not storytelling_report.get("repeat_s3_s2")
            ):
                floor = 44.0
            elif structurally_valid and storytelling_report.get("s4_has_landing"):
                floor = 40.0
            elif structurally_valid:
                floor = 35.0
            elif has_anchor and has_reveal_or_landing:
                floor = 30.0

            truly_broken = (
                storytelling_report.get("hook_hits", 0) == 0
                and storytelling_report.get("first_two_anchor_hits", 0) == 0
                and not storytelling_report.get("s2_has_sensory")
                and not storytelling_report.get("s3_has_reveal")
                and not storytelling_report.get("s4_has_landing")
            )
            if not truly_broken:
                total = max(total, floor)

        return {
            "hook": round(hook_score, 1),
            "body": round(body_score, 1),
            "lane": round(lane_score, 1),
            "concrete": round(concrete_score, 1),
            "specificity": round(specificity_score, 1),
            "spoken": round(spoken_score, 1),
            "novelty": 100.0,
            "novelty_penalty": 0.0,
            "repetition_penalty": round(repetition_penalty, 1),
            "generic_penalty": round(generic_penalty, 1),
            "story_anchor_penalty": round(storytelling_anchor_penalty, 1),
            "story_progression_penalty": round(
                float(storytelling_report.get("progression_penalty", 0.0)) if storytelling_report else 0.0,
                1,
            ),
            "story_archetype_fit": round(
                float(storytelling_dimensions.get("archetype_fit", 0.0)) if storytelling_dimensions else 0.0,
                1,
            ),
            "story_anchor_presence": round(
                float(storytelling_dimensions.get("anchor_presence", 0.0)) if storytelling_dimensions else 0.0,
                1,
            ),
            "story_progression": round(
                float(storytelling_dimensions.get("progression", 0.0)) if storytelling_dimensions else 0.0,
                1,
            ),
            "story_reveal_strength": round(
                float(storytelling_dimensions.get("reveal_strength", 0.0)) if storytelling_dimensions else 0.0,
                1,
            ),
            "story_final_landing": round(
                float(storytelling_dimensions.get("final_landing", 0.0)) if storytelling_dimensions else 0.0,
                1,
            ),
            "story_originality": round(
                float(storytelling_dimensions.get("originality", 0.0)) if storytelling_dimensions else 0.0,
                1,
            ),
            "story_composite": round(
                float(storytelling_dimensions.get("composite", 0.0)) if storytelling_dimensions else 0.0,
                1,
            ),
            "total": round(max(0.0, total), 1),
        }

    def _body_quality_score(self, body: str, topic: dict, lane: str, topic_is_ai: bool) -> int:
        text = str(body or "").strip()
        lowered = text.lower()
        if not text:
            return 0

        if lane == "ai_productivity":
            score = 72 if not self._is_weak_ai_productivity_body(text) else 42
            if self._has_concrete_ai_use_case(text):
                score += 12
            if self._has_action_sequence(text):
                score += 8
            manual_hits = len(
                re.findall(r"\b(by hand|manual|repetitive|admin|inbox|email|notes|checklist|follow-?up)\b", lowered)
            )
            score += min(10, manual_hits * 3)
            score -= min(24, self._generic_phrase_hits(text) * 6)
            if re.search(r"\b(productivity|workflow|efficiency)\b", lowered) and not self._has_concrete_ai_use_case(text):
                score -= 10
            return max(20, min(98, score))
        if lane == "storytelling" and not topic_is_ai:
            score = 78 if not self._is_weak_storytelling_body(text, topic) else 44
            scene_hits = len(re.findall(r"\b(hallway|door|room|stairs|window|floor|voice|shadow)\b", lowered))
            tension_hits = len(re.findall(r"\b(heard|saw|whispered|quiet|wrong|froze|ran|left)\b", lowered))
            label_hits = len(re.findall(r"\b(story|mystery|confession|plot twist|twist)\b", lowered))
            progression_hits = self._storytelling_progression_hits(text)
            score += min(12, scene_hits * 3)
            score += min(10, tension_hits * 2)
            score += min(14, progression_hits * 3)
            if self._has_story_sensory_detail(text):
                score += 6
            sentences = self._sentence_list(text)
            final_line = sentences[-1] if sentences else ""
            archetype = self._storytelling_topic_context(topic).get("archetype", "generic_story")
            if (
                self._story_line_has_consequence(final_line)
                or self._story_line_has_reveal(final_line, archetype=archetype)
                or self._story_line_has_final_image(final_line)
            ):
                score += 8
            else:
                score -= 6
            score -= min(18, max(0, label_hits - 1) * 6)
            score -= min(18, self._vague_storytelling_hits(text) * 9)
            if self._has_ai_productivity_contamination(text):
                score -= 30
            score -= min(16, self._generic_phrase_hits(text) * 5)
            return max(10, min(98, score))
        if lane == "commentary":
            score = 62
            if re.search(r"\b(hot take|unpopular|wrong|nobody says|truth)\b", lowered):
                score += 22
            if re.search(r"\b(because|example|evidence|proof)\b", lowered):
                score += 10
            score -= min(15, self._generic_phrase_hits(text) * 5)
            return max(20, min(96, score))
        if lane == "motivation":
            score = 62
            if re.search(r"\b(stop|excuses|discipline|act now|no one)\b", lowered):
                score += 22
            if re.search(r"\b(today|now|right now|before)\b", lowered):
                score += 8
            score -= min(15, self._generic_phrase_hits(text) * 5)
            return max(20, min(96, score))
        return 70

    def _lane_coherence_score(self, hook: str, narration: str, lane: str, topic: dict, topic_is_ai: bool) -> int:
        hook_text = str(hook or "")
        narration_text = str(narration or "")
        hook_lower = hook_text.lower()
        narration_lower = narration_text.lower()
        if lane == "storytelling" and not topic_is_ai:
            if self._has_ai_productivity_contamination(hook_text) or self._has_ai_productivity_contamination(narration_text):
                return 10
            score = 76 if not self._is_weak_storytelling_body(narration_text, topic) else 46
            scene_hits = len(re.findall(r"\b(hallway|door|room|whispered|heard|quiet|name|left)\b", narration_lower))
            progression_hits = self._storytelling_progression_hits(narration_text)
            topic_terms = self._storytelling_topic_terms(topic)
            alignment_hits = sum(1 for term in topic_terms if term in (hook_lower + " " + narration_lower))
            score += min(18, scene_hits * 3)
            score += min(14, progression_hits * 3)
            score += min(14, alignment_hits * 3)
            anchor_report = self._storytelling_anchor_report(hook=hook_text, narration=narration_text, topic=topic)
            score += min(12, anchor_report["hook_hits"] * 3)
            score += min(8, anchor_report["first_two_anchor_hits"] * 2)
            if anchor_report["hook_hits"] == 0 and anchor_report["first_two_anchor_hits"] == 0:
                score -= 18
            elif not anchor_report["anchor_by_sentence2"]:
                score -= 10
            if anchor_report["hook_fit_count"] > 1:
                score -= min(10, (anchor_report["hook_fit_count"] - 1) * 4)
            score -= min(10, anchor_report["mismatched_opener_penalty"])
            if self._contains_topic_label_verbatim(narration_text, self._topic_label(topic)):
                score -= 10
            score -= min(16, self._vague_storytelling_hits(narration_text) * 8)
            return max(10, min(98, score))
        if lane == "ai_productivity":
            score = 56
            if self._has_concrete_ai_use_case(narration_text):
                score += 20
            if self._has_action_sequence(narration_text):
                score += 12
            if self._has_weak_ai_productivity_phrasing(narration_text):
                score -= 20
            if re.search(r"\b(hallway|door|whispered|room went quiet)\b", hook_lower + " " + narration_lower):
                score -= 16
            return max(10, min(98, score))
        if lane == "commentary":
            score = 62
            if re.search(r"\b(hot take|unpopular|wrong|nobody says)\b", narration_lower):
                score += 24
            return max(20, min(96, score))
        if lane == "motivation":
            score = 62
            if re.search(r"\b(stop|discipline|excuses|act now)\b", narration_lower):
                score += 24
            return max(20, min(96, score))
        return 70

    def _concreteness_score(self, body: str, lane: str) -> int:
        lowered = str(body or "").lower()
        if not lowered:
            return 0
        if lane == "ai_productivity":
            task_hits = len(
                re.findall(
                    r"\b(email|emails|inbox|lecture|notes?|checklist|writing|draft|follow-?up|admin|calendar)\b",
                    lowered,
                )
            )
            action_hits = len(
                re.findall(
                    r"\b(summarize|draft|reply|clean|turn|convert|automate|organize|paste|review|send)\b",
                    lowered,
                )
            )
            score = 34 + min(30, task_hits * 6) + min(22, action_hits * 4)
            if self._has_action_sequence(lowered):
                score += 10
            if not self._has_concrete_ai_use_case(lowered):
                score -= 18
            if self._has_weak_ai_productivity_phrasing(lowered):
                score -= 16
            return max(10, min(98, score))
        if lane == "storytelling":
            scene_hits = len(re.findall(r"\b(hallway|door|room|stairs|window|floor|shadow|voice)\b", lowered))
            sensory_hits = len(re.findall(r"\b(heard|saw|whispered|quiet|cold|steps|name|froze)\b", lowered))
            progression_hits = self._storytelling_progression_hits(lowered)
            score = 34 + min(34, scene_hits * 6) + min(30, sensory_hits * 5) + min(14, progression_hits * 3)
            if self._has_ai_productivity_contamination(lowered):
                score -= 30
            score -= min(14, self._vague_storytelling_hits(lowered) * 7)
            return max(10, min(98, score))
        if lane == "commentary":
            detail_hits = len(re.findall(r"\b(example|because|proof|actually)\b", lowered))
            return max(20, min(95, 55 + (detail_hits * 8)))
        if lane == "motivation":
            action_hits = len(re.findall(r"\b(stop|start|act|do|ship|train)\b", lowered))
            return max(20, min(95, 55 + (action_hits * 7)))
        return 65

    def _specificity_score(self, body: str, lane: str, topic: dict, topic_is_ai: bool) -> int:
        text = str(body or "").strip()
        lowered = text.lower()
        if not text:
            return 0

        if lane == "ai_productivity":
            score = 28
            painful_markers = len(re.findall(r"\b(by hand|manual|repetitive|admin|slow)\b", lowered))
            use_case_markers = len(
                re.findall(
                    r"\b(emails?|inbox|lecture notes?|checklist|writing|follow-?up|notes?)\b",
                    lowered,
                )
            )
            score += 22 if painful_markers > 0 else 0
            score += 24 if self._has_concrete_ai_use_case(text) else 0
            score += min(14, use_case_markers * 4)
            score += 20 if self._has_action_sequence(text) else 0
            if self._has_weak_ai_productivity_phrasing(text):
                score -= 22
            return max(10, min(99, score))

        if lane == "storytelling" and not topic_is_ai:
            scene_markers = len(re.findall(r"\b(hallway|door|room|stairs|voice|shadow|window)\b", lowered))
            sensory_markers = len(re.findall(r"\b(heard|saw|whispered|quiet|cold|steps|name|froze)\b", lowered))
            tension_markers = len(re.findall(r"\b(then|next|after|suddenly|when)\b", lowered))
            label_hits = len(re.findall(r"\b(story|mystery|confession|twist)\b", lowered))
            progression_hits = self._storytelling_progression_hits(text)
            topic_terms = self._storytelling_topic_terms(topic)
            alignment_hits = sum(1 for term in topic_terms if term in lowered)
            score = (
                32
                + min(30, scene_markers * 6)
                + min(24, sensory_markers * 5)
                + min(12, tension_markers * 4)
                + min(14, progression_hits * 3)
                + min(16, alignment_hits * 4)
            )
            anchor_report = self._storytelling_anchor_report(hook="", narration=text, topic=topic)
            score += min(8, anchor_report["first_two_anchor_hits"] * 2)
            if anchor_report["hook_hits"] == 0 and anchor_report["first_two_anchor_hits"] == 0:
                score -= 12
            elif not anchor_report["anchor_by_sentence2"]:
                score -= 8
            if anchor_report["hook_fit_count"] > 1:
                score -= min(8, (anchor_report["hook_fit_count"] - 1) * 4)
            score -= min(8, anchor_report["filler_penalty"])
            score -= min(24, max(0, label_hits - 1) * 6)
            score -= min(18, self._vague_storytelling_hits(text) * 9)
            if self._has_ai_productivity_contamination(text):
                score -= 30
            return max(8, min(99, score))

        if lane == "commentary":
            markers = len(re.findall(r"\b(hot take|unpopular|wrong|because|example|proof)\b", lowered))
            return max(20, min(95, 52 + (markers * 7)))

        if lane == "motivation":
            markers = len(re.findall(r"\b(stop|discipline|excuses|now|today|before)\b", lowered))
            return max(20, min(95, 52 + (markers * 7)))

        return 65

    def _spoken_naturalness_score(self, narration: str) -> int:
        text = str(narration or "").strip()
        if not text:
            return 0

        sentences = self._sentence_list(text)
        if not sentences:
            return 45

        word_counts = [len(re.findall(r"[a-z0-9']+", sentence.lower())) for sentence in sentences]
        avg_words = sum(word_counts) / max(1, len(word_counts))

        score = 80
        if 6 <= avg_words <= 13:
            score += 8
        elif avg_words > 17:
            score -= 20
        elif avg_words < 4:
            score -= 12

        long_sentences = sum(1 for count in word_counts if count > 20)
        score -= long_sentences * 8

        first_words = []
        for sentence in sentences:
            parts = re.findall(r"[a-z0-9']+", sentence.lower())
            if parts:
                first_words.append(parts[0])
        repeated_starts = max((first_words.count(word) for word in set(first_words)), default=1)
        if repeated_starts >= 3:
            score -= 14

        score -= min(24, self._generic_phrase_hits(text) * 6)
        return max(15, min(97, score))

    def _apply_novelty_adjustments(self, scored: list[tuple[int, dict, dict]]) -> None:
        count = len(scored)
        if count <= 1:
            for _, _, breakdown in scored:
                breakdown["novelty"] = 100.0
            return

        max_similarity = [0.0 for _ in range(count)]
        similarity_sum = [0.0 for _ in range(count)]
        similarity_count = [0 for _ in range(count)]

        for i in range(count):
            for j in range(i + 1, count):
                sim = self._candidate_similarity(scored[i][1], scored[j][1])
                max_similarity[i] = max(max_similarity[i], sim)
                max_similarity[j] = max(max_similarity[j], sim)
                similarity_sum[i] += sim
                similarity_sum[j] += sim
                similarity_count[i] += 1
                similarity_count[j] += 1

                if sim >= 0.72:
                    left = scored[i][2]
                    right = scored[j][2]
                    left_rank = (left["total"], left["hook"], left["specificity"], left["concrete"], left["spoken"])
                    right_rank = (right["total"], right["hook"], right["specificity"], right["concrete"], right["spoken"])
                    weaker_idx = i if left_rank < right_rank else j
                    novelty_penalty = round(4 + ((sim - 0.72) * 16), 1)
                    scored[weaker_idx][2]["novelty_penalty"] += novelty_penalty

        for idx in range(count):
            breakdown = scored[idx][2]
            avg_similarity = similarity_sum[idx] / similarity_count[idx] if similarity_count[idx] else 0.0
            novelty_score = 100 - (max_similarity[idx] * 38) - (avg_similarity * 22)
            novelty_score = max(30.0, min(100.0, novelty_score))
            breakdown["novelty"] = round(novelty_score, 1)
            updated_total = max(0.0, float(breakdown.get("total", 0.0)) - float(breakdown.get("novelty_penalty", 0.0)))
            story_composite = float(breakdown.get("story_composite", 0.0))
            hook_score = float(breakdown.get("hook", 0.0))
            anchor_presence = float(breakdown.get("story_anchor_presence", 0.0))
            reveal_strength = float(breakdown.get("story_reveal_strength", 0.0))
            final_landing = float(breakdown.get("story_final_landing", 0.0))
            if story_composite >= 62 and anchor_presence >= 40 and reveal_strength >= 48 and final_landing >= 48:
                updated_total = max(updated_total, 42.0)
            elif story_composite >= 52 and anchor_presence >= 32 and (reveal_strength >= 42 or final_landing >= 42):
                updated_total = max(updated_total, 38.0)
            elif story_composite >= 42 and anchor_presence >= 24 and (reveal_strength >= 34 or final_landing >= 34):
                updated_total = max(updated_total, 35.0)
            elif hook_score >= 85 and anchor_presence >= 22:
                updated_total = max(updated_total, 20.0)
            breakdown["total"] = round(updated_total, 1)

    def _score_rank_tuple(self, breakdown: dict) -> tuple[float, float, float, float, float]:
        return (
            float(breakdown.get("total", 0.0)),
            float(breakdown.get("hook", 0.0)),
            float(breakdown.get("specificity", 0.0)),
            float(breakdown.get("concrete", 0.0)),
            float(breakdown.get("spoken", 0.0)),
        )

    def _storytelling_hook_similarity(self, left_hook: str, right_hook: str) -> float:
        return self._jaccard_similarity(
            self._tokenize_for_scoring(left_hook),
            self._tokenize_for_scoring(right_hook),
        )

    def _apply_storytelling_diversity_adjustments(self, scored: list[tuple[int, dict, dict]]) -> None:
        count = len(scored)
        if count <= 1:
            return

        penalties = [0.0 for _ in range(count)]
        caps = [100.0 for _ in range(count)]

        for i in range(count):
            for j in range(i + 1, count):
                left_hook = str(scored[i][1].get("hook", "") or "").strip()
                right_hook = str(scored[j][1].get("hook", "") or "").strip()
                hook_similarity = self._storytelling_hook_similarity(left_hook=left_hook, right_hook=right_hook)
                if hook_similarity < 0.60:
                    continue

                left_rank = self._score_rank_tuple(scored[i][2])
                right_rank = self._score_rank_tuple(scored[j][2])
                weaker_idx = i if left_rank < right_rank else j
                stronger_idx = j if weaker_idx == i else i

                if hook_similarity >= 0.75:
                    weaker_penalty = round(12 + ((hook_similarity - 0.75) * 32), 1)
                    stronger_penalty = round(6 + ((hook_similarity - 0.75) * 20), 1)
                    penalties[weaker_idx] += weaker_penalty
                    penalties[stronger_idx] += stronger_penalty
                    caps[weaker_idx] = min(caps[weaker_idx], 70.0)
                    caps[stronger_idx] = min(caps[stronger_idx], 82.0)
                else:
                    weaker_penalty = round(7 + ((hook_similarity - 0.60) * 22), 1)
                    stronger_penalty = round(3 + ((hook_similarity - 0.60) * 12), 1)
                    penalties[weaker_idx] += weaker_penalty
                    penalties[stronger_idx] += stronger_penalty
                    caps[weaker_idx] = min(caps[weaker_idx], 82.0)
                    caps[stronger_idx] = min(caps[stronger_idx], 92.0)

        for idx in range(count):
            breakdown = scored[idx][2]
            penalty = penalties[idx]
            cap = caps[idx]
            if penalty <= 0.0 and cap >= 100.0:
                continue

            updated_total = max(0.0, float(breakdown.get("total", 0.0)) - penalty)
            updated_total = min(updated_total, cap)
            # Keep duplicate punishment strong, but avoid collapsing anchored stories to zero.
            diversity_floor = 0.0
            story_composite = float(breakdown.get("story_composite", 0.0))
            reveal_strength = float(breakdown.get("story_reveal_strength", 0.0))
            final_landing = float(breakdown.get("story_final_landing", 0.0))
            anchor_presence = float(breakdown.get("story_anchor_presence", 0.0))
            if story_composite >= 60 and anchor_presence >= 38 and reveal_strength >= 46 and final_landing >= 46:
                diversity_floor = 40.0
            elif story_composite >= 50 and anchor_presence >= 30 and (reveal_strength >= 40 or final_landing >= 40):
                diversity_floor = 37.0
            elif story_composite >= 42 and anchor_presence >= 24 and (reveal_strength >= 32 or final_landing >= 32):
                diversity_floor = 35.0
            updated_total = max(updated_total, diversity_floor)
            breakdown["novelty_penalty"] = round(float(breakdown.get("novelty_penalty", 0.0)) + penalty, 1)
            breakdown["total"] = round(updated_total, 1)

    def _candidate_similarity(self, left_script: dict, right_script: dict) -> float:
        left_hook = str(left_script.get("hook", "") or "").strip()
        right_hook = str(right_script.get("hook", "") or "").strip()
        left_narration = str(left_script.get("narration", "") or "").strip()
        right_narration = str(right_script.get("narration", "") or "").strip()

        left_body = self._strip_hook_prefix_from_narration(left_narration, left_hook)
        right_body = self._strip_hook_prefix_from_narration(right_narration, right_hook)

        hook_similarity = self._jaccard_similarity(
            self._tokenize_for_scoring(left_hook),
            self._tokenize_for_scoring(right_hook),
        )
        body_similarity = self._jaccard_similarity(
            self._tokenize_for_scoring(left_body),
            self._tokenize_for_scoring(right_body),
        )
        pattern_similarity = self._jaccard_similarity(
            set(self._sentence_start_signature(left_narration)),
            set(self._sentence_start_signature(right_narration)),
        )

        return (hook_similarity * 0.45) + (body_similarity * 0.40) + (pattern_similarity * 0.15)

    def _generic_phrase_catalog(self) -> list[str]:
        return [
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

    def _generic_phrase_hits(self, text: str) -> int:
        lowered = str(text or "").lower()
        return sum(1 for phrase in self._generic_phrase_catalog() if phrase in lowered)

    def _generic_language_penalty(self, hook: str, body: str) -> float:
        hook_hits = self._generic_phrase_hits(hook)
        body_hits = self._generic_phrase_hits(body)
        tutorial_starts = [
            r"^\s*new to\b",
            r"^\s*here are\b",
            r"^\s*imagine\b",
            r"^\s*if you want to\b",
            r"^\s*this tool can\b",
        ]
        tutorial_penalty = 6 if any(re.search(pattern, str(hook or "").lower()) for pattern in tutorial_starts) else 0
        return min(42.0, (hook_hits * 7.0) + (body_hits * 5.0) + tutorial_penalty)

    def _repetition_penalty(self, hook: str, narration: str) -> float:
        sentences = self._sentence_list(narration)
        if not sentences:
            return 0.0

        penalty = 0.0
        if len(sentences) >= 2:
            first_sim = self._jaccard_similarity(
                self._tokenize_for_scoring(sentences[0]),
                self._tokenize_for_scoring(sentences[1]),
            )
            if first_sim >= 0.60:
                penalty += 10.0

        tokens = self._tokenize_for_scoring(narration)
        if tokens:
            freq = {}
            for token in tokens:
                freq[token] = freq.get(token, 0) + 1
            top_count = max(freq.values()) if freq else 0
            if top_count >= 3 and (top_count / max(1, len(tokens))) > 0.14:
                penalty += 8.0

        starts = self._sentence_start_signature(narration)
        start_repeats = max((starts.count(start) for start in set(starts)), default=1)
        if start_repeats >= 3:
            penalty += 7.0

        lengths = [len(re.findall(r"[a-z0-9']+", s.lower())) for s in sentences]
        buckets = [min(5, max(1, int(length / 4))) for length in lengths]
        if len(buckets) >= 4 and len(set(buckets)) <= 2:
            penalty += 5.0

        return min(34.0, penalty)

    def _has_action_sequence(self, text: str) -> bool:
        lowered = str(text or "").lower()
        sequence_markers = len(re.findall(r"\b(first|then|next|finally|after|before|step)\b", lowered))
        action_markers = len(
            re.findall(
                r"\b(summarize|draft|reply|clean|organize|sort|convert|turn|paste|review|send|automate)\b",
                lowered,
            )
        )
        return sequence_markers >= 1 and action_markers >= 2

    def _sentence_list(self, text: str) -> list[str]:
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(text or "").strip()) if s.strip()]

    def _sentence_start_signature(self, text: str) -> list[str]:
        signatures: list[str] = []
        for sentence in self._sentence_list(text)[:4]:
            words = re.findall(r"[a-z0-9']+", sentence.lower())
            if words:
                signatures.append(words[0])
        return signatures

    def _tokenize_for_scoring(self, text: str) -> list[str]:
        stop_words = {
            "the", "a", "an", "and", "or", "to", "for", "of", "in", "on", "with", "this", "that", "it",
            "is", "are", "was", "were", "be", "you", "your", "i", "we", "they", "he", "she", "my", "our",
            "at", "as", "by", "from", "but", "if", "then",
        }
        tokens = re.findall(r"[a-z0-9']+", str(text or "").lower())
        return [tok for tok in tokens if tok not in stop_words and len(tok) > 2]

    def _jaccard_similarity(self, left: set | list, right: set | list) -> float:
        left_set = set(left or [])
        right_set = set(right or [])
        if not left_set and not right_set:
            return 1.0
        if not left_set or not right_set:
            return 0.0
        return len(left_set & right_set) / len(left_set | right_set)

    def _apply_quality_guardrails(self, script: dict, topic: dict) -> dict:
        hook = str(script.get("hook", "") or "").strip()
        lane = self._detect_content_lane(topic)
        topic_is_ai = self._topic_mentions_ai(topic)

        if lane == "storytelling" and not topic_is_ai and hook and self._has_ai_productivity_contamination(hook):
            rewritten_hook = self._rewrite_storytelling_hook_humanized(topic)
            if rewritten_hook:
                script["hook"] = rewritten_hook
                hook = rewritten_hook
                logger.info("Hook lane coherence guardrail applied (storytelling hook decontaminated).")

        script = self._apply_hook_quality_pass(
            script=script,
            topic=topic,
            lane=lane,
            topic_is_ai=topic_is_ai,
        )

        narration = str(script.get("narration", "") or "").strip()
        if (
            lane == "storytelling"
            and not topic_is_ai
            and narration
            and self._has_ai_productivity_contamination(narration)
        ):
            rewritten = self._rewrite_storytelling_narration(topic=topic, hook=str(script.get("hook", "") or "").strip())
            if rewritten:
                script["narration"] = rewritten
                logger.info("Narration lane coherence guardrail applied (storytelling contamination removed).")

        if lane == "storytelling" and not topic_is_ai:
            script = self._enforce_storytelling_anchor_guardrail(script=script, topic=topic)

        script = self._humanize_script_language(
            script=script,
            topic=topic,
            lane=lane,
            topic_is_ai=topic_is_ai,
        )
        return script

    def _enforce_storytelling_anchor_guardrail(self, script: dict, topic: dict) -> dict:
        hook = str(script.get("hook", "") or "").strip()
        narration = str(script.get("narration", "") or "").strip()
        report = self._storytelling_anchor_report(hook=hook, narration=narration, topic=topic)

        needs_rewrite = (
            report["hook_hits"] == 0
            or report["first_two_anchor_hits"] == 0
            or not report["anchor_by_sentence2"]
            or report["mismatched_opener_penalty"] > 0
            or report["repeat_s2_hook"]
            or report["repeat_s3_s2"]
            or report["s4_dead_end"]
            or not report["s4_has_landing"]
        )
        if not needs_rewrite:
            script["_story_anchor_guardrail_fired"] = False
            return script

        rewritten_hook = self._rewrite_storytelling_hook_humanized(topic)
        if rewritten_hook:
            script["hook"] = rewritten_hook
            hook = rewritten_hook

        rewritten_narration = self._rewrite_storytelling_narration(topic=topic, hook=hook)
        if rewritten_narration:
            script["narration"] = rewritten_narration
            self._ensure_narration_starts_with_hook(script)
            script["words"] = len(str(script.get("narration", "") or "").split())

        logger.info(
            "Storytelling anchor guardrail applied (archetype=%s, hook_hits=%s, first_two_anchor_hits=%s).",
            report["archetype"],
            report["hook_hits"],
            report["first_two_anchor_hits"],
        )
        script["_story_anchor_guardrail_fired"] = True
        return script

    def _humanize_script_language(self, script: dict, topic: dict, lane: str, topic_is_ai: bool) -> dict:
        original_narration = str(script.get("narration", "") or "").strip()
        narration = original_narration
        changed = False
        hook = str(script.get("hook", "") or "").strip()

        if lane == "storytelling" and not topic_is_ai:
            if self._is_weak_storytelling_body(narration=narration, topic=topic):
                rewritten_narration = self._rewrite_storytelling_narration(topic=topic, hook=hook)
                if rewritten_narration:
                    narration = rewritten_narration
                    changed = True

        if lane == "ai_productivity":
            body_probe = self._strip_hook_prefix_from_narration(narration=narration, hook=hook)
            if self._is_weak_ai_productivity_body(body_probe):
                rewritten_narration = self._rewrite_ai_productivity_narration(topic=topic, hook=hook)
                if rewritten_narration:
                    narration = rewritten_narration
                    changed = True

        if narration != original_narration:
            script["narration"] = narration

        self._ensure_narration_starts_with_hook(script)
        script["words"] = len(str(script.get("narration", "") or "").split())
        if changed:
            logger.info(f"Body humanization pass applied ({lane} lane).")
        return script

    def _strip_hook_prefix_from_narration(self, narration: str, hook: str) -> str:
        body = str(narration or "").strip()
        hook_text = str(hook or "").strip()
        if not body or not hook_text:
            return body

        if body.lower().startswith(hook_text.lower()):
            remainder = body[len(hook_text):].strip()
            return remainder or body
        return body

    def _is_weak_ai_productivity_body(self, narration: str) -> bool:
        text = str(narration or "").strip()
        lowered = text.lower()
        if not text:
            return True

        if self._has_weak_ai_productivity_phrasing(text):
            return True
        if not self._has_concrete_ai_use_case(text):
            return True

        abstract_terms = len(
            re.findall(r"\b(productivity|workflow|efficiency|results|impact|tools?|system)\b", lowered)
        )
        concrete_terms = len(
            re.findall(
                r"\b(email|inbox|lecture|notes?|writing|draft|edit|checklist|admin|calendar|meeting|follow up|follow-up)\b",
                lowered,
            )
        )
        return abstract_terms >= 3 and concrete_terms <= 1

    def _is_weak_storytelling_body(self, narration: str, topic: dict) -> bool:
        text = str(narration or "").strip()
        lowered = text.lower()
        if not text:
            return True

        narration_sentences = self._sentence_list(text)
        hook_probe = narration_sentences[0] if narration_sentences else ""
        anchor_report = self._storytelling_anchor_report(hook=hook_probe, narration=text, topic=topic)
        if anchor_report["hook_hits"] == 0:
            return True
        if anchor_report["first_two_anchor_hits"] == 0:
            return True
        if not anchor_report["anchor_by_sentence2"]:
            return True
        if anchor_report["mismatched_opener_penalty"] > 0:
            return True
        if anchor_report["filler_penalty"] > 0:
            return True
        if anchor_report["repeat_s2_hook"] or anchor_report["repeat_s3_s2"]:
            return True
        if anchor_report["s4_dead_end"] or not anchor_report["s4_has_landing"]:
            return True

        topic_label = self._topic_label(topic)
        if self._contains_topic_label_verbatim(text, topic_label):
            return True
        if self._has_robotic_storytelling_phrasing(text, topic_label):
            return True

        category_count = len(re.findall(r"\b(story|mystery|confession|plot twist|twist)\b", lowered))
        scene_cues = len(
            re.findall(
                r"\b(heard|saw|happened|detail|wrong|left|door|hallway|steps|voice|whispered|looked)\b",
                lowered,
            )
        )
        progression_hits = self._storytelling_progression_hits(text)
        if self._vague_storytelling_hits(text) > 0:
            return True
        if progression_hits < 2:
            return True
        if scene_cues < 2:
            return True
        return category_count >= 2 and scene_cues <= 2

    def _detect_content_lane(self, topic: dict) -> str:
        topic_text = " ".join(
            [
                str(topic.get("title", "") or ""),
                str(topic.get("summary", "") or ""),
            ]
        ).lower()
        topic_tokens = set(re.findall(r"[a-z0-9]+", topic_text))

        if any(word in topic_tokens for word in {"commentary", "opinion", "debate", "controversial", "rant"}):
            return "commentary"
        if any(word in topic_tokens for word in {"motivation", "discipline", "mindset", "consistency", "success"}):
            return "motivation"

        ai_priority_terms = {
            "ai",
            "chatgpt",
            "gpt",
            "claude",
            "automation",
            "workflow",
            "productivity",
            "tool",
            "tools",
            "email",
            "inbox",
            "lecture",
            "meeting",
            "checklist",
            "admin",
            "study",
        }
        if any(term in topic_tokens for term in ai_priority_terms):
            return "ai_productivity"

        storytelling_scene_terms = {
            "hallway",
            "door",
            "midnight",
            "whisper",
            "whispered",
            "room",
            "quiet",
            "lights",
            "note",
            "name",
            "heard",
            "left",
            "moved",
            "creepy",
        }
        if any(term in topic_tokens for term in storytelling_scene_terms):
            return "storytelling"
        if re.search(
            r"\b(i heard|what happened|i should have left|went quiet|behind the door|lights went out)\b",
            topic_text,
        ):
            return "storytelling"

        lane_rules: list[tuple[str, set[str]]] = [
            ("storytelling", {"story", "storytelling", "mystery", "horror", "confession", "twist"}),
            ("ai_productivity", {"ai", "tech", "tool", "tools", "productivity", "automation", "workflow"}),
        ]
        for lane, keywords in lane_rules:
            if any(word in topic_tokens for word in keywords):
                return lane

        # Only if topic itself is ambiguous, fall back to repo niche defaults.
        niche = str(self.config.get("NICHE", "")).strip().lower()
        if niche == "motivation":
            return "motivation"
        if niche in {"ai_tools", "ai"}:
            return "ai_productivity"
        return "ai_productivity"

    def _topic_mentions_ai(self, topic: dict) -> bool:
        text = " ".join(
            [
                str(topic.get("title", "") or ""),
                str(topic.get("summary", "") or ""),
            ]
        ).lower()
        ai_terms = {"ai", "chatgpt", "gpt", "claude", "notion", "automation", "workflow", "productivity"}
        tokens = set(re.findall(r"[a-z0-9]+", text))
        return any(term in tokens for term in ai_terms)

    def _lane_hook_examples(self, lane: str, default_examples: list[str]) -> list[str]:
        lane_examples = {
            "ai_productivity": [
                "You're wasting time if you still do this manually.",
                "Stop doing your workflow the slow way.",
                "This one mistake is draining hours from your day.",
            ],
            "storytelling": [
                "A folded note slid under my door with my name on it.",
                "Something moved in the hallway outside my doorframe.",
                "After one sentence, nobody moved in the room.",
            ],
            "commentary": [
                "Hot take: most people are completely wrong about this.",
                "Nobody wants to say this out loud, so I will.",
                "Unpopular opinion, but this trend is mostly nonsense.",
            ],
            "motivation": [
                "You are not stuck. You are avoiding hard reps.",
                "Stop waiting for motivation. Start before you feel ready.",
                "Your excuses are louder than your effort right now.",
            ],
        }
        selected = lane_examples.get(lane, [])
        if selected:
            return selected
        return [example for example in default_examples if example][:3]

    def _lane_hook_guidance(self, lane: str) -> str:
        guidance = {
            "ai_productivity": (
                "- Be contrarian and urgency-driven.\n"
                "- Use framing like: \"You're wasting time if...\", \"Stop doing X...\".\n"
                "- Call out common bad workflow assumptions."
            ),
            "storytelling": (
                "- Start with suspense and tension tied to the exact topic object/location.\n"
                "- Hook must include the concrete archetype anchor (note, hallway movement, lights out, confession, whispered name, or room silence).\n"
                "- Avoid reusable generic openers; first line should feel impossible to copy to another topic."
            ),
            "commentary": (
                "- Start with a hot take or bold opinion.\n"
                "- Sound like a creator with a strong stance, not neutral analysis.\n"
                "- Trigger disagreement/curiosity quickly."
            ),
            "motivation": (
                "- Be blunt, short, and punchy.\n"
                "- Call out excuses directly.\n"
                "- Sound like a tough coach, not a motivational poster."
            ),
        }
        return guidance.get(lane, guidance["ai_productivity"])

    def _lane_body_guidance(self, lane: str, topic_is_ai: bool) -> str:
        guidance = {
            "ai_productivity": (
                "- Keep body practical and contrarian.\n"
                "- Focus on wasted time, wrong habits, and specific behavior fixes.\n"
                "- Sound like a creator sharing real workflow wins, not a tutorial blog."
            ),
            "storytelling": (
                "- Write as a suspense/confession short from start to finish.\n"
                "- Escalate stakes every 1-2 lines and maintain tension.\n"
                "- Sentence 1 and sentence 2 must keep the same archetype and mention the topic anchor by sentence 2.\n"
                "- Avoid list format and avoid instructional/productivity language."
            ),
            "commentary": (
                "- Keep body opinionated and argumentative.\n"
                "- Use a clear stance, one supporting point, one sharp counterpoint.\n"
                "- Prioritize punch and perspective over neutral exposition."
            ),
            "motivation": (
                "- Keep body blunt, direct, and no-nonsense.\n"
                "- Use command-style lines and concrete behavior cues.\n"
                "- No soft motivational clichés."
            ),
        }
        lane_guidance = guidance.get(lane, guidance["ai_productivity"])
        if lane == "storytelling" and not topic_is_ai:
            lane_guidance += (
                "\n- DO NOT mention AI tools, productivity apps, workflow systems, Notion, ChatGPT, or automation."
            )
        return lane_guidance

    def _has_ai_productivity_contamination(self, text: str) -> bool:
        lowered = str(text or "").lower()
        if re.search(r"\bai\b", lowered):
            return True
        patterns = [
            "ai tool",
            "ai tools",
            "productivity",
            "workflow",
            "notion",
            "chatgpt",
            "automation",
        ]
        return any(pattern in lowered for pattern in patterns)

    def _topic_label(self, topic: dict) -> str:
        raw_title = str(topic.get("title", "") or "")
        base_title = re.sub(r"\s*\([^)]*\)\s*$", "", raw_title).strip().lower()
        base_title = re.sub(r"[^a-z0-9\s]+", " ", base_title)
        return re.sub(r"\s+", " ", base_title).strip()

    def _normalize_match_text(self, text: str) -> str:
        normalized = re.sub(r"[^a-z0-9\s]+", " ", str(text or "").lower())
        return re.sub(r"\s+", " ", normalized).strip()

    def _contains_topic_label_verbatim(self, text: str, topic_label: str) -> bool:
        if not topic_label or len(topic_label.split()) < 2:
            return False
        normalized_text = self._normalize_match_text(text)
        return f" {topic_label} " in f" {normalized_text} "

    def _has_robotic_storytelling_phrasing(self, text: str, topic_label: str) -> bool:
        lowered = str(text or "").lower()
        if self._contains_topic_label_verbatim(lowered, topic_label):
            return True

        pattern_hits = [
            "mystery confession story",
            "horror night story",
            "this story",
            "that story",
            "story got weird",
            "storytelling content",
        ]
        if any(pattern in lowered for pattern in pattern_hits):
            return True

        category_count = len(re.findall(r"\b(story|storytelling|mystery|confession|twist)\b", lowered))
        action_count = len(re.findall(r"\b(heard|saw|walked|opened|found|ran|looked|dug|followed|whispered)\b", lowered))
        return category_count >= 3 and action_count <= 1

    def _storytelling_topic_terms(self, topic: dict) -> set[str]:
        topic_text = " ".join(
            [
                str(topic.get("title", "") or ""),
                str(topic.get("summary", "") or ""),
            ]
        ).lower()
        tokens = set(re.findall(r"[a-z0-9]+", topic_text))
        terms: set[str] = set()
        scene_markers = {
            "hallway", "corridor", "door", "room", "stairs", "stairwell", "landing",
            "window", "midnight", "lights", "note", "paper", "envelope", "handwriting",
            "shadow", "footsteps", "doorframe", "mirror", "reflection", "exit", "sign",
            "direction", "wrong", "way", "nickname", "voice", "elevator", "floor",
            "apartment", "threshold", "outside", "whisper", "whispered", "name", "quiet",
            "silence", "moved", "left", "heard", "confession", "overheard", "blackout",
            "dark", "power",
        }
        for marker in scene_markers:
            if marker in tokens:
                terms.add(marker)

        if "whisper" in terms or "whispered" in terms:
            terms.add("whisper")
        if "door" in terms:
            terms.add("door")
        if "hallway" in terms:
            terms.add("hallway")
        if "midnight" in terms or "lights" in terms:
            terms.add("night")
        if self._phrase_match_count(topic_text, ["exit sign"]) > 0:
            terms.add("exit")
            terms.add("sign")
            terms.add("exit_sign")
        if self._phrase_match_count(topic_text, ["wrong way", "wrong direction"]) > 0:
            terms.add("wrong")
            terms.add("direction")
        if self._phrase_match_count(topic_text, ["old nickname", "my nickname"]) > 0:
            terms.add("nickname")
        if self._phrase_match_count(topic_text, ["mirror in the stairwell", "stairwell mirror"]) > 0:
            terms.add("mirror")
            terms.add("stairwell")
        if self._phrase_match_count(topic_text, ["outside my apartment", "apartment door"]) > 0:
            terms.add("apartment")
            terms.add("outside")
            terms.add("door")
        return terms

    def _phrase_match_count(self, text: str, phrases: list[str]) -> int:
        lowered = str(text or "").lower()
        hits = 0
        for phrase in phrases:
            target = str(phrase or "").strip().lower()
            if not target:
                continue
            if " " in target:
                pattern = r"(?<![a-z0-9])" + re.escape(target) + r"(?![a-z0-9])"
            else:
                pattern = r"\b" + re.escape(target) + r"\b"
            if re.search(pattern, lowered):
                hits += 1
        return hits

    def _storytelling_archetype_requirements(self) -> dict[str, dict]:
        return {
            "note_under_door": {
                "hook_terms": ["note", "paper", "envelope", "handwriting", "under my door", "under the door"],
                "anchor_terms": ["note", "paper", "envelope", "handwriting", "door", "under", "floor", "threshold"],
            },
            "hallway_movement": {
                "hook_terms": ["hallway", "shadow", "footsteps", "doorframe", "something moved", "moved"],
                "anchor_terms": ["hallway", "shadow", "footsteps", "doorframe", "moved", "movement", "outside my door"],
            },
            "room_went_quiet": {
                "hook_terms": ["room", "sentence", "silence", "everyone froze", "nobody moved", "went quiet"],
                "anchor_terms": ["room", "sentence", "silence", "quiet", "everyone", "nobody moved", "froze"],
            },
            "lights_went_out": {
                "hook_terms": ["lights", "blackout", "dark", "power", "went out", "went dark"],
                "anchor_terms": ["lights", "blackout", "dark", "power", "room went dark", "flashlight", "outage"],
            },
            "confession_overheard": {
                "hook_terms": [
                    "confession",
                    "overheard",
                    "what was said",
                    "one sentence",
                    "whispered confession",
                    "i heard him say",
                    "i heard her say",
                ],
                "anchor_terms": ["confession", "overheard", "said", "sentence", "whispered", "door", "hallway"],
            },
            "whispered_name": {
                "hook_terms": ["name", "whisper", "called my name", "heard my name"],
                "anchor_terms": ["name", "whisper", "called", "heard", "hallway", "door", "behind me"],
            },
            "generic_story": {
                "hook_terms": ["door", "hallway", "room", "midnight", "shadow", "lights", "note"],
                "anchor_terms": ["door", "hallway", "room", "shadow", "lights", "note", "footsteps"],
            },
        }

    def _storytelling_topic_mode(self, topic: dict) -> str:
        topic_text = " ".join(
            [
                str(topic.get("title", "") or ""),
                str(topic.get("summary", "") or ""),
            ]
        ).lower()
        normalized = re.sub(r"\s+", " ", topic_text).strip()
        tokens = set(re.findall(r"[a-z0-9]+", normalized))
        note_signal = (
            self._phrase_match_count(normalized, ["under my door", "under the door"]) > 0
            and self._phrase_match_count(normalized, ["note", "paper", "envelope", "handwriting"]) > 0
        ) or ("note" in tokens and "door" in tokens)

        room_terms = self._phrase_match_count(normalized, ["room"]) > 0
        quiet_terms = self._phrase_match_count(normalized, ["quiet", "silence", "silent", "froze", "nobody moved"]) > 0
        sentence_terms = self._phrase_match_count(normalized, ["one sentence", "sentence", "one line"]) > 0

        lights_terms = self._phrase_match_count(normalized, ["lights", "power", "blackout", "dark"]) > 0
        lights_out_terms = self._phrase_match_count(
            normalized,
            ["lights went out", "went dark", "power went out", "power cut", "blackout", "lights died", "outage"],
        ) > 0

        confession_terms = self._phrase_match_count(
            normalized,
            ["confession", "overheard", "whispered confession", "what was said", "confessed", "admitted", "nobody should have heard"],
        ) > 0

        whisper_terms = self._phrase_match_count(
            normalized,
            ["whisper", "whispered", "heard my name", "called my name", "behind the door"],
        ) > 0
        name_terms = self._phrase_match_count(normalized, ["heard my name", "called my name", "whispered my name"]) > 0

        hallway_terms = self._phrase_match_count(normalized, ["hallway", "doorframe", "corridor", "outside my door"]) > 0
        movement_terms = self._phrase_match_count(normalized, ["moved", "movement", "shadow", "footsteps", "shifted", "creaked"]) > 0
        should_leave_terms = self._phrase_match_count(
            normalized,
            ["should have left", "i should have left", "left right there", "backed away", "turn around", "walked out"],
        ) > 0

        if note_signal:
            return "note_under_door"

        # Keep room-quiet mapping strict so "should have left" or whisper topics never route here.
        if room_terms and quiet_terms and sentence_terms:
            return "room_went_quiet"

        # Only route to blackout when outage language is explicit.
        if lights_terms and lights_out_terms:
            return "lights_went_out"

        if confession_terms:
            return "confession_overheard"

        if name_terms:
            return "whispered_name"
        if whisper_terms and ("door" in tokens or hallway_terms or "midnight" in tokens or "name" in tokens):
            return "whispered_name"

        if hallway_terms and movement_terms:
            return "hallway_movement"

        if should_leave_terms:
            if hallway_terms or movement_terms or "door" in tokens:
                return "hallway_movement"
            if whisper_terms or "name" in tokens:
                return "whispered_name"
            return "generic_story"

        if hallway_terms:
            return "hallway_movement"
        if whisper_terms:
            return "whispered_name"

        return "generic_story"

    def _storytelling_hook_archetype_fit_count(self, hook_text: str) -> int:
        requirements = self._storytelling_archetype_requirements()
        lowered = str(hook_text or "").lower()
        fit_count = 0
        for archetype, spec in requirements.items():
            if archetype == "generic_story":
                continue
            if self._phrase_match_count(lowered, spec["hook_terms"]) > 0:
                fit_count += 1
        return fit_count

    def _storytelling_mismatched_opener_penalty(self, hook_text: str, archetype: str) -> int:
        lowered = str(hook_text or "").lower()
        penalty = 0
        if ("i heard my name" in lowered or "called my name" in lowered) and archetype != "whispered_name":
            penalty += 10
        if ("the room went quiet" in lowered or "went dead quiet" in lowered) and archetype != "room_went_quiet":
            penalty += 10
        if ("i should have left" in lowered) and archetype not in {"hallway_movement", "lights_went_out", "generic_story"}:
            penalty += 8
        return penalty

    def _storytelling_hook_mode_bonus(self, lowered_hook: str, topic: dict) -> int:
        archetype = self._storytelling_topic_mode(topic)
        requirements = self._storytelling_archetype_requirements()
        spec = requirements.get(archetype, requirements["generic_story"])

        hook_hits = self._phrase_match_count(lowered_hook, spec["hook_terms"])
        anchor_hits = self._phrase_match_count(lowered_hook, spec["anchor_terms"])
        fit_count = self._storytelling_hook_archetype_fit_count(lowered_hook)

        score = 0
        if hook_hits == 0:
            score -= 28
        else:
            score += min(18, hook_hits * 5)

        if anchor_hits == 0:
            score -= 14
        else:
            score += min(10, anchor_hits * 3)

        if fit_count > 1:
            score -= min(18, (fit_count - 1) * 7)

        score -= self._storytelling_mismatched_opener_penalty(hook_text=lowered_hook, archetype=archetype)
        return score

    def _is_unknown_suspense_mode(self, mode: str) -> bool:
        known_modes = {
            "note_under_door",
            "hallway_movement",
            "room_went_quiet",
            "lights_went_out",
            "confession_overheard",
            "whispered_name",
        }
        return str(mode or "").strip().lower() not in known_modes

    def _storytelling_topic_context(self, topic: dict) -> dict:
        terms = self._storytelling_topic_terms(topic)
        raw_title = str(topic.get("title", "") or "story").lower()
        archetype = self._storytelling_topic_mode(topic)
        resolved_mode = archetype
        requirements = self._storytelling_archetype_requirements()
        spec = requirements.get(archetype, requirements["generic_story"])
        hook_terms = list(spec["hook_terms"])
        anchor_terms = list(spec["anchor_terms"])

        location = "hallway"
        object_name = "door"
        hook_seed: list[str]
        opening_lines: list[str]
        sensory_bits: list[str]
        detail_bits: list[str]
        escape_bits: list[str]
        fallback_hook_options: dict[str, list[str]] | None = None
        fallback_openers: dict[str, str] | None = None

        if archetype == "note_under_door":
            location = "doorway"
            object_name = "note"
            hook_seed = [
                "A folded note slid under my door with my name on it.",
                "I found an envelope under my door in handwriting I recognized.",
            ]
            opening_lines = [
                "Paper scraped under the door and stopped at my shoe.",
                "I watched an envelope slide under the door and hit the floor.",
            ]
            sensory_bits = [
                "I heard three soft taps after the paper stopped moving.",
                "The hallway went silent the second I picked up the note.",
                "The paper felt damp, like it had just been held.",
            ]
            detail_bits = [
                "Inside, one sentence was written in my old handwriting.",
                "The note used a nickname only one person knew.",
                "When I looked up again, there were fresh wet footprints by the door.",
            ]
            escape_bits = [
                "I left the note on the counter and locked the bedroom door.",
                "I backed away from the doorway and called my brother immediately.",
                "I stepped into the hall, but no one was there.",
            ]
        elif archetype == "hallway_movement":
            location = "hallway"
            object_name = "doorframe"
            hook_seed = [
                "Something moved in the hallway outside my doorframe.",
                "I saw a shadow cross the hallway when no one was there.",
            ]
            opening_lines = [
                "I stayed by the doorframe and watched the hallway mirror.",
                "Footsteps stopped in the hallway right outside my room.",
            ]
            sensory_bits = [
                "I heard one slow step, then a scrape against the wall.",
                "The hallway light flickered and the shadow shifted the wrong way.",
                "I saw movement at the edge of the doorframe without a body.",
            ]
            detail_bits = [
                "Then the shadow moved back toward me against the light.",
                "Then the doorframe creaked like someone leaned on it.",
                "Then a second set of footsteps started, but there was still no one there.",
            ]
            escape_bits = [
                "I stepped back into my room and shut the door softly.",
                "I moved for the stairs and didn't look over my shoulder.",
                "I left the hallway and called out, but no one answered.",
            ]
        elif archetype == "room_went_quiet":
            location = "room"
            object_name = "table"
            hook_seed = [
                "The room went quiet after one sentence.",
                "After one sentence, nobody moved in the room.",
            ]
            opening_lines = [
                "I finished one sentence, and every face in the room froze before anyone breathed.",
                "Chairs stopped scraping across the room the second I spoke, and no one reached for the door.",
            ]
            sensory_bits = [
                "No one blinked, coughed, or reached for a phone.",
                "The vent hum got loud enough to drown my heartbeat.",
                "A spoon hit the tile and kept spinning while nobody moved.",
            ]
            detail_bits = [
                "Then the person across from me mouthed, \"don't turn around,\" without looking at me.",
                "Then every face turned to the same dark corner behind me.",
                "Then one glass cracked on the table, and no hand was near it.",
            ]
            escape_bits = [
                "I decided to leave my bag, walked out, and never came back for it.",
                "I backed toward the door, locked it from the hallway, and through the glass everyone was facing me.",
                "I ran outside, called security, and the footage showed my chair occupied after I had already left.",
            ]
        elif archetype == "lights_went_out":
            location = "room"
            object_name = "light switch"
            hook_seed = [
                "The lights went out, and the whole room dropped into black.",
                "Right after the power cut, I heard someone whisper behind me.",
            ]
            opening_lines = [
                "The room went dark so fast I could hear people breathing.",
                "When the lights died, I reached for the wall and missed.",
            ]
            sensory_bits = [
                "Something brushed my shoulder in the blackout.",
                "A phone flashlight flashed once, then snapped off again.",
                "I heard the breaker click, but the room stayed dark.",
            ]
            detail_bits = [
                "Then someone said my name from the side of the room where no one was standing.",
                "Then I saw a silhouette move between me and the dead exit sign.",
                "Then the power returned for one second, and the wrong person was in my spot.",
            ]
            escape_bits = [
                "I followed the wall to the door and got out before the blackout hit again.",
                "I ran for the hallway and the exit sign pointed the wrong way behind me.",
                "I got outside, and when the lights came back, someone else was standing where I had been.",
            ]
        elif archetype == "confession_overheard":
            location = "hallway"
            object_name = "door"
            hallway_confession_mode = (
                "hallway" in terms
                or "door" in terms
                or "footsteps" in terms
                or self._phrase_match_count(
                    raw_title,
                    ["hallway confession", "creepy hallway confession", "in the hallway", "outside the door"],
                ) > 0
            )
            hook_seed = [
                "I overheard a confession through the door, then heard my name outside.",
                "One whispered confession in the hallway ended with the latch moving.",
            ]
            if hallway_confession_mode:
                opening_lines = [
                    "I froze in the hallway with my shoulder to the wall while the confession kept coming.",
                    "The confession came through the door, and footsteps stopped on my side of it.",
                ]
                sensory_bits = [
                    "I heard the door latch tremble between each sentence.",
                    "The hallway light buzzed while a shoe scraped once beside me.",
                    "Their voice dropped when the floorboards creaked next to my shoes.",
                ]
                detail_bits = [
                    "Then one confession line gave my full name and tomorrow's date, and the latch clicked.",
                    "Then I heard my name from the hall side of the door, right behind me.",
                    "Then a shadow stopped under the door before anyone opened it.",
                ]
                escape_bits = [
                    "I ran down the hallway, left my phone recording by the door, and never got that file back.",
                    "I walked out to the street and texted the sentence to two people before the door opened.",
                    "I called the police from the bus stop and gave them the timestamp before dawn.",
                ]
            else:
                opening_lines = [
                    "I stopped outside the door when the confession started using details from my life.",
                    "I froze by the door because every sentence sounded rehearsed for me.",
                ]
                sensory_bits = [
                    "The voice inside was low, but every word landed clearly through the wall.",
                    "I heard one sentence repeated with the same pause before my name.",
                    "My hand was on the doorknob while they kept confessing inside.",
                ]
                detail_bits = [
                    "Then I heard, \"I did it,\" followed by my full name and tonight's date.",
                    "Then the confession described a scar on my hand no one in that room had seen.",
                    "Then the door latch clicked while someone whispered the same sentence from the hallway.",
                ]
                escape_bits = [
                    "I left, sent the sentence to two contacts, and deleted the recording from my phone.",
                    "I ran for the stairs and called the police before I reached the lobby.",
                    "I wrote every word in my notes and signed the timestamp before sunrise.",
                ]
        elif archetype == "whispered_name":
            location = "hallway"
            object_name = "door"
            hook_seed = [
                "I heard my name whispered from the hallway.",
                "Someone called my name from behind the door at midnight.",
            ]
            opening_lines = [
                "I turned toward the hallway because the whisper sounded close.",
                "The second time I heard my name, I stopped moving.",
            ]
            sensory_bits = [
                "I heard the same whisper from the other side of the door.",
                "Cold air hit my neck when my name was called again.",
                "The hallway floor creaked once, then nothing.",
            ]
            detail_bits = [
                "Then the door handle turned even though no one was there.",
                "Then the whisper used a nickname only my family knows.",
                "Then I saw a shadow pause at the edge of the hallway light.",
            ]
            escape_bits = [
                "I backed away from the hallway and locked the door.",
                "I moved to the stairs without answering the voice.",
                "I left that floor and didn't look back.",
            ]
        else:
            normalized_title = self._normalize_match_text(raw_title)
            title_tokens = set(re.findall(r"[a-z0-9]+", normalized_title))

            mirror_stairwell_mode = (
                "mirror" in title_tokens
                and ("stairwell" in title_tokens or "landing" in title_tokens or "stairs" in title_tokens)
            )
            exit_sign_wrong_mode = (
                ("exit sign" in normalized_title or ("exit" in title_tokens and "sign" in title_tokens))
                and (
                    self._phrase_match_count(
                        normalized_title,
                        ["wrong way", "wrong direction", "pointed the wrong way", "pointed wrong"],
                    ) > 0
                    or "wrong" in title_tokens
                    or "direction" in title_tokens
                )
            )
            nickname_midnight_mode = (
                ("nickname" in title_tokens or self._phrase_match_count(normalized_title, ["old nickname", "my old nickname"]) > 0)
                and ("midnight" in title_tokens or "called" in title_tokens or "voice" in title_tokens)
            )
            elevator_wrong_floor_mode = (
                "elevator" in title_tokens
                and ("floor" in title_tokens or "opened" in title_tokens or "wrong" in title_tokens)
            )
            footsteps_apartment_mode = (
                "footsteps" in title_tokens
                and (
                    "apartment" in title_tokens
                    or "outside" in title_tokens
                    or self._phrase_match_count(normalized_title, ["outside my apartment", "outside my door", "apartment door"]) > 0
                )
            )
            locked_door_voice_mode = (
                "door" in title_tokens
                and ("locked" in title_tokens or "lock" in title_tokens)
                and ("voice" in title_tokens or "behind" in title_tokens or "whisper" in title_tokens)
            )

            if mirror_stairwell_mode:
                resolved_mode = "mirror_stairwell"
                location = "stairwell landing"
                object_name = "mirror"
                hook_seed = [
                    "The hallway mirror in the stairwell showed someone standing on the landing behind me.",
                    "I checked the stairwell hallway mirror and my reflection didn't copy me.",
                ]
                opening_lines = [
                    "I watched the hallway mirror from the stairwell landing by the fire door.",
                    "The stairwell hallway was empty, but the mirror held a second figure.",
                ]
                sensory_bits = [
                    "I heard footsteps in the hallway below me while the reflection stayed still.",
                    "Cold air moved up the stairwell even though every door was shut.",
                    "The metal rail clicked once behind me, but the landing stayed empty.",
                ]
                detail_bits = [
                    "Then my reflection lifted its hand one second after mine.",
                    "Then the mirror showed someone step off the landing where no one stood.",
                    "Then my face in the hallway mirror turned before I did.",
                ]
                escape_bits = [
                    "I took the stairs down without looking back at the mirror.",
                    "I skipped the next landing and left through the side exit.",
                    "I got outside and only then checked the hallway mirror in my phone.",
                ]
                fallback_hook_options = {
                    "whispered_moment": [
                        "The hallway mirror in the stairwell showed someone on the landing behind me.",
                        "I looked into the stairwell mirror and saw movement that wasn't mine.",
                        "The mirror in the stairwell copied everything except one move.",
                    ],
                    "wrong_detail": [
                        "My reflection in the stairwell hallway mirror moved a second late.",
                        "The mirror looked normal until the landing behind me filled with a second figure.",
                        "I turned in the stairwell, but only the mirror version kept moving.",
                    ],
                    "consequence_escape": [
                        "When the stairwell mirror changed, I ran for the exit stairs.",
                        "I left the landing the moment my reflection moved wrong.",
                        "After what I saw in that stairwell mirror, I never took those stairs alone again.",
                    ],
                }
                fallback_openers = {
                    "whispered_moment": "I stopped on the stairwell landing and checked the mirror.",
                    "wrong_detail": "The stairwell mirror looked normal until my reflection blinked late.",
                    "consequence_escape": "When the reflection broke sync, I took the stairs two at a time.",
                }
                profile_terms = ["mirror", "stairwell", "landing", "reflection", "stairs", "hallway"]
            elif exit_sign_wrong_mode:
                resolved_mode = "exit_sign_wrong"
                location = "hallway"
                object_name = "exit sign"
                hook_seed = [
                    "The exit sign at the end of the hallway pointed the wrong way.",
                    "I followed the hallway exit sign until the arrow turned me deeper inside.",
                ]
                opening_lines = [
                    "I stepped into the hallway and checked the exit sign first.",
                    "The arrow above the exit flickered and pointed away from the stairs.",
                ]
                sensory_bits = [
                    "I heard doors closing behind me while the hallway stayed empty and one shadow moved back toward me.",
                    "The fluorescent buzz got louder each time I passed the same sign.",
                    "My footsteps echoed forward, but the exit stayed the same distance away.",
                ]
                detail_bits = [
                    "Then the exit arrow rotated and pointed back toward my apartment.",
                    "Then I found the same fire extinguisher twice on different walls.",
                    "Then every exit sign said EXIT, but each arrow pointed a different direction.",
                ]
                escape_bits = [
                    "I ignored the arrows, hugged the wall, and found the real stairwell.",
                    "I took the emergency stairs and left through the loading door.",
                    "I kept one hand on the wall until I reached the street.",
                ]
                fallback_hook_options = {
                    "whispered_moment": [
                        "The hallway exit sign pointed me the wrong way.",
                        "I followed the exit sign once, and it led me deeper into the building.",
                        "The exit arrow changed direction while I was looking at it.",
                    ],
                    "wrong_detail": [
                        "Every exit sign in the hallway said EXIT, but none pointed out.",
                        "At first the exit arrow looked normal, then it flipped when I started moving.",
                        "The sign pointed one way, but the hallway looped me back to the same door.",
                    ],
                    "consequence_escape": [
                        "When the exit sign pointed wrong, I abandoned the hallway route and took the stairs.",
                        "I stopped trusting the arrows and followed the wall to get out.",
                        "I reached the street only after ignoring every exit sign on that floor.",
                    ],
                }
                fallback_openers = {
                    "whispered_moment": "I stepped into the hallway and checked the exit sign first.",
                    "wrong_detail": "The exit sign looked normal until the arrow turned.",
                    "consequence_escape": "After the sign pointed wrong again, I moved for the stairwell.",
                }
                profile_terms = ["exit sign", "exit", "sign", "wrong way", "direction", "hallway"]
            elif nickname_midnight_mode:
                resolved_mode = "nickname_midnight"
                location = "hallway outside my apartment"
                object_name = "door"
                hook_seed = [
                    "At midnight, someone used my old nickname from the hallway.",
                    "I heard my old nickname outside my door right after midnight.",
                ]
                opening_lines = [
                    "In the dead silent hallway, my phone clock flipped to 12:00 when the voice said my old nickname.",
                    "I stood in the dark hallway and heard that nickname again.",
                ]
                sensory_bits = [
                    "The voice sounded close enough to be outside the door.",
                    "The hallway stayed silent outside the door after the second call.",
                    "I heard one soft knock outside the door, then breathing on the other side.",
                ]
                detail_bits = [
                    "Then the voice repeated a nickname no one has used in years.",
                    "Then my phone lit up with no call while the voice kept talking.",
                    "Then the same nickname came from both the hallway and my speaker at once.",
                ]
                escape_bits = [
                    "I backed away, recorded the hallway, and left my apartment.",
                    "I took the stairs to the lobby without answering.",
                    "I waited outside until sunrise and never opened that door.",
                ]
                fallback_hook_options = {
                    "whispered_moment": [
                        "Someone used my old nickname outside my door at midnight.",
                        "I heard my old nickname from the hallway when the clock hit 12:00.",
                        "That midnight voice knew the nickname I buried years ago.",
                    ],
                    "wrong_detail": [
                        "The voice used my old nickname, and my phone showed no incoming call.",
                        "I heard my nickname in the hallway, then through my speaker at the same time.",
                        "No one was outside, but that old nickname came through the door again.",
                    ],
                    "consequence_escape": [
                        "After the third time it used my old nickname, I left the floor.",
                        "I stopped listening, took the stairs, and got out of the building.",
                        "I never opened that door after hearing my old nickname at midnight.",
                    ],
                }
                fallback_openers = {
                    "whispered_moment": "My phone clock turned to midnight when the voice started.",
                    "wrong_detail": "The nickname came from the hallway first, then from inside my apartment.",
                    "consequence_escape": "When the voice used that nickname again, I left immediately.",
                }
                profile_terms = ["nickname", "old nickname", "midnight", "voice", "hallway", "door"]
            elif elevator_wrong_floor_mode:
                resolved_mode = "elevator_wrong_floor"
                location = "elevator"
                object_name = "elevator doors"
                hook_seed = [
                    "The elevator opened on the wrong floor, and the lobby number didn't match the panel.",
                    "I pressed my floor, but the elevator doors opened somewhere I didn't recognize.",
                ]
                opening_lines = [
                    "The elevator dinged once and opened to a floor that wasn't mine.",
                    "The panel showed my floor, but the elevator kept opening on a different number.",
                ]
                sensory_bits = [
                    "The corridor air felt colder than the elevator cab.",
                    "No apartment sounds reached the elevator lobby, only the motor hum.",
                    "The floor indicator blinked my number while the wall sign showed another.",
                ]
                detail_bits = [
                    "Then the doors opened again to the same empty floor under a different number.",
                    "Then I saw my own doormat in front of the wrong unit.",
                    "Then the call button outside lit up before anyone stepped out.",
                ]
                escape_bits = [
                    "I stayed inside the elevator and rode straight to the lobby.",
                    "I rode down to the lobby and left through the main entrance.",
                    "I got outside from the lobby and waited before going back in.",
                ]
                fallback_hook_options = {
                    "whispered_moment": [
                        "The elevator opened on the wrong floor even though I pressed mine.",
                        "I stepped out of the elevator into a floor that shouldn't exist in my building.",
                        "The elevator doors opened, but the floor number didn't match the panel.",
                    ],
                    "wrong_detail": [
                        "The elevator kept opening on the wrong floor while showing my number.",
                        "I saw my own doormat on a floor I have never lived on.",
                        "The doors closed and reopened to the same empty hall with a different sign.",
                    ],
                    "consequence_escape": [
                        "When the elevator opened on the wrong floor again, I stayed inside and rode to the lobby.",
                        "After the second wrong stop, I stayed in the elevator and rode down to the lobby.",
                        "I left the building from the lobby after the elevator sent me to the wrong floor twice.",
                    ],
                }
                fallback_openers = {
                    "whispered_moment": "The elevator dinged and opened to the wrong floor.",
                    "wrong_detail": "I pressed my floor again, but the same wrong floor came back.",
                    "consequence_escape": "After the second wrong stop, I stayed inside and rode down to the lobby.",
                }
                profile_terms = ["elevator", "wrong floor", "floor", "doors", "lobby", "call button"]
            elif footsteps_apartment_mode:
                resolved_mode = "footsteps_apartment"
                location = "apartment doorway"
                object_name = "apartment door"
                hook_seed = [
                    "Footsteps stopped outside my apartment door, then the latch twitched once.",
                    "I heard footsteps in the hall, then my apartment door handle moved.",
                ]
                opening_lines = [
                    "The footsteps reached my apartment door and stopped at the threshold.",
                    "I stood behind the door and counted the silence after the last step.",
                ]
                sensory_bits = [
                    "The hallway light buzzed while no shadow passed under the door.",
                    "I heard breathing once, then the floorboards went silent and stayed still.",
                    "My peephole showed an empty hall, but the floor kept creaking.",
                ]
                detail_bits = [
                    "Then one slow step moved away without a second footfall.",
                    "Then my doorknob turned a fraction and stopped.",
                    "Then the footsteps restarted from the far end of the hall at the same pace.",
                ]
                escape_bits = [
                    "I locked the deadbolt, texted my neighbor, and stayed away from the door.",
                    "I left through the fire stairs and circled back from outside.",
                    "I waited for sunrise before opening the door.",
                ]
                fallback_hook_options = {
                    "whispered_moment": [
                        "Footsteps stopped outside my apartment door, and the latch moved once.",
                        "I heard someone walk to my apartment and stop at the threshold, then the handle twitched.",
                        "The footsteps ended outside my door, then the latch clicked with no knock.",
                    ],
                    "wrong_detail": [
                        "The peephole showed an empty hall while footsteps stayed outside my apartment.",
                        "The doorknob moved after the footsteps stopped.",
                        "I heard the same pacing restart from the far end of the hall without passing my door.",
                    ],
                    "consequence_escape": [
                        "After the handle moved, I left through the fire stairs.",
                        "I stayed off that doorway until morning and never opened the door.",
                        "I texted my neighbor, locked everything, and waited outside.",
                    ],
                }
                fallback_openers = {
                    "whispered_moment": "The footsteps stopped outside the door at my apartment threshold and did not continue.",
                    "wrong_detail": "I checked the peephole and saw an empty hallway where the footsteps had stopped.",
                    "consequence_escape": "When the handle shifted, I moved for the fire stairs.",
                }
                profile_terms = ["footsteps", "apartment", "door", "threshold", "hallway", "outside", "latch", "handle"]
            elif locked_door_voice_mode:
                resolved_mode = "locked_door_voice"
                location = "locked doorway"
                object_name = "latch"
                hook_seed = [
                    "I heard a voice behind the locked door, and the latch moved once.",
                    "The locked door stayed shut, then the latch clicked from the other side.",
                ]
                opening_lines = [
                    "I stood outside the locked door and listened to the voice behind it.",
                    "The voice stayed low behind the locked door while the latch trembled.",
                ]
                sensory_bits = [
                    "I heard one whisper outside the door, then the latch clicked.",
                    "The hallway stayed still while the door handle twitched once.",
                    "I stayed outside the door and heard breathing behind the lock.",
                ]
                detail_bits = [
                    "Then the voice used my name and the latch moved again.",
                    "Then the handle turned halfway and stopped.",
                    "Then the same sentence came from behind the door and right beside me.",
                ]
                escape_bits = [
                    "I backed away from the locked door and took the stairs.",
                    "I left the hallway before the latch moved a third time.",
                    "I got outside and called for help from the street.",
                ]
                fallback_hook_options = {
                    "whispered_moment": [
                        "I heard a voice behind the locked door, and the latch moved once.",
                        "The locked door stayed shut, then the latch clicked by itself.",
                        "A voice came through the locked door and the latch moved.",
                    ],
                    "wrong_detail": [
                        "The voice stayed behind the locked door, but the latch moved on my side.",
                        "I heard the same line behind the locked door while the handle turned halfway.",
                        "The door stayed locked, and the latch clicked in answer to the voice.",
                    ],
                    "consequence_escape": [
                        "When the latch moved again, I left the hallway and took the stairs.",
                        "I backed away from the locked door and got out before the handle turned again.",
                        "I left as soon as the voice and latch synced the second time.",
                    ],
                }
                fallback_openers = {
                    "whispered_moment": "I stopped outside the locked door when the voice started.",
                    "wrong_detail": "The locked door stayed shut while the latch moved.",
                    "consequence_escape": "After the second latch click, I went for the stairs.",
                }
                profile_terms = ["door", "locked", "voice", "latch", "handle", "hallway"]
            else:
                if "room" in terms:
                    location = "room"
                elif "elevator" in terms:
                    location = "elevator"
                elif "stairwell" in terms or "stairs" in terms:
                    location = "stairwell"
                elif "hallway" in terms or "corridor" in terms:
                    location = "hallway"
                elif "door" in terms:
                    location = "doorway"
                elif "apartment" in terms:
                    location = "apartment doorway"

                object_name = "door"
                if "mirror" in terms:
                    object_name = "mirror"
                elif "exit_sign" in terms or ("exit" in terms and "sign" in terms):
                    object_name = "exit sign"
                elif "elevator" in terms:
                    object_name = "elevator doors"
                elif "window" in terms:
                    object_name = "window"
                elif "note" in terms:
                    object_name = "note"
                elif "lights" in terms:
                    object_name = "light switch"

                should_leave_mode = (
                    "left" in terms
                    or "walked" in terms
                    or self._phrase_match_count(raw_title, ["should have left", "left right there", "walked out"]) > 0
                )
                if should_leave_mode:
                    hook_seed = [
                        f"I should have left right there by the {location}.",
                        f"I stayed one second too long near the {object_name}.",
                    ]
                    opening_lines = [
                        f"I stopped in the {location} and heard movement near the {object_name}.",
                        "I took one step back and listened for it again.",
                    ]
                    sensory_bits = [
                        "I heard one slow step behind me, then silence.",
                        f"The {location} stayed empty, but the air shifted beside me.",
                        "A soft click came from the dark side of the hall.",
                    ]
                    detail_bits = [
                        f"Then the {object_name} moved without anyone touching it.",
                        "Then the same sound repeated from the wrong direction.",
                        "Then I realized I was hearing it from behind me too.",
                    ]
                    escape_bits = [
                        f"I backed away from the {location} and took the nearest exit.",
                        "I moved for the stairs before the sound came again.",
                        "I got outside first and checked everything from the street.",
                    ]
                else:
                    hook_seed = [
                        f"I noticed one wrong detail in the {location} and stopped.",
                        f"I heard a sound near the {object_name} that didn't fit.",
                    ]
                    opening_lines = [
                        f"I stood in the {location} and listened for a second pass.",
                        f"I watched the {object_name} and waited for it to happen again.",
                    ]
                    sensory_bits = [
                        "I heard one step, then silence long enough to count to five.",
                        f"The {location} stayed empty, but the air shifted beside me.",
                        "A soft click came from the dark side of the hall.",
                    ]
                    detail_bits = [
                        f"Then the {object_name} moved without anyone touching it.",
                        "Then the sound repeated from the wrong direction.",
                        "Then I realized I was hearing it from behind me too.",
                    ]
                    escape_bits = [
                        f"I backed away from the {location} and took the nearest exit.",
                        "I moved for the stairs before the next sound hit.",
                        "I got outside first and checked everything from the street.",
                    ]

                fallback_hook_options = {
                    "whispered_moment": [
                        hook_seed[0],
                        f"I heard something from the {location} and stopped cold.",
                        f"I caught one sound near the {object_name} that shouldn't be there.",
                    ],
                    "wrong_detail": [
                        f"The {location} looked normal until the {object_name} changed.",
                        f"I watched the {object_name}, and one detail shifted in the wrong direction.",
                        f"One second later, the {object_name} moved again with no one there.",
                    ],
                    "consequence_escape": [
                        f"When the {object_name} changed again, I moved for the exit.",
                        f"I left the {location} before the sound repeated a third time.",
                        "I stopped waiting and got out immediately.",
                    ],
                }
                fallback_openers = {
                    "whispered_moment": opening_lines[0],
                    "wrong_detail": opening_lines[1],
                    "consequence_escape": f"I stepped back from the {location} and moved for the exit.",
                }
                profile_terms = [tok for tok in title_tokens if len(tok) > 3][:6]

            hook_terms = list(dict.fromkeys(hook_terms + profile_terms))
            anchor_terms = list(dict.fromkeys(anchor_terms + profile_terms))

        return {
            "terms": terms,
            "location": location,
            "object_name": object_name,
            "hook_seed": hook_seed,
            "opening_lines": opening_lines,
            "sensory_bits": sensory_bits,
            "detail_bits": detail_bits,
            "escape_bits": escape_bits,
            "topic_key": raw_title,
            "mode": resolved_mode,
            "archetype": archetype,
            "hook_terms": hook_terms,
            "anchor_terms": anchor_terms,
            "fallback_hook_options": fallback_hook_options,
            "fallback_openers": fallback_openers,
        }

    def _has_story_sensory_detail(self, text: str) -> bool:
        lowered = str(text or "").lower()
        return bool(
            re.search(
                r"\b(heard|saw|felt|whisper|whispered|breathing|footsteps|handle|door|doorframe|hallway|room|cold|shadow|lights?|dark|blackout|name|silence|silent|clicked|creaked|flash)\b",
                lowered,
            )
        )

    def _story_line_similarity(self, left: str, right: str) -> float:
        return self._jaccard_similarity(
            self._tokenize_for_scoring(left),
            self._tokenize_for_scoring(right),
        )

    def _shared_word_prefix_count(self, left: str, right: str, limit: int = 7) -> int:
        left_words = re.findall(r"[a-z0-9']+", str(left or "").lower())[:limit]
        right_words = re.findall(r"[a-z0-9']+", str(right or "").lower())[:limit]
        shared = 0
        for lw, rw in zip(left_words, right_words):
            if lw != rw:
                break
            shared += 1
        return shared

    def _story_line_has_reveal(self, line: str, archetype: str) -> bool:
        lowered = str(line or "").lower()
        base_markers = [
            "moved the wrong way",
            "no one was there",
            "nobody was there",
            "didn't match",
            "did not match",
            "wrong floor",
            "floor indicator",
            "call button",
            "looked back",
            "clicked",
            "turned",
            "cracked",
            "stopped at my door",
            "under my door",
            "handwriting",
            "wet footprints",
            "silhouette",
            "shape moved",
            "confession",
            "overheard",
            "called my name",
            "heard my name",
            "nickname",
            "blackout",
            "went dark",
            "power returned",
            "for one second",
        ]
        archetype_markers = {
            "note_under_door": ["note", "envelope", "handwriting", "wet footprints", "under my door", "sentence on the note"],
            "hallway_movement": ["hallway", "shadow", "footsteps", "doorframe", "reflection", "mirror", "stopped at my door"],
            "room_went_quiet": ["room", "silent", "nobody moved", "everyone froze", "faces turned", "glass cracked"],
            "lights_went_out": ["lights", "blackout", "dark", "silhouette", "power returned", "flash"],
            "confession_overheard": ["confession", "overheard", "what was said", "didn't match", "my name", "door latch"],
            "whispered_name": ["my name", "nickname", "handle turned", "shadow", "no one there", "wrong place"],
            "generic_story": ["wrong floor", "call button", "exit sign", "stairwell", "apartment", "footsteps"],
        }
        markers = base_markers + archetype_markers.get(archetype, [])
        return any(marker in lowered for marker in markers)

    def _story_line_has_consequence(self, line: str) -> bool:
        lowered = str(line or "").lower()
        consequence_markers = [
            "i left",
            "i ran",
            "i backed away",
            "i walked out",
            "i moved for the exit",
            "i locked",
            "i called",
            "i wrote",
            "i decided",
            "i never came back",
            "i shut the door",
            "i got out",
            "i followed the wall",
            "i turned around",
        ]
        return any(marker in lowered for marker in consequence_markers)

    def _story_line_has_final_image(self, line: str) -> bool:
        lowered = str(line or "").lower()
        image_markers = [
            "wet footprints",
            "empty hallway",
            "door latch",
            "handle turning",
            "shadow at the doorframe",
            "everyone was facing me",
            "glass cracked",
            "exit sign",
            "silhouette",
            "phone light",
            "flashlight",
            "no one there",
            "door moved",
            "note on the floor",
        ]
        return any(marker in lowered for marker in image_markers)

    def _story_line_is_dead_end(self, line: str, archetype: str) -> bool:
        lowered = str(line or "").lower().strip()
        if not lowered:
            return True
        dead_patterns = [
            r"^then i looked at the door\.?$",
            r"^then i looked at the note\.?$",
            r"^then i looked at it\.?$",
            r"^then i looked back\.?$",
            r"^it got weird fast\.?$",
            r"^everything changed\.?$",
            r"^something felt wrong\.?$",
            r"^then the hallway went quiet enough to hear my pulse\.?$",
        ]
        if any(re.search(pattern, lowered) for pattern in dead_patterns):
            if self._story_line_has_reveal(lowered, archetype) or self._story_line_has_consequence(lowered) or self._story_line_has_final_image(lowered):
                return False
            return True
        return False

    def _vague_storytelling_hits(self, text: str) -> int:
        lowered = str(text or "").lower()
        sentences = self._sentence_list(lowered)
        if not sentences:
            return 0
        vague_patterns = [
            "something was wrong",
            "it got weird",
            "i knew it was bad",
            "one detail felt wrong",
        ]
        hits = 0
        for idx, sentence in enumerate(sentences):
            if not any(pattern in sentence for pattern in vague_patterns):
                continue
            next_sentence = sentences[idx + 1] if idx + 1 < len(sentences) else ""
            if not (self._has_story_sensory_detail(sentence) or self._has_story_sensory_detail(next_sentence)):
                hits += 1
        return hits

    def _storytelling_progression_hits(self, text: str) -> int:
        lowered = str(text or "").lower()
        transition_hits = len(re.findall(r"\b(then|next|after|when|before|suddenly)\b", lowered))
        action_hits = len(
            re.findall(
                r"\b(heard|saw|turned|opened|stopped|moved|froze|ran|left|backed away|walked out)\b",
                lowered,
            )
        )
        return min(5, transition_hits + min(3, action_hits // 2))

    def _storytelling_anchor_report(self, hook: str, narration: str, topic: dict) -> dict:
        context = self._storytelling_topic_context(topic)
        hook_text = str(hook or "").strip()
        narration_text = str(narration or "").strip()
        archetype = context.get("archetype", "generic_story")

        # Story progression checks must evaluate the body after the hook prefix.
        body_text = self._strip_hook_prefix_from_narration(narration=narration_text, hook=hook_text)
        body_sentences = self._sentence_list(body_text)
        if not body_sentences:
            body_sentences = self._sentence_list(narration_text)

        sentence_1 = body_sentences[0] if len(body_sentences) >= 1 else ""
        sentence_2 = body_sentences[1] if len(body_sentences) >= 2 else ""
        sentence_3 = body_sentences[2] if len(body_sentences) >= 3 else ""
        sentence_4 = body_sentences[3] if len(body_sentences) >= 4 else ""

        hook_source = hook_text or sentence_1
        hook_lower = hook_source.lower()
        first_two = f"{sentence_1} {sentence_2}".strip().lower()

        hook_hits = self._phrase_match_count(hook_lower, context.get("hook_terms", []))
        hook_anchor_hits = self._phrase_match_count(hook_lower, context.get("anchor_terms", []))
        first_two_anchor_hits = self._phrase_match_count(first_two, context.get("anchor_terms", []))
        anchor_by_sentence2 = (hook_anchor_hits + first_two_anchor_hits) > 0
        hook_fit_count = self._storytelling_hook_archetype_fit_count(hook_lower)

        filler_phrases = [
            "it got weird fast",
            "something felt wrong",
            "everything changed",
            "i froze",
        ]
        filler_penalty = 0
        first_two_sentences = [sentence_1.lower(), sentence_2.lower()]
        for idx, sentence in enumerate(first_two_sentences):
            if not sentence:
                continue
            if not any(phrase in sentence for phrase in filler_phrases):
                continue
            next_sentence = first_two_sentences[idx + 1] if idx + 1 < len(first_two_sentences) else ""
            if not (self._has_story_sensory_detail(sentence) or self._has_story_sensory_detail(next_sentence)):
                filler_penalty += 8

        repeat_ignore_tokens = {
            "room",
            "door",
            "hallway",
            "confession",
            "lights",
            "light",
            "name",
            "note",
            "whisper",
            "whispered",
            "shadow",
            "quiet",
            "sentence",
            "midnight",
        }
        repeat_ignore_tokens.update(self._storytelling_topic_terms(topic))
        for phrase in context.get("anchor_terms", []) + context.get("hook_terms", []):
            repeat_ignore_tokens.update(
                tok for tok in re.findall(r"[a-z0-9']+", str(phrase or "").lower()) if len(tok) > 2
            )

        def filtered_tokens(text: str) -> set[str]:
            tokens = self._tokenize_for_scoring(text)
            return {tok for tok in tokens if tok not in repeat_ignore_tokens}

        def meaningful_prefix_count(left: str, right: str, limit: int = 8) -> int:
            left_words = [
                tok
                for tok in re.findall(r"[a-z0-9']+", str(left or "").lower())
                if len(tok) > 2 and tok not in repeat_ignore_tokens
            ][:limit]
            right_words = [
                tok
                for tok in re.findall(r"[a-z0-9']+", str(right or "").lower())
                if len(tok) > 2 and tok not in repeat_ignore_tokens
            ][:limit]
            shared = 0
            for lw, rw in zip(left_words, right_words):
                if lw != rw:
                    break
                shared += 1
            return shared

        s1_tokens = filtered_tokens(sentence_1)
        s2_tokens = filtered_tokens(sentence_2)
        s3_tokens = filtered_tokens(sentence_3)
        hook_tokens = filtered_tokens(hook_source)

        sim_s2_s1 = self._jaccard_similarity(s2_tokens, s1_tokens)
        sim_s2_hook = self._jaccard_similarity(s2_tokens, hook_tokens)
        sim_s3_s2 = self._jaccard_similarity(s3_tokens, s2_tokens)
        shared_prefix_s2_s1 = meaningful_prefix_count(sentence_2, sentence_1)
        shared_prefix_s2_hook = meaningful_prefix_count(sentence_2, hook_source)
        shared_prefix_s3_s2 = meaningful_prefix_count(sentence_3, sentence_2)

        repeat_s2_hook = (
            (sim_s2_s1 >= 0.70 and len(s2_tokens) >= 2 and len(s1_tokens) >= 2)
            or (sim_s2_hook >= 0.74 and len(s2_tokens) >= 2 and len(hook_tokens) >= 2)
            or shared_prefix_s2_s1 >= 4
            or shared_prefix_s2_hook >= 4
        )
        repeat_s3_s2 = (
            (sim_s3_s2 >= 0.74 and len(s3_tokens) >= 2 and len(s2_tokens) >= 2)
            or shared_prefix_s3_s2 >= 4
        )

        s2_has_sensory = self._has_story_sensory_detail(sentence_2)
        s3_has_reveal = self._story_line_has_reveal(sentence_3, archetype=archetype)
        s4_has_landing = (
            self._story_line_has_consequence(sentence_4)
            or self._story_line_has_reveal(sentence_4, archetype=archetype)
            or self._story_line_has_final_image(sentence_4)
        )
        s4_dead_end = self._story_line_is_dead_end(sentence_4, archetype=archetype)

        progression_penalty = 0
        progression_bonus = 0
        if repeat_s2_hook:
            progression_penalty += 14
        if repeat_s3_s2:
            progression_penalty += 8
        if not s2_has_sensory:
            progression_penalty += 8
        else:
            progression_bonus += 5
        if not s3_has_reveal:
            progression_penalty += 10
        else:
            progression_bonus += 6
        if s4_dead_end or not s4_has_landing:
            progression_penalty += 14
        else:
            progression_bonus += 8
        progression_bonus = min(18, progression_bonus)
        progression_penalty = min(40, progression_penalty)

        mismatched_opener_penalty = self._storytelling_mismatched_opener_penalty(hook_text=hook_lower, archetype=archetype)
        return {
            "archetype": archetype,
            "sentence_1": sentence_1,
            "sentence_2": sentence_2,
            "sentence_3": sentence_3,
            "sentence_4": sentence_4,
            "hook_hits": hook_hits,
            "hook_anchor_hits": hook_anchor_hits,
            "first_two_anchor_hits": first_two_anchor_hits,
            "anchor_by_sentence2": anchor_by_sentence2,
            "hook_fit_count": hook_fit_count,
            "filler_penalty": min(16, filler_penalty),
            "mismatched_opener_penalty": mismatched_opener_penalty,
            "repeat_s2_hook": repeat_s2_hook,
            "repeat_s3_s2": repeat_s3_s2,
            "shared_prefix_s2_s1": shared_prefix_s2_s1,
            "s2_has_sensory": s2_has_sensory,
            "s3_has_reveal": s3_has_reveal,
            "s4_has_landing": s4_has_landing,
            "s4_dead_end": s4_dead_end,
            "progression_bonus": progression_bonus,
            "progression_penalty": progression_penalty,
        }

    def _storytelling_anchor_penalty(self, hook: str, narration: str, topic: dict) -> float:
        report = self._storytelling_anchor_report(hook=hook, narration=narration, topic=topic)
        penalty = 0.0
        if report["hook_hits"] == 0 and report["first_two_anchor_hits"] == 0:
            penalty += 16.0
        elif report["hook_hits"] == 0:
            penalty += 7.0
        elif report["first_two_anchor_hits"] == 0:
            penalty += 6.0
        if not report["anchor_by_sentence2"]:
            penalty += 12.0
        if report["hook_fit_count"] > 1:
            penalty += min(10.0, (report["hook_fit_count"] - 1) * 4.0)
        if report["repeat_s2_hook"]:
            penalty += 10.0
        if report["repeat_s3_s2"]:
            penalty += 7.0
        if not report["s2_has_sensory"]:
            penalty += 6.0
        if not report["s3_has_reveal"]:
            penalty += 8.0
        if report["s4_dead_end"]:
            penalty += 14.0
        elif not report["s4_has_landing"]:
            penalty += 10.0
        penalty += min(6.0, float(report["filler_penalty"]) * 0.5)
        penalty += min(8.0, float(report["mismatched_opener_penalty"]))
        penalty += min(8.0, float(report["progression_penalty"]) * 0.2)
        penalty -= min(6.0, float(report["progression_bonus"]) * 0.4)

        structurally_valid = (
            report["anchor_by_sentence2"]
            and report["s2_has_sensory"]
            and (report["s3_has_reveal"] or report["s4_has_landing"])
            and not report["s4_dead_end"]
        )
        if structurally_valid:
            penalty = min(penalty, 12.0)
        elif report["anchor_by_sentence2"] and (report["s3_has_reveal"] or report["s4_has_landing"]):
            penalty = min(penalty, 20.0)
        return min(42.0, max(0.0, penalty))

    def _storytelling_dimension_scores(self, hook: str, narration: str, topic: dict) -> dict:
        report = self._storytelling_anchor_report(hook=hook, narration=narration, topic=topic)
        archetype = report["archetype"]
        sentence_4 = report["sentence_4"]

        archetype_fit = 34.0
        if report["hook_hits"] == 0:
            archetype_fit -= 24.0
        else:
            archetype_fit += min(36.0, report["hook_hits"] * 12.0)
        archetype_fit += min(16.0, report["hook_anchor_hits"] * 4.0)
        if report["hook_fit_count"] > 1:
            archetype_fit -= min(22.0, (report["hook_fit_count"] - 1) * 8.0)
        archetype_fit -= float(report["mismatched_opener_penalty"])
        archetype_fit = max(0.0, min(100.0, archetype_fit))

        anchor_presence = 24.0
        anchor_presence += min(30.0, report["hook_anchor_hits"] * 10.0)
        anchor_presence += min(30.0, report["first_two_anchor_hits"] * 10.0)
        if not report["anchor_by_sentence2"]:
            anchor_presence -= 28.0
        anchor_presence = max(0.0, min(100.0, anchor_presence))

        progression = 22.0
        progression += 16.0 if report["s2_has_sensory"] else -14.0
        progression += 24.0 if report["s3_has_reveal"] else -18.0
        progression += 24.0 if report["s4_has_landing"] and not report["s4_dead_end"] else -22.0
        if report["repeat_s2_hook"]:
            progression -= 18.0
        if report["repeat_s3_s2"]:
            progression -= 12.0
        progression -= min(20.0, float(report["progression_penalty"]) * 0.35)
        progression += min(12.0, float(report["progression_bonus"]) * 0.5)
        progression = max(0.0, min(100.0, progression))

        reveal_strength = 26.0 + (38.0 if report["s3_has_reveal"] else -16.0)
        if self._story_line_has_reveal(sentence_4, archetype=archetype):
            reveal_strength += 12.0
        if report["repeat_s3_s2"]:
            reveal_strength -= 10.0
        reveal_strength = max(0.0, min(100.0, reveal_strength))

        final_landing = 22.0 + (42.0 if report["s4_has_landing"] else -22.0)
        if self._story_line_has_consequence(sentence_4):
            final_landing += 12.0
        if self._story_line_has_final_image(sentence_4):
            final_landing += 10.0
        if report["s4_dead_end"]:
            final_landing -= 36.0
        final_landing = max(0.0, min(100.0, final_landing))

        originality = 78.0
        if report["hook_fit_count"] > 1:
            originality -= min(24.0, (report["hook_fit_count"] - 1) * 10.0)
        if report["repeat_s2_hook"]:
            originality -= 20.0
        if report["repeat_s3_s2"]:
            originality -= 12.0
        originality -= min(12.0, float(report["filler_penalty"]))
        originality = max(0.0, min(100.0, originality))

        composite = (
            (archetype_fit * 0.21)
            + (anchor_presence * 0.20)
            + (progression * 0.24)
            + (reveal_strength * 0.14)
            + (final_landing * 0.16)
            + (originality * 0.05)
        )
        return {
            "archetype": archetype,
            "archetype_fit": round(archetype_fit, 1),
            "anchor_presence": round(anchor_presence, 1),
            "progression": round(progression, 1),
            "reveal_strength": round(reveal_strength, 1),
            "final_landing": round(final_landing, 1),
            "originality": round(originality, 1),
            "composite": round(max(0.0, min(100.0, composite)), 1),
            "report": report,
        }

    def _story_scene_openers(self, topic: dict) -> list[str]:
        context = self._storytelling_topic_context(topic)
        topic_key = context["topic_key"]
        archetype = context.get("archetype", "generic_story")
        extras = [
            f"I should have left when the {context['location']} went still.",
            f"I heard something shift near the {context['object_name']}.",
            f"I backed away from the {context['location']} and listened.",
            f"I stopped at the {context['location']} when I heard it again.",
        ]
        if archetype == "note_under_door":
            extras = [
                "A note slid under my door with my name on it.",
                "I found an envelope under the door in shaky handwriting.",
                "Paper scraped under the door right after midnight.",
                "The note under my door used a name from my past.",
            ]
        elif archetype == "hallway_movement":
            extras = [
                "Something moved in the hallway outside my doorframe.",
                "I saw a shadow cross the hallway with no one there.",
                "Footsteps stopped in the hallway right by my door.",
                "The hallway mirror caught movement that wasn't mine.",
            ]
        elif archetype == "room_went_quiet":
            extras = [
                "The room went quiet after one sentence, and nobody reached for the door.",
                "After one sentence, every face in the room shifted behind me.",
                "I spoke once, and everyone in the room froze while a glass cracked.",
                "One sentence killed the room, and through the door everyone was facing me.",
            ]
        elif archetype == "lights_went_out":
            extras = [
                "The lights went out, and the room dropped into black.",
                "Right after the blackout, someone whispered my name from the wrong side of the room.",
                "The power cut, and I couldn't see my own hands.",
                "When the lights died, the exit sign stayed dark and something moved in front of it.",
            ]
        elif archetype == "confession_overheard":
            extras = [
                "I overheard a confession through the door, then heard my name outside it.",
                "One whispered confession in the hallway ended with the latch moving.",
                "I heard exactly what was said, and it included tomorrow's date.",
                "The confession started with one sentence, then footsteps stopped on my side of the door.",
            ]
        elif archetype == "whispered_name":
            extras = [
                "I heard my name whispered from the hallway.",
                "Someone called my name from behind the door.",
                "The whisper used my name, not just my voice.",
                "I heard my name twice before I turned around.",
            ]
        elif archetype == "generic_story":
            extras = []
            generic_hook_options = context.get("fallback_hook_options")
            if isinstance(generic_hook_options, dict):
                hook_angles = (
                    ["whispered_moment", "wrong_detail"]
                    if self._is_unknown_suspense_mode(context.get("mode", archetype))
                    else ["whispered_moment", "wrong_detail", "consequence_escape"]
                )
                for angle in hook_angles:
                    hooks = generic_hook_options.get(angle, [])
                    if isinstance(hooks, list):
                        extras.extend(hooks[:2])
            extras.extend(context.get("opening_lines", [])[:2])
            extras = [line for line in extras if str(line or "").strip()]
            if not extras:
                extras = [
                    f"I heard something shift near the {context['object_name']}.",
                    f"I stopped in the {context['location']} when the sound came again.",
                ]
        else:
            extras = []
            generic_hook_options = context.get("fallback_hook_options")
            if isinstance(generic_hook_options, dict):
                hook_angles = (
                    ["whispered_moment", "wrong_detail"]
                    if self._is_unknown_suspense_mode(context.get("mode", archetype))
                    else ["whispered_moment", "wrong_detail", "consequence_escape"]
                )
                for angle in hook_angles:
                    hooks = generic_hook_options.get(angle, [])
                    if isinstance(hooks, list):
                        extras.extend(hooks[:2])
            extras.extend(context.get("opening_lines", [])[:2])
            extras = [line for line in extras if str(line or "").strip()]
            if not extras:
                extras = [
                    f"I heard something shift near the {context['object_name']}.",
                    f"I stopped in the {context['location']} when the sound came again.",
                ]
        all_options = context["hook_seed"] + extras
        start = self._stable_index([topic_key, "story-openers"], len(all_options))
        rotated = all_options[start:] + all_options[:start]
        return rotated[:4]

    def _rewrite_storytelling_hook_humanized(self, topic: dict) -> str:
        openers = self._story_scene_openers(topic)
        raw_title = str(topic.get("title", "") or "story")
        index = self._stable_index([raw_title.lower(), "story-hook-human"], len(openers))
        return openers[index]

    def _has_weak_ai_productivity_phrasing(self, text: str) -> bool:
        lowered = str(text or "").lower()
        patterns = [
            "boost productivity",
            "boost your productivity",
            "take your workflow to the next level",
            "take your productivity to the next level",
            "take it to the next level",
            "next level",
            "supercharge your workflow",
            "powerful tools",
            "work smarter",
            "game-changer",
            "unlock your productivity",
            "unlock your potential",
            "productivity skyrocket",
            "save you time instantly",
            "for what really matters",
            "ai can handle it",
            "future of work",
        ]
        return any(pattern in lowered for pattern in patterns)

    def _has_concrete_ai_use_case(self, text: str) -> bool:
        lowered = str(text or "").lower()
        use_case_terms = {
            "email", "emails", "inbox", "calendar", "meeting", "meetings", "lecture", "lectures",
            "notes", "checklist", "spreadsheet", "report", "docs", "documents",
            "slides", "messages", "follow-up", "follow up", "reply", "replies", "transcript",
            "summary", "summaries", "writing", "draft", "editing", "edit", "admin",
        }
        if any(term in lowered for term in use_case_terms):
            return True

        action_hits = re.findall(
            r"\b(automate|summarize|organize|sort|draft|reply|schedule|extract|prioritize|tag|clean|edit|rewrite|convert)\b",
            lowered,
        )
        target_hits = re.findall(
            r"\b(email|emails|inbox|meeting|note|notes|lecture|calendar|spreadsheet|report|doc|docs|slides?|writing|draft|admin|checklist)\b",
            lowered,
        )
        return bool(action_hits and target_hits)

    def _rewrite_ai_productivity_hook(self, topic: dict) -> str:
        raw_title = str(topic.get("title", "") or "your workflow")
        hook_templates = [
            "You're wasting time on one task you can automate today.",
            "Stop burning 30 minutes on work AI can handle.",
            "Fix this one workflow mistake and reclaim your day.",
        ]
        index = self._stable_index([raw_title.lower(), "ai-hook-human"], len(hook_templates))
        return hook_templates[index]

    def _rewrite_ai_productivity_narration(self, topic: dict, hook: str) -> str:
        opening = hook.strip() or "You're wasting time on one task you can automate today."
        raw_title = str(topic.get("title", "") or "workflow")
        templates = [
            [
                opening,
                "Start with email.",
                "Ask AI to summarize unread threads into five bullets.",
                "Then draft replies for the top three messages.",
                "You review fast, tweak tone, and send.",
                "One repetitive admin step is gone.",
            ],
            [
                opening,
                "Use it after class or meetings.",
                "Paste lecture notes and get a clean summary.",
                "Then ask for a short checklist of next actions.",
                "Finally, run one pass to clean up your writing.",
                "You get clarity in minutes, not an hour.",
            ],
            [
                opening,
                "Take your messy task dump before you log off.",
                "Have AI sort it into a priority checklist.",
                "Pick one annoying admin step and automate it.",
                "Set tomorrow's first action before closing your laptop.",
                "You start the day with direction, not chaos.",
            ],
        ]
        index = self._stable_index([raw_title.lower(), "ai-body-human"], len(templates))
        return " ".join(line.strip() for line in templates[index] if line.strip())

    def _ensure_narration_starts_with_hook(self, script: dict) -> None:
        hook = str(script.get("hook", "") or "").strip()
        narration = str(script.get("narration", "") or "").strip()
        if not hook or not narration:
            return

        normalized_hook = self._normalize_match_text(hook)
        normalized_narration = self._normalize_match_text(narration)
        if normalized_narration.startswith(normalized_hook):
            return
        script["narration"] = f"{hook} {narration}".strip()

    def _sanitize_story_fallback_line(self, line: str, context: dict | None = None) -> str:
        text = re.sub(r"\s+", " ", str(line or "")).strip(" ,;")
        if not text:
            return ""

        words = re.findall(r"[A-Za-z0-9']+", text)
        if not words:
            return ""

        if len(words) >= 2 and words[-2].lower() == "than" and words[-1].lower() == "the":
            object_name = ""
            if isinstance(context, dict):
                object_name = str(context.get("object_name", "") or "").strip()
            object_words = re.findall(r"[A-Za-z0-9']+", object_name)
            if object_words:
                words.extend(object_words[:2])
            else:
                words = words[:-2]

        bad_tail = {
            "and",
            "or",
            "to",
            "with",
            "without",
            "in",
            "on",
            "at",
            "for",
            "from",
            "before",
            "after",
            "than",
            "as",
            "the",
            "a",
            "an",
        }
        while len(words) > 4 and words[-1].lower() in bad_tail:
            words.pop()

        if not words:
            return ""
        cleaned = " ".join(words)
        if cleaned and cleaned[-1] not in ".!?":
            cleaned = f"{cleaned}."
        return cleaned

    def _rewrite_storytelling_narration(self, topic: dict, hook: str) -> str:
        raw_title = str(topic.get("title", "") or "story").strip()
        context = self._storytelling_topic_context(topic)
        openers = self._story_scene_openers(topic)
        opener_index = self._stable_index([raw_title.lower(), "story-opener"], len(openers))
        scene_opening = openers[opener_index]
        topic_label = self._topic_label(topic)

        opening = hook.strip() or scene_opening
        if self._contains_topic_label_verbatim(opening, topic_label):
            opening = scene_opening

        sensory_line = context["sensory_bits"][
            self._stable_index([context["topic_key"], "story-sensory"], len(context["sensory_bits"]))
        ]
        reveal_line = context["detail_bits"][
            self._stable_index([context["topic_key"], "story-detail"], len(context["detail_bits"]))
        ]
        payoff_line = context["escape_bits"][
            self._stable_index([context["topic_key"], "story-escape"], len(context["escape_bits"]))
        ]
        opening_line = context["opening_lines"][
            self._stable_index([context["topic_key"], "story-opening-line"], len(context["opening_lines"]))
        ]

        anchor_probe = f"{opening} {sensory_line} {reveal_line}".lower()
        if self._phrase_match_count(anchor_probe, context.get("anchor_terms", [])) == 0:
            opening_line = f"In the {context['location']}, I kept staring at the {context['object_name']}."
            sensory_line = context["sensory_bits"][0]
            reveal_line = context["detail_bits"][0]

        if self._story_line_similarity(opening_line, opening) >= 0.58:
            alt_opening = context["opening_lines"][
                (self._stable_index([context["topic_key"], "story-opening-line-alt"], len(context["opening_lines"])) + 1)
                % len(context["opening_lines"])
            ]
            if self._story_line_similarity(alt_opening, opening) < 0.58:
                opening_line = alt_opening
        if (
            self._story_line_similarity(sensory_line, opening) >= 0.58
            or self._story_line_similarity(sensory_line, opening_line) >= 0.58
        ):
            alt_sensory = context["sensory_bits"][
                (self._stable_index([context["topic_key"], "story-sensory-alt"], len(context["sensory_bits"])) + 1)
                % len(context["sensory_bits"])
            ]
            if (
                self._story_line_similarity(alt_sensory, opening) < 0.58
                and self._story_line_similarity(alt_sensory, opening_line) < 0.58
            ):
                sensory_line = alt_sensory
        if self._story_line_similarity(reveal_line, sensory_line) >= 0.60:
            reveal_line = context["detail_bits"][(self._stable_index([context["topic_key"], "story-detail-alt"], len(context["detail_bits"])) + 1) % len(context["detail_bits"])]
        if self._story_line_is_dead_end(payoff_line, context.get("archetype", "generic_story")):
            payoff_line = context["escape_bits"][0]

        # Extend the rewrite to ~35-45s of narration. The similarity-checked
        # sensory_line/reveal_line/payoff_line stay the first beat of each
        # bucket, so body sentences 1-4 remain [opening_line, sensory, reveal,
        # payoff] - the roles _is_weak_storytelling_body inspects - and the
        # humanization pass sees an unchanged structure.
        sensory_seq = self._distinct_story_bits([sensory_line, *context["sensory_bits"]], 0, 3)
        reveal_seq = self._distinct_story_bits([reveal_line, *context["detail_bits"]], 0, 3)
        payoff_seq = self._distinct_story_bits([payoff_line, *context["escape_bits"]], 0, 3)
        body_lines = [opening] + self._compose_extended_story_body(
            opening_line, sensory_seq, reveal_seq, payoff_seq
        )
        lines = [line.strip() for line in body_lines if line and line.strip()]
        if len(lines) < 5:
            lines = [
                opening.strip(),
                opening_line.strip(),
                sensory_line.strip(),
                reveal_line.strip(),
                payoff_line.strip(),
            ]
        cleaned_lines = [self._sanitize_story_fallback_line(line, context) for line in lines if line and line.strip()]
        return " ".join(line.strip() for line in cleaned_lines if line.strip())

    def _apply_hook_quality_pass(self, script: dict, topic: dict, lane: str, topic_is_ai: bool) -> dict:
        hook = str(script.get("hook", "") or "").strip()
        score = self._hook_quality_score(hook=hook, lane=lane, topic=topic)
        threshold = self._hook_quality_threshold(lane)

        if score >= threshold:
            return script

        rewritten = self._rewrite_hook_for_lane(topic=topic, lane=lane, topic_is_ai=topic_is_ai)
        if not rewritten:
            return script

        script["hook"] = rewritten
        self._ensure_narration_starts_with_hook(script)
        logger.info(
            f"Hook judge rewrite applied ({lane} lane, score={score} < {threshold})."
        )
        return script

    def _hook_quality_threshold(self, lane: str) -> int:
        thresholds = {
            "ai_productivity": 78,
            "storytelling": 76,
            "commentary": 74,
            "motivation": 74,
        }
        return thresholds.get(lane, 74)

    def _hook_quality_score(self, hook: str, lane: str, topic: dict) -> int:
        text = str(hook or "").strip()
        lowered = text.lower()
        if not text:
            return 0

        score = 100

        hard_reject_patterns = [
            r"^\s*new to\b",
            r"\bstart with this one rule\b",
            r"^\s*here are\b",
            r"^\s*this tool can\b",
            r"^\s*imagine\b",
            r"^\s*if you want to\b",
        ]
        if any(re.search(pattern, lowered) for pattern in hard_reject_patterns):
            score -= 70

        generic_patterns = [
            r"\b(productivity|workflow|tools?|guide|tips?|beginner)\b",
            r"\b(next level|powerful)\b",
            r"\bthis video\b",
        ]
        generic_hits = sum(1 for pattern in generic_patterns if re.search(pattern, lowered))
        if generic_hits >= 2:
            score -= 20

        word_count = len(re.findall(r"[a-z0-9']+", lowered))
        if word_count < 5:
            score -= 20
        if word_count > 16:
            score -= 10

        tension_markers = [
            "stop",
            "wasting",
            "wrong",
            "quiet",
            "whispered",
            "hallway",
            "door",
            "nobody",
            "excuses",
            "discipline",
            "hot take",
            "unpopular",
        ]
        if not any(marker in lowered for marker in tension_markers):
            score -= 15

        if lane == "ai_productivity":
            pain_markers = [
                "by hand",
                "manual",
                "wasting time",
                "wrong task",
                "slow",
                "repetitive",
                "emails",
                "email",
                "notes",
                "checklist",
                "admin",
            ]
            if not any(marker in lowered for marker in pain_markers):
                score -= 20
        elif lane == "storytelling":
            scene_markers = [
                "someone",
                "room",
                "hallway",
                "door",
                "heard",
                "saw",
                "whispered",
                "quiet",
                "name",
                "midnight",
                "note",
                "lights",
                "steps",
            ]
            if not any(marker in lowered for marker in scene_markers):
                score -= 25
            if self._vague_storytelling_hits(text) > 0:
                score -= 18
            category_hits = len(re.findall(r"\b(story|mystery|confession|plot twist|twist)\b", lowered))
            if category_hits >= 2:
                score -= 12
            topic_terms = self._storytelling_topic_terms(topic)
            alignment_hits = sum(1 for term in topic_terms if term in lowered)
            if topic_terms and alignment_hits == 0:
                score -= 10
            elif topic_terms:
                score += min(10, alignment_hits * 3)
            score += self._storytelling_hook_mode_bonus(lowered_hook=lowered, topic=topic)

            story_context = self._storytelling_topic_context(topic)
            unknown_suspense_mode = self._is_unknown_suspense_mode(story_context.get("mode", ""))
            if unknown_suspense_mode:
                decision_like_markers = [
                    "stayed inside the elevator",
                    "rode straight to the lobby",
                    "rode down to the lobby",
                    "ran for the stairs",
                    "moved for the stairs",
                    "left the building",
                    "i left",
                    "i ran",
                    "i backed away",
                    "i walked out",
                ]
                is_decision_like = self._story_line_has_consequence(text) or any(
                    marker in lowered for marker in decision_like_markers
                )
                if is_decision_like:
                    score -= 34
                    if re.match(r"^\s*(after|when)\b", lowered):
                        score -= 8

                anomaly_markers = [
                    "wrong floor",
                    "didn't match",
                    "did not match",
                    "before anyone",
                    "call button",
                    "opened again",
                    "same empty",
                    "same floor",
                    "pointed the wrong way",
                    "arrow flipped",
                    "latch clicked",
                    "no one stepped out",
                    "no one was there",
                ]
                anomaly_hits = sum(1 for marker in anomaly_markers if marker in lowered)
                score += min(20, anomaly_hits * 5)
        elif lane == "commentary":
            stance_markers = [
                "hot take",
                "unpopular",
                "nobody says",
                "wrong",
                "truth",
                "lie",
            ]
            if not any(marker in lowered for marker in stance_markers):
                score -= 20
        elif lane == "motivation":
            blunt_markers = [
                "stop",
                "excuses",
                "discipline",
                "no one",
                "nobody",
                "you're",
            ]
            if not any(marker in lowered for marker in blunt_markers):
                score -= 20

        topic_label = self._topic_label(topic)
        if topic_label and self._contains_topic_label_verbatim(lowered, topic_label):
            score -= 12

        return max(0, min(100, score))

    def _rewrite_hook_for_lane(self, topic: dict, lane: str, topic_is_ai: bool) -> str:
        raw_title = str(topic.get("title", "") or "topic").strip().lower()

        templates = {
            "ai_productivity": [
                "Stop writing these emails by hand.",
                "You're wasting time cleaning notes like this.",
                "Most people use AI for the wrong task first.",
                "You're burning time on one admin step every day.",
            ],
            "storytelling": [
                "Someone whispered my name from the hallway.",
                "I knew something was wrong the second I opened the door.",
                "The room went quiet after one sentence.",
                "One detail felt wrong, and I should have left.",
            ],
            "commentary": [
                "Hot take: most people are completely wrong about this.",
                "Nobody says this part out loud, so I will.",
                "Unpopular opinion: this advice sounds smart but fails in real life.",
            ],
            "motivation": [
                "Stop waiting. Your excuses are the real problem.",
                "You don't need motivation today. You need discipline.",
                "No one is coming to save your routine.",
            ],
        }

        if lane == "storytelling" and topic_is_ai:
            lane = "ai_productivity"
        if lane == "storytelling":
            return self._rewrite_storytelling_hook_humanized(topic)

        lane_templates = templates.get(lane, templates["ai_productivity"])
        idx = self._stable_index([raw_title, lane, "hook-judge"], len(lane_templates))
        return lane_templates[idx]

    def _is_local_anthropic_target(self) -> bool:
        if not self.anthropic_base_url:
            return False
        parsed = urlparse(self.anthropic_base_url)
        host = (parsed.hostname or "").strip().lower()
        return host in {"127.0.0.1", "localhost", "0.0.0.0"}

    def _local_model_available(self, model_name: str) -> bool:
        if not self._is_local_anthropic_target():
            return True

        base_url = self.anthropic_base_url.rstrip("/")
        if not base_url:
            return False

        discovered_models: list[str] = []
        endpoints = [f"{base_url}/api/tags", f"{base_url}/v1/models"]
        for url in endpoints:
            try:
                with urllib.request.urlopen(url, timeout=1.5) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="ignore"))
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
                continue
            except Exception:
                continue

            if url.endswith("/api/tags"):
                models = payload.get("models", []) if isinstance(payload, dict) else []
                for item in models:
                    if isinstance(item, dict):
                        value = str(item.get("name", "")).strip().lower()
                    else:
                        value = str(item).strip().lower()
                    if value:
                        discovered_models.append(value)
            elif url.endswith("/v1/models"):
                models = payload.get("data", []) if isinstance(payload, dict) else []
                for item in models:
                    if isinstance(item, dict):
                        value = str(item.get("id", "")).strip().lower()
                    else:
                        value = str(item).strip().lower()
                    if value:
                        discovered_models.append(value)

        if not discovered_models:
            return False

        target = (model_name or "").strip().lower()
        target_base = target.split(":", 1)[0]
        for available in discovered_models:
            available_base = available.split(":", 1)[0]
            if available == target or available_base == target_base:
                return True
        return False

    def generate_metadata(self, script: dict, topic: dict, affiliate_links: dict) -> dict:
        """
        Generates complete YouTube metadata:
        - Title (SEO optimized)
        - Description (with affiliate links)
        - Tags (30 tags for maximum discovery)
        - Category
        """
        # Build description with affiliate links
        description_parts = [
            script.get("description_snippet", ""),
            "",  # blank line
        ]

        if affiliate_links:
            description_parts.append("🔗 Tools I recommend:")
            for name, data in list(affiliate_links.items())[:3]:
                description_parts.append(f"► {name.title()}: {data['link']}")
            description_parts.append("")

        description_parts.extend([
            "─────────────────────────",
            "🔔 FOLLOW for daily AI tips & strategies",
            "📌 Save this for later!",
            "",
            f"#Shorts #{self.config.get('NICHE', 'ai').replace('_', '')} #AI #Viral",
        ])

        # Generate 30 tags using Claude
        tags = self._generate_seo_tags(script, topic)

        return {
            "title": script.get("title", topic["title"])[:100],
            "description": "\n".join(description_parts),
            "tags": tags,
            "categoryId": self._get_category_id(),
            "privacyStatus": "public",
            "short_caption": f"{script.get('hook', '')} {script.get('cta', '').replace('Follow', 'Follow @mychannel')}",
            "playlist_id": None,  # Add to playlist if set up
        }

    def _generate_seo_tags(self, script: dict, topic: dict) -> list:
        """Generates 30 SEO-optimized tags using Claude."""
        try:
            message = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": f"""Generate exactly 30 YouTube tags for this video.
Video title: {script.get('title', '')}
Topic: {topic.get('title', '')}
Niche: {self.config.get('NICHE', 'ai_tools')}

Return ONLY a JSON array of 30 strings. Mix of:
- Exact keyword tags (2-3 words)
- Broad niche tags  
- Trending hashtag-style tags
- Long-tail keywords

Example: ["ai tools 2025", "best ai apps", "artificial intelligence", "AI tutorial", ...]"""
                }],
            )

            raw = message.content[0].text.strip()
            raw = re.sub(r"```json\s*|```\s*", "", raw)
            tags = json.loads(raw)
            if isinstance(tags, list):
                return tags[:30]
        except Exception:
            pass

        # Fallback tags
        return script.get("tags", []) + [
            "shorts", "viral", "ai", "2025", "tutorial", "tips",
            "howto", "trending", "new", "technology"
        ]

    def _get_niche_context(self) -> str:
        contexts = {
            "ai_tools": "AI tools, productivity software, automation, ChatGPT, Claude, Midjourney",
            "finance": "personal finance, investing, passive income, side hustles, wealth building",
            "motivation": "self-improvement, productivity, success mindset, discipline, habits",
        }
        return contexts.get(self.config.get("NICHE", "ai_tools"), "")

    def _get_category_id(self) -> str:
        categories = {
            "ai_tools": "28",    # Science & Technology
            "finance": "25",      # News & Politics
            "motivation": "22",   # People & Blogs
        }
        return categories.get(self.config.get("NICHE", "ai_tools"), "22")

    def _extract_angle_from_title(self, title: str) -> tuple[str | None, str]:
        match = re.search(r"\(([^()]+)\)\s*$", title or "")
        if not match:
            return None, (title or "").strip()
        angle = match.group(1).strip().lower()
        base_title = re.sub(r"\s*\([^()]+\)\s*$", "", title).strip()
        return angle, (base_title or (title or "").strip())

    def _stable_index(self, key_parts: list[str], modulo: int) -> int:
        if modulo <= 0:
            return 0
        joined = "|".join(key_parts)
        total = sum((idx + 1) * ord(ch) for idx, ch in enumerate(joined))
        return total % modulo

    def _get_topic_group(self, clean_title: str) -> str:
        lowered = (clean_title or "").lower()
        if any(term in lowered for term in ["study hack", "study hacks", "exam prep", "exam"]):
            return "study_hacks"
        if any(term in lowered for term in ["student productivity", "student focus", "student"]):
            return "student_productivity"
        if any(term in lowered for term in ["ai tools", "ai tool", "chatgpt", "claude", "automation"]):
            return "ai_tools"
        return "ai_tools"

    def _get_fallback_script(
        self,
        topic: dict,
        lane: str | None = None,
        topic_is_ai: bool | None = None,
        log_warning: bool = True,
    ) -> dict:
        """Emergency fallback script if AI generation fails."""
        raw_title = (topic.get("title", "Amazing AI Content") or "Amazing AI Content").strip()
        if log_warning:
            logger.warning(f"Fallback script used for topic: {raw_title}")

        resolved_lane = lane or self._detect_content_lane(topic)
        resolved_topic_is_ai = self._topic_mentions_ai(topic) if topic_is_ai is None else topic_is_ai
        fallback_candidates = self._build_fallback_candidates(
            topic=topic,
            lane=resolved_lane,
            topic_is_ai=resolved_topic_is_ai,
        )
        selected = self._select_best_candidate(
            candidates=fallback_candidates,
            topic=topic,
            lane=resolved_lane,
            topic_is_ai=resolved_topic_is_ai,
        )
        return selected

    def _build_fallback_candidates(self, topic: dict, lane: str, topic_is_ai: bool) -> list[dict]:
        raw_title = (topic.get("title", "Amazing AI Content") or "Amazing AI Content").strip()
        _, clean_title = self._extract_angle_from_title(raw_title)
        lane_key = "ai_productivity" if lane == "storytelling" and topic_is_ai else lane
        if lane_key == "storytelling":
            lane_variants = self._storytelling_fallback_variants(topic)
        else:
            lane_variants = self._fallback_lane_variants(lane_key)

        scene_map = {
            "ai_productivity": ("Person handling inbox and notes on laptop", "email notes ai workflow"),
            "storytelling": ("Dim hallway with tense reaction and quick exit", "dark hallway suspense"),
            "commentary": ("Creator speaking to camera with bold subtitle overlays", "talking head opinion"),
            "motivation": ("Creator walking with direct-to-camera energy", "motivation direct camera"),
        }
        scene_description, scene_query = scene_map.get(lane_key, scene_map["ai_productivity"])
        story_context = self._storytelling_topic_context(topic) if lane_key == "storytelling" else None
        if story_context:
            location = str(story_context.get("location", "interior")).strip()
            object_name = str(story_context.get("object_name", "detail")).strip()
            terms = [str(term).strip() for term in list(story_context.get("terms", [])) if str(term).strip()]
            anchor_terms = terms[:3] if terms else []
            scene_description = f"Suspense scene in {location} centered on {object_name}"
            if anchor_terms:
                scene_query = " ".join(anchor_terms[:4])
            else:
                scene_query = f"{location} {object_name} suspense".strip()

        cta_map = {
            "ai_productivity": "Save this and follow for practical AI workflows.",
            "storytelling": "Follow for more true-feeling late-night stories.",
            "commentary": "Follow for more unfiltered takes.",
            "motivation": "Follow for more no-excuses momentum.",
        }
        cta = cta_map.get(lane_key, cta_map["ai_productivity"])

        candidates: list[dict] = []
        for variant in lane_variants[:3]:
            lines = [variant["hook"]] + variant.get("lines", [])
            if story_context:
                lines = [self._sanitize_story_fallback_line(line, story_context) for line in lines]
            narration = " ".join(line.strip() for line in lines if line and line.strip())
            narration_words = len(narration.split())
            script = {
                "title": raw_title[:100],
                "hook": lines[0] if lines else variant["hook"],
                "on_screen_hook": variant.get("on_screen_hook", variant["hook"][:36].upper()),
                "thumbnail_text": variant.get("thumbnail_text", "WATCH THIS"),
                "narration": narration,
                "scenes": [
                    {
                        "timestamp_start": 0,
                        "timestamp_end": 59,
                        "description": scene_description,
                        "stock_query": scene_query,
                    }
                ],
                "estimated_duration": self._estimate_narration_seconds(narration_words),
                "words": narration_words,
                "visual_queries": [clean_title, lane_key.replace("_", " "), variant.get("angle", "shorts")],
                "tags": ["shorts", "viral", "ai", "2025"],
                "description_snippet": variant.get("caption", f"{clean_title} short with strong hook and clear payoff."),
                "cta": cta,
            }
            script = self._apply_quality_guardrails(script=script, topic=topic)
            # Guardrails may rewrite narration; recompute length fields from the
            # final text so estimated_duration and words are never stale.
            final_words = len(str(script.get("narration", "") or "").split())
            script["words"] = final_words
            script["estimated_duration"] = self._estimate_narration_seconds(final_words)
            script.update({
                "topic": topic,
                "generated_at": datetime.now().isoformat(),
                "is_fallback": True,
            })
            candidates.append(script)
        return candidates

    # Observed TTS pace from real bridge renders: ~175 wpm => ~2.9 words/sec.
    _WORDS_PER_SECOND = 2.9

    @classmethod
    def _estimate_narration_seconds(cls, word_count: int) -> int:
        """Estimate spoken narration length in whole seconds from a word count."""
        if word_count <= 0:
            return 0
        return max(1, round(word_count / cls._WORDS_PER_SECOND))

    @staticmethod
    def _distinct_story_bits(bits: list[str], base_index: int, count: int = 3) -> list[str]:
        """Pick up to `count` distinct beats from `bits`, walking forward from
        `base_index`. Deterministic, and safe when `bits` is short or empty."""
        picked: list[str] = []
        total = len(bits)
        if total == 0:
            return picked
        step = 0
        while len(picked) < min(count, total) and step < total:
            candidate = bits[(base_index + step) % total]
            if candidate and candidate not in picked:
                picked.append(candidate)
            step += 1
        return picked

    @staticmethod
    def _compose_extended_story_body(
        open_line: str,
        sensory: list[str],
        detail: list[str],
        escape: list[str],
    ) -> list[str]:
        """Build a fallback story body long enough for a 35-45s Short.

        Layout: open_line, then up to three [sensory, detail, escape] cycles.
        Sentences 1-4 of the body are always [open, sensory[0], detail[0],
        escape[0]] - the exact roles the storytelling anchor guardrail inspects
        (see _storytelling_anchor_report) - so adding length never changes
        guardrail behavior. The extra beats land in sentences 5+."""
        body: list[str] = [open_line]
        cycles = min(3, max(len(sensory), len(detail), len(escape), 0))
        for cycle in range(cycles):
            for bucket in (sensory, detail, escape):
                if cycle < len(bucket):
                    body.append(bucket[cycle])
        return body

    def _storytelling_fallback_variants(self, topic: dict) -> list[dict]:
        context = self._storytelling_topic_context(topic)
        topic_key = context["topic_key"]
        location = context["location"]
        object_name = context["object_name"]
        terms = context["terms"]
        mode = context.get("mode", "generic_story")
        unknown_suspense_mode = self._is_unknown_suspense_mode(mode)
        if mode == "note_under_door":
            hook_options = {
                "whispered_moment": [
                    "I heard paper scrape under my door and saw a note with my name.",
                    "An envelope slid under my door in handwriting I recognized.",
                    "A folded note appeared under my door after midnight.",
                ],
                "wrong_detail": [
                    "The note under my door used my old handwriting.",
                    "I picked up the paper under my door and one sentence made me stop breathing.",
                    "The envelope under my door was sealed with a mark I knew.",
                ],
                "consequence_escape": [
                    "I read the note under my door and backed away from the hallway.",
                    "After that note slid under my door, I locked every room.",
                    "I should have ignored the envelope under my door, but I opened it.",
                ],
            }
        elif mode == "hallway_movement":
            hook_options = {
                "whispered_moment": [
                    "Something moved in the hallway outside my doorframe.",
                    "I heard footsteps in the hallway, but no one was there.",
                    "A shadow crossed the hallway when the lights were steady.",
                ],
                "wrong_detail": [
                    "The hallway looked empty until the doorframe shifted.",
                    "I saw the hallway mirror catch movement that wasn't mine.",
                    "One shadow in the hallway moved against the light.",
                ],
                "consequence_escape": [
                    "I should have left when the hallway shadow stopped at my door.",
                    "When something moved in the hallway, I went for the stairs.",
                    "I backed away from the hallway and shut the door quietly.",
                ],
            }
        elif mode == "room_went_quiet":
            hook_options = {
                "whispered_moment": [
                    "The room went quiet after one sentence, and nobody touched the door.",
                    "After one sentence, every face in the room froze on me.",
                    "I spoke once, and the room stopped breathing.",
                ],
                "wrong_detail": [
                    "One sentence killed every sound in the room before the glass cracked.",
                    "The room went silent, then every face turned to the corner behind me.",
                    "After one sentence, the room froze and the lock clicked from the hallway.",
                ],
                "consequence_escape": [
                    "When the room went quiet after my sentence, I left my bag and walked out.",
                    "I should have walked out when nobody moved, but I heard the lock click behind me.",
                    "The room went silent, so I locked the door from the hallway and called security.",
                ],
            }
        elif mode == "lights_went_out":
            hook_options = {
                "whispered_moment": [
                    "The lights went out, and someone whispered behind me.",
                    "Right after the blackout, someone said my name from the wrong side of the room.",
                    "The power cut and the room dropped into dark before the exit sign died.",
                ],
                "wrong_detail": [
                    "When the lights went out, one silhouette moved in the dark.",
                    "The room went dark, then I heard my name from a spot no one could reach.",
                    "The blackout lasted three seconds, and when power flashed back, everyone had changed places.",
                ],
                "consequence_escape": [
                    "After the lights went out, I ran for the hallway before the second blackout.",
                    "I should have left when the room went dark the first time, but the exit sign pointed the wrong way.",
                    "The power died, so I followed the wall to the exit and never stepped back inside.",
                ],
            }
        elif mode == "confession_overheard":
            hallway_confession_mode = (
                "hallway" in terms
                or "door" in terms
                or self._phrase_match_count(
                    topic_key,
                    ["hallway confession", "creepy hallway confession", "in the hallway", "outside the door"],
                ) > 0
            )
            hook_options = {
                "whispered_moment": [
                    "I overheard a confession through the door, then heard my name outside.",
                    "One whispered confession in the hallway stopped me cold and moved the latch.",
                    "I heard exactly what was said before footsteps stopped on my side of the door.",
                ],
                "wrong_detail": [
                    "The confession started with one sentence that used tomorrow's date.",
                    "I heard the confession through the wall, and then I heard my name from the hallway.",
                    "What I overheard in that confession named a detail only I should know.",
                ],
                "consequence_escape": [
                    "After overhearing that confession, I ran for the stairs and sent the line to two people.",
                    "I should have walked away, but the latch clicked and I left my phone recording by the door.",
                    "That whispered confession sent me to the street with a timestamped note and one call to police.",
                ],
            }
            if hallway_confession_mode:
                hook_options = {
                    "whispered_moment": [
                        "A hallway confession came through the door while footsteps stopped beside me.",
                        "I overheard a confession in the hallway, and then the latch moved on my side.",
                        "I heard exactly what was said, then someone stood on my side of the door.",
                    ],
                    "wrong_detail": [
                        "The confession line used my full name, and the hallway light flickered.",
                        "I heard the confession through the wall, then my name from the hall side.",
                        "The door stayed shut, but a shadow stopped under it after the confession.",
                    ],
                    "consequence_escape": [
                        "After that hallway confession, I ran for the stairs and left my phone recording behind.",
                        "I left my phone recording by the door and took the street exit before the latch opened.",
                        "That confession sent me outside, where I wrote every word before the sirens started.",
                    ],
                }
        elif mode == "whispered_name":
            hook_options = {
                "whispered_moment": [
                    "I heard my name whispered from the hallway.",
                    "Someone called my name from behind the door at midnight.",
                    "I heard my name twice before I turned around.",
                ],
                "wrong_detail": [
                    "The whisper said my name, then the doorframe creaked.",
                    "I heard my name in the dark where no one was standing.",
                    "Someone called my name from the hallway, then the light blinked out.",
                ],
                "consequence_escape": [
                    "When I heard my name a third time, I backed away.",
                    "I should have left after the first whisper of my name.",
                    "I moved for the stairs when the voice used my full name.",
                ],
            }
        else:
            generic_hook_options = context.get("fallback_hook_options")
            if isinstance(generic_hook_options, dict):
                hook_options = generic_hook_options
            else:
                hook_options = {
                    "whispered_moment": [
                        f"I heard a voice from the {location} and stopped cold.",
                        f"Something whispered near the {location} when I was alone.",
                        f"I heard breathing by the {object_name} before I turned around.",
                    ],
                    "wrong_detail": [
                        f"The {location} looked normal until the {object_name} moved.",
                        f"I watched the {object_name} and one detail shifted the wrong way.",
                        f"Something changed beside the {object_name} with no one there.",
                    ],
                    "consequence_escape": [
                        f"I backed away from the {location} the second I heard that sound.",
                        f"I moved for the exit when the {object_name} clicked.",
                        "I should have left, but I waited one second too long.",
                    ],
                }

        story_angles = ["whispered_moment", "wrong_detail", "consequence_escape"]
        # Each angle gets up to THREE distinct beats per bucket so the fallback
        # body runs ~110-120 words (~35-45s at TTS pace). _compose_extended_story_body
        # keeps body sentences 1-4 in their original [open, sensory, detail, escape]
        # roles, so the storytelling anchor guardrail is unaffected by the length.
        sensory_by_angle: dict[str, list[str]] = {}
        detail_by_angle: dict[str, list[str]] = {}
        escape_by_angle: dict[str, list[str]] = {}
        if mode in {"room_went_quiet", "lights_went_out", "confession_overheard"}:
            for offset, angle in enumerate(story_angles):
                sensory_base = self._stable_index([topic_key, "fallback-sensory", angle], len(context["sensory_bits"]))
                detail_base = self._stable_index([topic_key, "fallback-detail", angle], len(context["detail_bits"]))
                escape_base = self._stable_index([topic_key, "fallback-escape", angle], len(context["escape_bits"]))
                sensory_by_angle[angle] = self._distinct_story_bits(context["sensory_bits"], sensory_base + offset, 3)
                detail_by_angle[angle] = self._distinct_story_bits(context["detail_bits"], detail_base + offset, 3)
                escape_by_angle[angle] = self._distinct_story_bits(context["escape_bits"], escape_base + offset, 3)
        else:
            sensory_lines = self._distinct_story_bits(
                context["sensory_bits"],
                self._stable_index([topic_key, "fallback-sensory"], len(context["sensory_bits"])),
                3,
            )
            detail_lines = self._distinct_story_bits(
                context["detail_bits"],
                self._stable_index([topic_key, "fallback-detail"], len(context["detail_bits"])),
                3,
            )
            escape_lines = self._distinct_story_bits(
                context["escape_bits"],
                self._stable_index([topic_key, "fallback-escape"], len(context["escape_bits"])),
                3,
            )
            for angle in story_angles:
                sensory_by_angle[angle] = sensory_lines
                detail_by_angle[angle] = detail_lines
                escape_by_angle[angle] = escape_lines

        whisper_open_line = f"I heard it first in the {location}, before I saw anything."
        wrong_detail_open_line = f"The {location} looked normal for one breath."
        consequence_open_line = "I stepped backward toward the exit immediately."
        if mode == "note_under_door":
            whisper_open_line = "Paper scraped under my door and stopped at my shoe."
            wrong_detail_open_line = "I picked up the note and recognized the handwriting instantly."
            consequence_open_line = "After reading the note, I stepped away from the doorway."
        elif mode == "hallway_movement":
            whisper_open_line = "I stayed by the doorframe and watched the hallway."
            wrong_detail_open_line = "I watched the hallway mirror and caught movement at the edge of the frame."
            consequence_open_line = "When the hallway shifted again, I went for the stairs."
        elif mode == "room_went_quiet":
            whisper_open_line = "After one sentence, the room went silent and no one reached for the door."
            wrong_detail_open_line = "The room stayed silent long enough to hear the vent and a glass crack."
            consequence_open_line = "When nobody moved, I decided to leave my bag and back toward the door."
        elif mode == "lights_went_out":
            whisper_open_line = "The lights died, and the room went black."
            wrong_detail_open_line = "In the blackout, I heard one voice near the door where nobody stood."
            consequence_open_line = "When the power failed again, I ran along the wall and got out."
        elif mode == "confession_overheard":
            hallway_confession_mode = (
                "hallway" in terms
                or "door" in terms
                or self._phrase_match_count(
                    topic_key,
                    ["hallway confession", "creepy hallway confession", "in the hallway", "outside the door"],
                ) > 0
            )
            if hallway_confession_mode:
                whisper_open_line = "I stopped in the hallway when the confession started and footsteps settled beside me."
                wrong_detail_open_line = "I heard one confession line through the door, then my name from the hall side."
                consequence_open_line = "After that confession line, I ran down the hallway and left my phone recording by the door."
            else:
                whisper_open_line = "I stopped when the confession started behind the door and used my name."
                wrong_detail_open_line = "I heard one sentence through the door, then the latch moved on my side."
                consequence_open_line = "After that confession line, I backed away and called the police from the stairs."
        elif mode == "whispered_name":
            whisper_open_line = "I heard my name from the hallway and turned slowly."
            wrong_detail_open_line = "The voice used my name again from the dark side of the door."
            consequence_open_line = "After the third whisper of my name, I left the floor."
        generic_openers = context.get("fallback_openers")
        if isinstance(generic_openers, dict):
            whisper_open_line = generic_openers.get("whispered_moment", whisper_open_line)
            wrong_detail_open_line = generic_openers.get("wrong_detail", wrong_detail_open_line)
            consequence_open_line = generic_openers.get("consequence_escape", consequence_open_line)

        reveal_open_line = wrong_detail_open_line
        reveal_hook_options = list(hook_options.get("wrong_detail", []))
        if unknown_suspense_mode:
            if mode == "elevator_wrong_floor":
                reveal_hook_options = [
                    "The call button outside lit up before anyone stepped out.",
                    "The elevator doors reopened to the same empty floor under a different number.",
                    "The floor indicator showed my number while the lobby sign stayed wrong.",
                ]
                reveal_open_line = "I pressed my floor again, and the doors opened to that wrong level."
            elif mode == "exit_sign_wrong":
                reveal_hook_options = [
                    "Every EXIT sign pointed a different direction on the same floor.",
                    "The exit arrow flipped while I was staring at it.",
                    "I followed the sign and it looped me back to the same door.",
                ]
                reveal_open_line = "I followed the sign once more, and it looped me back again."
            else:
                reveal_hook_options = (
                    list(context.get("detail_bits", [])) + list(hook_options.get("wrong_detail", []))
                )

            reveal_hook_options = [
                self._sanitize_story_fallback_line(line, context)
                for line in reveal_hook_options
                if line and not self._story_line_has_consequence(line)
            ]
            reveal_hook_options = [line for line in reveal_hook_options if line]
            if not reveal_hook_options:
                reveal_hook_options = list(hook_options.get("wrong_detail", []))

        third_angle = "consequence_escape"
        third_hook = hook_options["consequence_escape"][
            self._stable_index([topic_key, "consequence_escape", "hook"], len(hook_options["consequence_escape"]))
        ]
        third_on_screen_hook = "I LEFT FAST"
        third_thumbnail = "NOPE"
        third_lines = self._compose_extended_story_body(
            consequence_open_line,
            sensory_by_angle["consequence_escape"],
            detail_by_angle["consequence_escape"],
            escape_by_angle["consequence_escape"],
        )
        third_caption = "The moment I stopped waiting and got out."
        if unknown_suspense_mode:
            third_angle = "irreversible_reveal"
            third_hook = reveal_hook_options[
                self._stable_index([topic_key, "irreversible_reveal", "hook"], len(reveal_hook_options))
            ]
            third_on_screen_hook = "THE REVEAL HIT FIRST"
            third_thumbnail = "NO WAY"
            third_lines = self._compose_extended_story_body(
                reveal_open_line,
                sensory_by_angle["wrong_detail"],
                detail_by_angle["consequence_escape"],
                escape_by_angle["consequence_escape"],
            )
            third_caption = "The reveal hit before the decision."

        variants = [
            {
                "angle": "whispered_moment",
                "hook": hook_options["whispered_moment"][
                    self._stable_index([topic_key, "whispered_moment", "hook"], len(hook_options["whispered_moment"]))
                ],
                "on_screen_hook": "WHISPER IN THE DARK",
                "thumbnail_text": "I HEARD MY NAME",
                "lines": self._compose_extended_story_body(
                    whisper_open_line,
                    sensory_by_angle["whispered_moment"],
                    detail_by_angle["whispered_moment"],
                    escape_by_angle["whispered_moment"],
                ),
                "caption": "A whisper in the dark that felt too real.",
            },
            {
                "angle": "wrong_detail",
                "hook": hook_options["wrong_detail"][
                    self._stable_index([topic_key, "wrong_detail", "hook"], len(hook_options["wrong_detail"]))
                ],
                "on_screen_hook": "ONE DETAIL CHANGED",
                "thumbnail_text": "IT MOVED",
                "lines": self._compose_extended_story_body(
                    wrong_detail_open_line,
                    sensory_by_angle["wrong_detail"],
                    detail_by_angle["wrong_detail"],
                    escape_by_angle["wrong_detail"],
                ),
                "caption": "One wrong detail turned normal into panic.",
            },
            {
                "angle": third_angle,
                "hook": third_hook,
                "on_screen_hook": third_on_screen_hook,
                "thumbnail_text": third_thumbnail,
                "lines": third_lines,
                "caption": third_caption,
            },
        ]
        for variant in variants:
            variant["hook"] = self._sanitize_story_fallback_line(str(variant.get("hook", "")), context)
            variant["lines"] = [
                self._sanitize_story_fallback_line(str(line), context)
                for line in list(variant.get("lines", []))
            ]
        return variants

    def _fallback_lane_variants(self, lane: str) -> list[dict]:
        variants = {
            "ai_productivity": [
                {
                    "angle": "manual_pain",
                    "hook": "Stop writing these emails by hand.",
                    "on_screen_hook": "STOP MANUAL EMAILS",
                    "thumbnail_text": "STOP MANUAL",
                    "lines": [
                        "Use AI to summarize inbox threads in five bullets.",
                        "Draft the top replies, then edit and send fast.",
                        "That removes one repetitive admin task today.",
                    ],
                    "caption": "Kill manual email busywork with one AI move.",
                },
                {
                    "angle": "wrong_first_use",
                    "hook": "Most people use AI for the wrong task first.",
                    "on_screen_hook": "WRONG FIRST TASK",
                    "thumbnail_text": "WRONG TASK",
                    "lines": [
                        "Don't start with random prompts and vague ideas.",
                        "Start by cleaning one messy note set into action items.",
                        "You'll feel the time savings immediately.",
                    ],
                    "caption": "Use AI on the right first task.",
                },
                {
                    "angle": "concrete_workflow",
                    "hook": "You're wasting time cleaning notes like this.",
                    "on_screen_hook": "FIX NOTE WORKFLOW",
                    "thumbnail_text": "CLEAN NOTES FAST",
                    "lines": [
                        "Paste lecture notes and ask for a structured summary.",
                        "Turn that summary into a checklist for tomorrow.",
                        "Then automate one annoying follow-up admin step.",
                    ],
                    "caption": "Concrete AI workflow for notes and next actions.",
                },
            ],
            "storytelling": [
                {
                    "angle": "whispered_moment",
                    "hook": "Someone whispered my name from the hallway.",
                    "on_screen_hook": "I HEARD MY NAME",
                    "thumbnail_text": "THE HALLWAY",
                    "lines": [
                        "The room went still and nobody moved.",
                        "I followed the sound and saw the door shift.",
                        "I should have turned back right there.",
                    ],
                    "caption": "A whispered moment that changed everything.",
                },
                {
                    "angle": "wrong_detail",
                    "hook": "One detail felt wrong the second I looked up.",
                    "on_screen_hook": "ONE DETAIL WRONG",
                    "thumbnail_text": "IT FELT OFF",
                    "lines": [
                        "At first it looked normal, then I noticed the frame moving.",
                        "I heard footsteps where no one should be.",
                        "That's when I realized I wasn't alone.",
                    ],
                    "caption": "The eerie detail that made me leave.",
                },
                {
                    "angle": "consequence_escape",
                    "hook": "What happened next is why I ran out.",
                    "on_screen_hook": "I RAN OUT",
                    "thumbnail_text": "I LEFT FAST",
                    "lines": [
                        "I heard one sentence and the whole room went quiet.",
                        "The door slammed behind me before I reached it.",
                        "I got out, but I still hear that voice.",
                    ],
                    "caption": "Consequence moment and fast escape.",
                },
            ],
            "commentary": [
                {
                    "angle": "hard_take",
                    "hook": "Hot take: most people are wrong about this.",
                    "lines": [
                        "The popular advice sounds smart but fails in practice.",
                        "One real example beats ten recycled tips.",
                        "If you're serious, you need a sharper standard.",
                    ],
                },
                {
                    "angle": "counter_point",
                    "hook": "Nobody says this part out loud.",
                    "lines": [
                        "People copy trends instead of testing outcomes.",
                        "That's why average advice keeps producing average results.",
                        "Choose evidence, not vibes.",
                    ],
                },
                {
                    "angle": "blunt_rebuttal",
                    "hook": "Unpopular opinion: this advice is mostly noise.",
                    "lines": [
                        "If it worked, you'd see clear results by now.",
                        "The fix is less hype and more measurable actions.",
                        "That's what actually moves the needle.",
                    ],
                },
            ],
            "motivation": [
                {
                    "angle": "excuse_callout",
                    "hook": "Stop waiting. Your excuses are the problem.",
                    "lines": [
                        "You don't need a new plan, you need reps.",
                        "Do one hard action before your mood catches up.",
                        "Discipline starts before motivation shows up.",
                    ],
                },
                {
                    "angle": "discipline_frame",
                    "hook": "You don't need motivation today. You need discipline.",
                    "lines": [
                        "Show up tired and do the first hard task anyway.",
                        "Small consistent reps beat perfect plans.",
                        "That's how momentum is built.",
                    ],
                },
                {
                    "angle": "consequence_push",
                    "hook": "No one is coming to rescue your routine.",
                    "lines": [
                        "Every delay is a vote for staying stuck.",
                        "Pick one action and finish it before you scroll.",
                        "Then repeat tomorrow without negotiation.",
                    ],
                },
            ],
        }
        return variants.get(lane, variants["ai_productivity"])
