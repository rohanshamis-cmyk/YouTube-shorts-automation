"""
Topic Finder - Discovers viral-potential topics from multiple sources.
Sources: Reddit (PRAW), Google Trends (pytrends), RSS feeds, YouTube trending
"""

import os
import json
import time
import hashlib
import logging
import feedparser
import requests
from datetime import datetime, timedelta
from pathlib import Path

try:
    import praw
    REDDIT_AVAILABLE = True
except ImportError:
    REDDIT_AVAILABLE = False

try:
    from pytrends.request import TrendReq
    PYTRENDS_AVAILABLE = True
except ImportError:
    PYTRENDS_AVAILABLE = False

logger = logging.getLogger("RevenueMachine.TopicFinder")

SEEN_TOPICS_FILE = Path("drafts/seen_topics.json")


class TopicFinder:
    def __init__(self, config: dict, niche_config: dict):
        self.config = config
        self.niche = niche_config
        self.seen_topics = self._load_seen_topics()

        # Init Reddit if available
        if REDDIT_AVAILABLE and config.get("REDDIT_CLIENT_ID"):
            try:
                self.reddit = praw.Reddit(
                    client_id=config["REDDIT_CLIENT_ID"],
                    client_secret=config["REDDIT_SECRET"],
                    user_agent="RevenueMachine/1.0",
                )
                logger.info("✅ Reddit API connected")
            except Exception as e:
                logger.warning(f"Reddit init failed: {e}")
                self.reddit = None
        else:
            self.reddit = None

        # Init Google Trends
        if PYTRENDS_AVAILABLE:
            try:
                self.pytrends = TrendReq(hl="en-US", tz=360)
                logger.info("✅ Google Trends connected")
            except Exception as e:
                logger.warning(f"Google Trends init failed: {e}")
                self.pytrends = None
        else:
            self.pytrends = None

    def get_trending_topics(self, count: int = 5) -> list:
        """
        Aggregates trending topics from all sources.
        Returns list of scored topic dicts.
        """
        all_topics = []

        # Source 1: Reddit
        reddit_topics = self._get_reddit_topics()
        all_topics.extend(reddit_topics)
        logger.info(f"  Reddit: {len(reddit_topics)} topics")

        # Source 2: Google Trends
        trend_topics = self._get_google_trends()
        all_topics.extend(trend_topics)
        logger.info(f"  Google Trends: {len(trend_topics)} topics")

        # Source 3: RSS Feeds
        rss_topics = self._get_rss_topics()
        all_topics.extend(rss_topics)
        logger.info(f"  RSS: {len(rss_topics)} topics")

        # Source 4: YouTube Trending (scrape via API)
        yt_topics = self._get_youtube_trending()
        all_topics.extend(yt_topics)
        logger.info(f"  YouTube Trending: {len(yt_topics)} topics")

        # Filter out already-used topics
        fresh_topics = [t for t in all_topics if not self._is_seen(t)]
        logger.info(f"  Fresh (unseen) topics: {len(fresh_topics)}")

        if not fresh_topics:
            logger.warning("All topics already seen. Clearing history...")
            self.seen_topics = {}
            fresh_topics = all_topics

        # Score and deduplicate
        scored = self._score_topics(fresh_topics)
        return scored[:count]

    def score_and_pick(self, topics: list) -> dict:
        """Picks the single best topic from a scored list."""
        if not topics:
            return self._get_fallback_topic()
        return topics[0]  # Already sorted by score

    # ──────────────────────────────────────────────────
    #  REDDIT SOURCE
    # ──────────────────────────────────────────────────
    def _get_reddit_topics(self) -> list:
        topics = []
        if not self.reddit:
            return topics

        try:
            for subreddit_name in self.niche.get("subreddits", [])[:3]:
                subreddit = self.reddit.subreddit(subreddit_name)
                for post in subreddit.hot(limit=10):
                    if post.score > 100 and not post.is_self:  # Link posts tend to be newsworthy
                        continue
                    if post.score > 50:  # Hot post
                        topics.append({
                            "title": post.title,
                            "source": "reddit",
                            "subreddit": subreddit_name,
                            "engagement": post.score + post.num_comments * 5,
                            "url": f"https://reddit.com{post.permalink}",
                            "timestamp": datetime.fromtimestamp(post.created_utc).isoformat(),
                        })
        except Exception as e:
            logger.warning(f"Reddit scrape error: {e}")

        return topics

    # ──────────────────────────────────────────────────
    #  GOOGLE TRENDS SOURCE
    # ──────────────────────────────────────────────────
    def _get_google_trends(self) -> list:
        topics = []
        if not self.pytrends:
            return topics

        try:
            # Get trending searches for today
            trending_searches = self.pytrends.trending_searches(pn="united_states")
            for term in trending_searches[0].tolist()[:10]:
                topics.append({
                    "title": term,
                    "source": "google_trends",
                    "engagement": 1000,  # Trending = high volume
                    "url": f"https://trends.google.com/trends/explore?q={term.replace(' ', '+')}",
                    "timestamp": datetime.now().isoformat(),
                })
        except Exception as e:
            logger.warning(f"Google Trends error: {e}")

        return topics

    # ──────────────────────────────────────────────────
    #  RSS FEEDS SOURCE
    # ──────────────────────────────────────────────────
    def _get_rss_topics(self) -> list:
        topics = []
        cutoff = datetime.now() - timedelta(hours=48)

        for feed_url in self.niche.get("rss_feeds", []):
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:5]:
                    # Check if recent enough
                    pub_date = None
                    if hasattr(entry, "published_parsed") and entry.published_parsed:
                        import calendar
                        pub_date = datetime.fromtimestamp(calendar.timegm(entry.published_parsed))
                        if pub_date < cutoff:
                            continue

                    topics.append({
                        "title": entry.get("title", ""),
                        "source": "rss",
                        "feed": feed_url,
                        "engagement": 200,
                        "url": entry.get("link", ""),
                        "timestamp": pub_date.isoformat() if pub_date else datetime.now().isoformat(),
                        "summary": entry.get("summary", "")[:200],
                    })
            except Exception as e:
                logger.warning(f"RSS parse error ({feed_url}): {e}")

        return topics

    # ──────────────────────────────────────────────────
    #  YOUTUBE TRENDING SOURCE
    # ──────────────────────────────────────────────────
    def _get_youtube_trending(self) -> list:
        """Gets trending YouTube videos in the niche category."""
        topics = []

        try:
            # Use YouTube Data API to get trending videos
            api_key = self.config.get("YOUTUBE_API_KEY", "")
            if not api_key:
                return topics

            # Category 28 = Science & Tech, 22 = People & Blogs, 25 = News & Politics
            category_map = {
                "ai_tools": "28",
                "finance": "25",
                "motivation": "22",
            }
            category = category_map.get(self.config.get("NICHE", "ai_tools"), "28")

            url = (
                f"https://www.googleapis.com/youtube/v3/videos"
                f"?part=snippet,statistics"
                f"&chart=mostPopular"
                f"&regionCode=US"
                f"&videoCategoryId={category}"
                f"&maxResults=10"
                f"&key={api_key}"
            )
            resp = requests.get(url, timeout=10)
            data = resp.json()

            for item in data.get("items", []):
                views = int(item["statistics"].get("viewCount", 0))
                if views > 10000:  # Only viral-scale content
                    topics.append({
                        "title": item["snippet"]["title"],
                        "source": "youtube_trending",
                        "engagement": views,
                        "url": f"https://youtube.com/watch?v={item['id']}",
                        "timestamp": item["snippet"]["publishedAt"],
                        "tags": item["snippet"].get("tags", []),
                    })
        except Exception as e:
            logger.warning(f"YouTube trending error: {e}")

        return topics

    # ──────────────────────────────────────────────────
    #  SCORING ALGORITHM
    # ──────────────────────────────────────────────────
    def _score_topics(self, topics: list) -> list:
        """
        Scores topics based on:
        - Engagement (likes/views/comments)
        - Recency (newer = higher score)
        - Keyword relevance to niche
        - Uniqueness (penalize duplicates)
        """
        niche_keywords = self.niche.get("keywords", [])
        hook_words = ["new", "just dropped", "breaking", "secret", "nobody", "viral",
                      "shocking", "revealed", "exposed", "leaked", "banned", "deleted"]

        scored = []
        seen_similar = set()

        for topic in topics:
            if not topic.get("title"):
                continue

            title_lower = topic["title"].lower()

            # Check for duplicate-similar topics
            title_hash = title_lower[:30]
            if title_hash in seen_similar:
                continue
            seen_similar.add(title_hash)

            # Base score from engagement
            score = min(topic.get("engagement", 0) / 100, 100)

            # Recency bonus (1-24 hours old = +30 points)
            try:
                pub_time = datetime.fromisoformat(topic.get("timestamp", ""))
                hours_old = (datetime.now() - pub_time).total_seconds() / 3600
                if hours_old < 6:
                    score += 50
                elif hours_old < 24:
                    score += 30
                elif hours_old < 48:
                    score += 10
            except Exception:
                pass

            # Niche keyword relevance bonus
            for kw in niche_keywords:
                if kw.lower() in title_lower:
                    score += 15

            # Hook word bonus
            for hw in hook_words:
                if hw in title_lower:
                    score += 8

            # Source reliability bonus
            source_bonus = {"reddit": 10, "google_trends": 20, "youtube_trending": 25, "rss": 5}
            score += source_bonus.get(topic.get("source", ""), 0)

            topic["score"] = round(score, 2)
            scored.append(topic)

        # Sort by score descending
        return sorted(scored, key=lambda x: x["score"], reverse=True)

    # ──────────────────────────────────────────────────
    #  SEEN TOPICS MANAGEMENT
    # ──────────────────────────────────────────────────
    def _get_topic_hash(self, topic: dict) -> str:
        return hashlib.md5(topic.get("title", "").lower().encode()).hexdigest()[:8]

    def _is_seen(self, topic: dict) -> bool:
        return self._get_topic_hash(topic) in self.seen_topics

    def _mark_seen(self, topic: dict):
        h = self._get_topic_hash(topic)
        self.seen_topics[h] = datetime.now().isoformat()
        self._save_seen_topics()

    def _load_seen_topics(self) -> dict:
        if SEEN_TOPICS_FILE.exists():
            try:
                with open(SEEN_TOPICS_FILE) as f:
                    data = json.load(f)
                # Remove topics older than 30 days
                cutoff = datetime.now() - timedelta(days=30)
                return {k: v for k, v in data.items()
                        if datetime.fromisoformat(v) > cutoff}
            except Exception:
                pass
        return {}

    def _save_seen_topics(self):
        SEEN_TOPICS_FILE.parent.mkdir(exist_ok=True)
        with open(SEEN_TOPICS_FILE, "w") as f:
            json.dump(self.seen_topics, f, indent=2)

    def _get_fallback_topic(self) -> dict:
        """Emergency fallback when no topics found."""
        fallback_titles = {
            "ai_tools": "This new AI tool will save you 10 hours per week",
            "finance": "The passive income strategy most people overlook",
            "motivation": "Stop waiting for motivation. Do this instead.",
        }
        niche = self.config.get("NICHE", "ai_tools")
        return {
            "title": fallback_titles.get(niche, "Something incredible just happened in AI"),
            "source": "fallback",
            "engagement": 0,
            "score": 0,
            "timestamp": datetime.now().isoformat(),
        }
