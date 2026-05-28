"""
Topic Fetcher - AI-news topic source for the two-host dialogue pipeline.

Thin wrapper around the existing TopicFinder (topic_finder.py): it supplies the
AI-tools/news niche config (RSS feeds + subreddits + keywords) and exposes one
clean entry point, fetch_fresh_topic(), that returns a single fresh, unseen
AI-news item with enough source text for dialogue_writer.py to fact-check
against.

Why reuse TopicFinder instead of a new fetcher: TopicFinder already does RSS
(feedparser), Reddit (praw), recency-weighted scoring, and seen-topic dedup with
a 30-day expiry log at drafts/seen_topics.json. Rebuilding that would duplicate
~200 lines for no gain.

Freshness is the channel's defense against YouTube's 2026 "inauthentic content"
policy: every video covers a genuinely new AI release, so dedup here is load
bearing, not cosmetic.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from topic_finder import TopicFinder

logger = logging.getLogger("RevenueMachine.TopicFetcher")

# AI-tools/news niche config consumed by TopicFinder. Feeds chosen for steady,
# high-signal AI coverage; adjust freely. Reddit is used only if real
# REDDIT_CLIENT_ID / REDDIT_SECRET are set in .env (placeholders are ignored).
AI_NEWS_NICHE_CONFIG: dict[str, Any] = {
    "rss_feeds": [
        "https://techcrunch.com/category/artificial-intelligence/feed/",
        "https://venturebeat.com/category/ai/feed/",
        "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        "https://feeds.arstechnica.com/arstechnica/technology-lab",
        "https://www.technologyreview.com/feed/",
    ],
    "subreddits": ["artificial", "LocalLLaMA", "OpenAI", "MachineLearning"],
    "keywords": [
        "ai", "gpt", "llm", "model", "openai", "anthropic", "claude", "gemini",
        "agent", "chatbot", "machine learning", "neural", "open source",
        "benchmark", "release", "launch",
    ],
    "hook_templates": [],
    "affiliate_programs": {},
}

_REDDIT_PLACEHOLDERS = {"", "YOUR_REDDIT_APP_ID", "YOUR_REDDIT_SECRET"}


def _build_config() -> dict[str, str]:
    """Reads provider credentials from the environment for TopicFinder."""
    config = {
        "REDDIT_CLIENT_ID": os.getenv("REDDIT_CLIENT_ID", "").strip(),
        "REDDIT_SECRET": os.getenv("REDDIT_SECRET", "").strip(),
        "YOUTUBE_API_KEY": os.getenv("YOUTUBE_API_KEY", "").strip(),
        "NICHE": "ai_tools",
    }
    # Drop placeholder Reddit creds so TopicFinder cleanly skips that source
    # instead of failing an auth call on every run.
    if config["REDDIT_CLIENT_ID"] in _REDDIT_PLACEHOLDERS:
        config["REDDIT_CLIENT_ID"] = ""
    if config["REDDIT_SECRET"] in _REDDIT_PLACEHOLDERS:
        config["REDDIT_SECRET"] = ""
    if config["YOUTUBE_API_KEY"] in {"", "YOUR_YOUTUBE_API_KEY_HERE"}:
        config["YOUTUBE_API_KEY"] = ""
    return config


def fetch_fresh_topic(
    *,
    mark_seen: bool = True,
    niche_config: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Returns one fresh, unseen AI-news item, or None if nothing is available.

    The returned dict carries: title, source, url, timestamp, score, and
    `summary` (the source text dialogue_writer.py fact-checks against).

    `mark_seen=True` records the picked topic in the seen-topics log so the next
    run will not cover it again. Pass False for dry runs / tests.
    """
    finder = TopicFinder(
        config=_build_config(),
        niche_config=niche_config or AI_NEWS_NICHE_CONFIG,
    )

    topics = finder.get_trending_topics(count=5)
    # get_trending_topics already filters seen topics and scores by recency +
    # niche relevance; topics[0] is the best fresh candidate.
    fresh = [t for t in topics if t.get("source") != "fallback" and t.get("title")]
    if not fresh:
        logger.warning("topic_fetcher: no fresh AI-news topic available this run.")
        return None

    picked = fresh[0]
    if mark_seen:
        # _mark_seen persists immediately, so a crash after this point cannot
        # cause the same item to be re-covered on the next run.
        finder._mark_seen(picked)
    logger.info(
        "topic_fetcher: picked '%s' (source=%s score=%s)",
        picked.get("title", "")[:80],
        picked.get("source", "?"),
        picked.get("score", "?"),
    )
    return picked


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    topic = fetch_fresh_topic(mark_seen=False)
    if topic:
        print("TITLE:", topic.get("title", ""))
        print("SOURCE:", topic.get("source", ""), "| URL:", topic.get("url", ""))
        print("SUMMARY:", (topic.get("summary", "") or "")[:300])
    else:
        print("No fresh topic available.")
