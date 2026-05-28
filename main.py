"""
╔══════════════════════════════════════════════════════════════════╗
║        AI YOUTUBE SHORTS REVENUE MACHINE - MAIN PIPELINE        ║
║   Fully automated: Trend → Script → Voice → Video → Upload      ║
╚══════════════════════════════════════════════════════════════════╝

BUGS FIXED (v1.1):
  [1] Added load_dotenv() — .env file now loads correctly
  [2] thumbnail_creator.py and analytics_tracker.py are now
      proper standalone modules (were buried in affiliate_manager)
  [3] --dry-run now truly stops before upload/cross-post
  [4] Config keys aligned with cross_poster.py:
        TIKTOK_ACCESS_TOKEN (was TIKTOK_SESSION_ID)
        INSTAGRAM_ACCESS_TOKEN (was INSTAGRAM_USERNAME)
        INSTAGRAM_BUSINESS_ACCOUNT_ID (was INSTAGRAM_PASSWORD)
  [5] script['words'] accessed safely with .get()
"""

import os, re, sys, time, logging, schedule
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Fix [1]: Load .env BEFORE reading os.getenv()
load_dotenv()

# Fix [2]: All imports now point to proper standalone modules
from topic_finder      import TopicFinder
from script_generator  import ScriptGenerator
from voiceover         import VoiceoverEngine
from video_generator   import VideoGenerator
from thumbnail_creator import ThumbnailCreator
from uploader          import YouTubeUploader
from cross_poster      import CrossPoster
from affiliate_manager import AffiliateManager
from analytics_tracker import AnalyticsTracker

# ════════════════════════════════════════════
#  CONFIGURATION
# ════════════════════════════════════════════
CONFIG = {
    "ANTHROPIC_API_KEY":            os.getenv("ANTHROPIC_API_KEY"),
    "OPENAI_API_KEY":               os.getenv("OPENAI_API_KEY"),
    "ELEVENLABS_API_KEY":           os.getenv("ELEVENLABS_API_KEY"),
    "YOUTUBE_CLIENT_SECRETS":       os.getenv("YOUTUBE_CLIENT_SECRETS", "client_secrets.json"),
    "YOUTUBE_API_KEY":              os.getenv("YOUTUBE_API_KEY"),
    "PEXELS_API_KEY":               os.getenv("PEXELS_API_KEY"),
    "PIXABAY_API_KEY":              os.getenv("PIXABAY_API_KEY"),
    "REDDIT_CLIENT_ID":             os.getenv("REDDIT_CLIENT_ID"),
    "REDDIT_SECRET":                os.getenv("REDDIT_SECRET"),
    # Fix [4]: Correct key names matching cross_poster.py
    "TIKTOK_ACCESS_TOKEN":          os.getenv("TIKTOK_ACCESS_TOKEN"),
    "INSTAGRAM_ACCESS_TOKEN":       os.getenv("INSTAGRAM_ACCESS_TOKEN"),
    "INSTAGRAM_BUSINESS_ACCOUNT_ID":os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID"),
    "TELEGRAM_BOT_TOKEN":           os.getenv("TELEGRAM_BOT_TOKEN"),
    "TELEGRAM_CHAT_ID":             os.getenv("TELEGRAM_CHAT_ID"),
    "GOOGLE_SHEETS_ID":             os.getenv("GOOGLE_SHEETS_ID"),
    "NICHE":                        os.getenv("CHANNEL_NICHE", "ai_tools"),
    "VIDEOS_PER_DAY":               int(os.getenv("VIDEOS_PER_DAY", "3")),
    "UPLOAD_TIMES":                 ["08:00", "13:00", "19:00"],
    "OUTPUT_DIR":                   Path("outputs"),
    "DRAFTS_DIR":                   Path("drafts"),
    "LOGS_DIR":                     Path("logs"),
    "VIDEO_RESOLUTION":             "1080x1920",
    "VIDEO_FPS":                    30,
    "MAX_DURATION":                 59,
    "MIN_DURATION":                 15,
    "VOICE_ID":                     "21m00Tcm4TlvDq8ikWAM",
    "VOICE_STABILITY":              0.5,
    "VOICE_SIMILARITY":             0.75,
}

NICHE_CONFIGS = {
    "ai_tools": {
        "subreddits":  ["artificial", "ChatGPT", "MachineLearning", "singularity"],
        "rss_feeds":   ["https://techcrunch.com/category/artificial-intelligence/feed/"],
        "keywords":    ["AI tool", "ChatGPT", "Claude", "new AI", "AI replaced"],
        "affiliate_programs": {
            "jasper":     {"link": "https://jasper.ai?fpr=YOUR_REF",             "cpa": 30},
            "elevenlabs": {"link": "https://elevenlabs.io/sign-up?ref=YOUR_REF", "cpa": 20},
            "cursor":     {"link": "https://cursor.com",                          "cpa": 0},
        },
        "cpm_estimate": 12,
        "hook_templates": [
            "This AI tool does {X} in 10 seconds (I'm not joking)",
            "Nobody is talking about this AI yet...",
            "The AI that replaced my entire workflow in 3 days",
        ],
    },
    "finance": {
        "subreddits":  ["personalfinance", "financialindependence", "investing"],
        "rss_feeds":   ["https://www.cnbc.com/id/100003114/device/rss/rss.html"],
        "keywords":    ["passive income", "make money", "side hustle", "investing"],
        "affiliate_programs": {
            "robinhood": {"link": "https://robinhood.c3me6x.net/YOUR_REF", "cpa": 5},
            "coinbase":  {"link": "https://coinbase.com/join/YOUR_REF",    "cpa": 10},
        },
        "cpm_estimate": 18,
        "hook_templates": [
            "How I made ${X} while I was sleeping",
            "The passive income strategy nobody talks about",
        ],
    },
    "motivation": {
        "subreddits":  ["GetMotivated", "selfimprovement", "productivity"],
        "rss_feeds":   [],
        "keywords":    ["productivity", "discipline", "success", "mindset"],
        "affiliate_programs": {
            "audible":    {"link": "https://amzn.to/YOUR_REF",             "cpa": 5},
            "skillshare": {"link": "https://skillshare.com?ref=YOUR_REF",  "cpa": 7},
        },
        "cpm_estimate": 8,
        "hook_templates": [
            "Stop wasting your mornings. Do this instead.",
            "Why 99% of people never achieve their goals",
        ],
    },
}


def setup_logging() -> logging.Logger:
    CONFIG["LOGS_DIR"].mkdir(parents=True, exist_ok=True)
    log_file = CONFIG["LOGS_DIR"] / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("RevenueMachine")


def validate_config(dry_run: bool = False) -> bool:
    """Fail fast with clear messages if keys are missing."""
    required: dict[str, str] = {}
    if not (CONFIG.get("OPENAI_API_KEY") or CONFIG.get("ANTHROPIC_API_KEY")):
        required["OPENAI_API_KEY or ANTHROPIC_API_KEY"] = (
            "Set OPENAI_API_KEY (preferred) or ANTHROPIC_API_KEY"
        )
    if not dry_run:
        required.update({
            "ELEVENLABS_API_KEY": "https://elevenlabs.io (free tier available)",
            "PEXELS_API_KEY":     "https://www.pexels.com/api/ (free)",
        })
    missing = [f"  • {k}  →  {url}" for k, url in required.items() if not CONFIG.get(k)]
    if missing:
        print("\n❌  Missing required keys in .env:\n" + "\n".join(missing))
        print("\nCopy .env.template → .env and fill in the values.\n")
        return False
    return True


def _dry_run_enrichment(topic_title: str, script: dict) -> dict:
    """Build posting-friendly dry-run fields without external APIs."""
    topic_text = (topic_title or "").strip()
    angle_match = re.search(r"\(([^()]+)\)\s*$", topic_text)
    angle = angle_match.group(1).strip().lower() if angle_match else ""
    clean_topic = re.sub(r"\s*\([^()]+\)\s*$", "", topic_text).strip() or topic_text
    topic_lower = clean_topic.lower()

    if "study" in topic_lower and "hack" in topic_lower:
        hashtags = ["#StudyHacks", "#SmartStudy", "#Shorts"]
        caption = "Study smarter with hacks you can use today."
        default_cta = "Save this for your next study session."
    elif "student" in topic_lower and "product" in topic_lower:
        hashtags = ["#StudentProductivity", "#StudentTips", "#Shorts"]
        caption = "Student productivity tips that actually stick."
        default_cta = "Follow for more student productivity shortcuts."
    elif "ai" in topic_lower and "tool" in topic_lower:
        hashtags = ["#AITools", "#Productivity", "#Shorts"]
        caption = "AI tools to save time this week."
        default_cta = "Follow for more AI tools that save hours."
    else:
        words = re.findall(r"[a-zA-Z0-9]+", clean_topic)
        core = [w for w in words if len(w) >= 3][:2]
        hashtags = [f"#{w.title()}" for w in core]
        while len(hashtags) < 2:
            hashtags.append("#Shorts")
        hashtags.append("#Shorts")
        hashtags = hashtags[:3]
        caption = f"{clean_topic} in under 60 seconds."
        default_cta = "Follow for more quick tips."

    hook = (script.get("hook", "") or "").strip()
    description_snippet = (script.get("description_snippet", "") or "").strip()
    caption_line = description_snippet or hook or caption
    raw_title = (script.get("title", "") or clean_topic).strip()
    video_title = re.sub(r"\s*\([^()]+\)\s*$", "", raw_title).strip() or raw_title
    on_screen_hook = (script.get("on_screen_hook", "") or hook or video_title).strip()
    if len(on_screen_hook) > 52:
        on_screen_hook = on_screen_hook[:49].rstrip() + "..."

    broll_idea = "Close-up of notes and laptop while studying."
    scenes = script.get("scenes", [])
    if isinstance(scenes, list) and scenes:
        first_scene = scenes[0]
        if isinstance(first_scene, dict):
            broll_idea = (
                (first_scene.get("description", "") or "").strip()
                or (first_scene.get("stock_query", "") or "").strip()
                or broll_idea
            )
    broll_idea = re.sub(r"\s+", " ", broll_idea).strip()

    post_time_map = {
        "beginner tip": "07:30-09:00 local",
        "mistake to avoid": "12:00-13:30 local",
        "tool recommendation": "18:00-20:00 local",
        "quick workflow": "16:00-18:00 local",
        "time-saving trick": "20:00-22:00 local",
    }
    best_post_time_guess = post_time_map.get(angle, "17:00-19:00 local")

    if "study" in topic_lower or "student" in topic_lower:
        target_audience = "Students and learners preparing for exams."
    elif "ai" in topic_lower and "tool" in topic_lower:
        target_audience = "Students and creators exploring AI productivity tools."
    else:
        target_audience = "Short-form viewers looking for practical tips."

    thumbnail_map = {
        "beginner tip": "START HERE",
        "mistake to avoid": "STOP THIS",
        "tool recommendation": "USE THIS TOOL",
        "quick workflow": "3-STEP FLOW",
        "time-saving trick": "SAVE TIME FAST",
    }
    thumbnail_text = (script.get("thumbnail_text", "") or "").strip() or thumbnail_map.get(angle)
    if not thumbnail_text:
        words = re.findall(r"[A-Za-z0-9]+", on_screen_hook)
        thumbnail_text = " ".join(words[:3]).upper() if words else "WATCH THIS"

    shot_map = {
        "beginner tip": [
            "close-up of notebook and laptop",
            "typing a simple prompt into an AI tool",
            "AI summary showing on screen",
            "student highlighting key notes",
            "final checklist overlay",
        ],
        "mistake to avoid": [
            "messy study desk with too many tabs open",
            "typing a vague AI prompt",
            "confusing output on screen",
            "rewriting prompt with clear instructions",
            "before vs after result split-screen",
        ],
        "tool recommendation": [
            "opening recommended AI tool homepage",
            "quick feature walkthrough on screen",
            "copying class notes into the tool",
            "clean summary generated in seconds",
            "student reviewing concise notes",
        ],
        "quick workflow": [
            "timer starts at 30 minutes",
            "step 1 prompt typed on screen",
            "step 2 output converted to checklist",
            "step 3 active recall with flashcards",
            "workflow recap with 3 bullets",
        ],
        "time-saving trick": [
            "calendar with tight study schedule",
            "single AI prompt template on screen",
            "auto-generated study outline appears",
            "student reviewing only high-priority items",
            "time saved counter overlay",
        ],
    }
    default_shot_list = [
        "close-up of laptop and notes",
        "typing key prompt on screen",
        "result appears in AI tool",
        "highlighting takeaways in notebook",
        "final action checklist overlay",
    ]
    shot_list = shot_map.get(angle, default_shot_list)

    edit_map = {
        "beginner tip": [
            "use fast cuts between each step",
            "bold first hook text in first 2 seconds",
            "add subtle zoom on key terms",
            "keep total pace under 35 seconds",
        ],
        "mistake to avoid": [
            "start with high-contrast warning text",
            "use red highlight on the mistake moment",
            "cut quickly to the corrected approach",
            "keep rhythm tight and energetic",
        ],
        "tool recommendation": [
            "show tool name clearly in first frame",
            "use cursor highlights on important clicks",
            "add short labels for each feature",
            "end with clear save/follow CTA card",
        ],
        "quick workflow": [
            "show step numbers as large overlays",
            "use quick whoosh transitions between steps",
            "freeze frame briefly on final checklist",
            "keep total runtime around 30-35 seconds",
        ],
        "time-saving trick": [
            "lead with a strong time-saving promise",
            "use timer/countdown overlays",
            "zoom on the shortcut moment",
            "finish with clear next-action text",
        ],
    }
    edit_notes = edit_map.get(
        angle,
        [
            "use fast cuts and clear text overlays",
            "highlight key phrase in first 2 seconds",
            "keep runtime under 35 seconds",
        ],
    )

    narration_text = (
        script.get("narration", script.get("script", script.get("content", ""))) or ""
    ).strip()

    def _normalize_caption_line(value: str) -> str:
        text = re.sub(r"\s+", " ", (value or "").strip())
        text = re.sub(r"^[\-\d\.\)\s]+", "", text)
        return text.strip(" .")

    caption_lines: list[str] = []
    chunks = re.split(r"(?:\n+|(?<=[.!?])\s+)", narration_text)
    for chunk in chunks:
        line = _normalize_caption_line(chunk)
        if not line:
            continue
        if len(line) > 72:
            sub_chunks = [part.strip() for part in re.split(r"[;,]", line) if part.strip()]
            if sub_chunks:
                for part in sub_chunks:
                    short_part = _normalize_caption_line(part)
                    if short_part:
                        caption_lines.append(short_part)
                    if len(caption_lines) >= 8:
                        break
                if len(caption_lines) >= 8:
                    break
                continue
        caption_lines.append(line)
        if len(caption_lines) >= 8:
            break

    if len(caption_lines) < 4:
        filler_lines = [
            on_screen_hook,
            "Use this simple approach today.",
            "Save this short for your next session.",
            (script.get("cta", "") or default_cta),
        ]
        for filler in filler_lines:
            normalized = _normalize_caption_line(filler)
            if normalized and normalized not in caption_lines:
                caption_lines.append(normalized)
            if len(caption_lines) >= 4:
                break

    return {
        "title": raw_title,
        "topic": clean_topic,
        "hook": hook,
        "on_screen_hook": on_screen_hook,
        "video_title": video_title,
        "broll_idea": broll_idea,
        "posting_order": "1",
        "best_post_time_guess": best_post_time_guess,
        "target_audience": target_audience,
        "thumbnail_text": thumbnail_text,
        "shot_list": shot_list[:6],
        "edit_notes": edit_notes[:5],
        "caption_lines": caption_lines[:8],
        "narration": narration_text,
        "hashtags": hashtags[:3],
        "caption": caption_line,
        "cta": (script.get("cta", "") or default_cta).strip(),
    }


class RevenueMachine:
    def __init__(self, dry_run: bool = False):
        self.logger       = setup_logging()
        self.niche_config = NICHE_CONFIGS.get(CONFIG["NICHE"], NICHE_CONFIGS["ai_tools"])
        self.topic_finder = TopicFinder(CONFIG, self.niche_config)
        self.script_gen   = ScriptGenerator(CONFIG, self.niche_config)
        self.voiceover    = VoiceoverEngine(CONFIG)
        self.video_gen    = VideoGenerator(CONFIG)
        self.thumbnail    = ThumbnailCreator(CONFIG)
        self.uploader     = None if dry_run else YouTubeUploader(CONFIG)
        self.cross_poster = None if dry_run else CrossPoster(CONFIG)
        self.affiliate    = AffiliateManager(self.niche_config)
        self.analytics    = AnalyticsTracker(CONFIG)
        for d in [CONFIG["OUTPUT_DIR"], CONFIG["DRAFTS_DIR"]]:
            d.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"🚀 Revenue Machine v1.1 | Niche: {CONFIG['NICHE']}")

    def run_daily_pipeline(
        self,
        dry_run: bool = False,
        topic_override: str | None = None,
        batch_position: int | None = None,
    ) -> bool:
        self.logger.info("=" * 60)
        mode = "DRY RUN" if dry_run else "LIVE"
        self.logger.info(f"🎬 PIPELINE [{mode}] @ {datetime.now().strftime('%H:%M:%S')}")
        self.logger.info("=" * 60)
        topic_override = (topic_override or "").strip() or None

        # 1. Topic
        if topic_override:
            self.logger.info("🧭 [1/9] Using provided topic...")
            topic = {
                "title": topic_override,
                "source": "manual",
                "engagement": 0,
                "score": 0,
                "summary": "",
                "timestamp": datetime.now().isoformat(),
            }
            if batch_position and batch_position > 0:
                topic["batch_position"] = batch_position
        else:
            self.logger.info("🔍 [1/9] Discovering trending topics...")
            topics = self.topic_finder.get_trending_topics(count=5)
            if not topics:
                if dry_run:
                    self.logger.warning("⚠️ No topics found — using fallback topic for dry run.")
                    topic = self.topic_finder.score_and_pick([])
                else:
                    self.logger.error("❌ No topics — skipping run.")
                    return False
            else:
                topic = self.topic_finder.score_and_pick(topics)
        self.logger.info(f"✅ Topic: '{topic['title']}' (score: {topic['score']})")

        # 2. Script
        self.logger.info("✍️  [2/9] Generating viral script...")
        script   = self.script_gen.generate(topic=topic, duration_target=45)
        # Fix [5]: words is an int, not a list
        words    = script.get("words", 0)
        duration = script.get("estimated_duration", "?")
        self.logger.info(f"✅ Script: '{script['title']}' ({words} words, ~{duration}s)")

        # Fix [3]: --dry-run truly stops here
        if dry_run:
            from pathlib import Path
            Path("drafts").mkdir(exist_ok=True)
            draft_file = Path("drafts") / "latest_dry_run_script.txt"
            enriched = _dry_run_enrichment(topic.get("title", ""), script)
            draft_text = (
                f"TITLE: {enriched['title']}\n"
                f"TOPIC: {enriched['topic']}\n"
                f"HOOK: {enriched['hook']}\n"
                f"ON_SCREEN_HOOK: {enriched['on_screen_hook']}\n"
                f"VIDEO_TITLE: {enriched['video_title']}\n"
                f"BROLL_IDEA: {enriched['broll_idea']}\n\n"
                f"POSTING_ORDER: {enriched['posting_order']}\n"
                f"BEST_POST_TIME_GUESS: {enriched['best_post_time_guess']}\n"
                f"TARGET_AUDIENCE: {enriched['target_audience']}\n"
                f"THUMBNAIL_TEXT: {enriched['thumbnail_text']}\n\n"
                f"SHOT_LIST:\n"
                + "\n".join(f"{i + 1}. {item}" for i, item in enumerate(enriched["shot_list"]))
                + "\n\n"
                f"EDIT_NOTES:\n"
                + "\n".join(f"- {item}" for item in enriched["edit_notes"])
                + "\n\n"
                f"SCRIPT:\n{enriched['narration']}\n\n"
                f"CAPTION_LINES:\n"
                + "\n".join(
                    f"{i + 1}. {line}" for i, line in enumerate(enriched["caption_lines"])
                )
                + "\n\n"
                f"HASHTAGS:\n"
                f"- {enriched['hashtags'][0]}\n"
                f"- {enriched['hashtags'][1]}\n"
                f"- {enriched['hashtags'][2]}\n\n"
                f"CAPTION: {enriched['caption']}\n"
                f"CTA: {enriched['cta']}\n"
            )
            draft_file.write_text(draft_text, encoding="utf-8")
            self.logger.info("=" * 60)
            self.logger.info("✅ DRY RUN COMPLETE — script generated, nothing uploaded.")
            self.logger.info(f"   Title: {script['title']}")
            self.logger.info(f"   Hook:  {script.get('hook', '')}")
            self.logger.info(f"   Saved: {draft_file}")
            self.logger.info("=" * 60)
            return True

        # 3. Voiceover
        self.logger.info("🎙️  [3/9] Creating AI voiceover...")
        audio_path = self.voiceover.generate(
            text=script["narration"], output_dir=CONFIG["OUTPUT_DIR"], voice_id=CONFIG["VOICE_ID"]
        )
        self.logger.info(f"✅ Audio: {audio_path}")

        # 4. Stock footage
        self.logger.info("🎥 [4/9] Sourcing stock footage...")
        footage = self.video_gen.fetch_stock_footage(
            queries=script.get("visual_queries", [topic["title"]]),
            count=max(len(script.get("scenes", [])), 3),
        )
        self.logger.info(f"✅ {len(footage)} clips fetched")

        # 5. Assemble
        self.logger.info("🔧 [5/9] Assembling video...")
        video_path = self.video_gen.assemble(
            audio=audio_path, footage=footage, script=script,
            output_dir=CONFIG["OUTPUT_DIR"], add_captions=True, add_background_music=True,
        )
        self.logger.info(f"✅ Video: {video_path}")

        # 6. Thumbnail
        self.logger.info("🖼️  [6/9] Generating thumbnail...")
        thumb_path = self.thumbnail.create(title=script["title"])
        self.logger.info(f"✅ Thumbnail: {thumb_path}")

        # 7. SEO metadata
        self.logger.info("🏷️  [7/9] Building SEO metadata...")
        metadata = self.script_gen.generate_metadata(
            script=script, topic=topic,
            affiliate_links=self.affiliate.get_relevant_links(topic),
        )
        self.logger.info(f"✅ Title: {metadata['title']}")

        # 8. Upload
        self.logger.info("📤 [8/9] Uploading to YouTube...")
        video_id = self.uploader.upload(video_path=video_path, thumbnail_path=thumb_path, metadata=metadata)
        self.logger.info(f"✅ Live: youtube.com/shorts/{video_id}")

        # 9. Cross-post (best-effort — failure won't kill the pipeline)
        self.logger.info("📱 [9/9] Cross-posting...")
        try:
            results = self.cross_poster.post_all(
                video_path=video_path, caption=metadata["short_caption"],
                platforms=["tiktok", "instagram_reels"],
            )
            for platform, result in results.items():
                icon = "✅" if result["success"] else "⚠️"
                self.logger.info(f"  {icon} {platform}: {result.get('url', result.get('error', 'done'))}")
        except Exception as e:
            self.logger.warning(f"  Cross-post error (non-fatal): {e}")

        # Analytics + Telegram
        self.analytics.log_video({"date": datetime.now().isoformat(), "topic": topic["title"],
                                   "video_id": video_id, "niche": CONFIG["NICHE"], "estimated_duration": duration})
        self._notify(f"🎬 New Short!\n📽️ {script['title']}\n🔗 youtube.com/shorts/{video_id}")

        self.logger.info("=" * 60)
        self.logger.info(f"🎉 DONE — youtube.com/shorts/{video_id}")
        self.logger.info("=" * 60)
        return True

    def start_scheduler(self):
        for t in CONFIG["UPLOAD_TIMES"]:
            schedule.every().day.at(t).do(self.run_daily_pipeline)
            self.logger.info(f"  📅 Scheduled: {t}")
        self.logger.info("▶️  Running first video now...")
        self.run_daily_pipeline()
        while True:
            schedule.run_pending()
            time.sleep(60)

    def _notify(self, message: str):
        token, chat = CONFIG.get("TELEGRAM_BOT_TOKEN"), CONFIG.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            return
        try:
            import requests
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": message}, timeout=5)
        except Exception:
            pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI YouTube Shorts Revenue Machine v1.1",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="""
Examples:
  python main.py --dry-run              # Test only — generates script, NO upload
  python main.py --dry-run --topic "AI study hacks"
  python main.py --run-once             # Upload exactly 1 video and exit
  python main.py --run-once --count 3   # Upload 3 videos now
  python main.py --niche finance        # Override niche
  python main.py                        # Start 24/7 scheduler (3 videos/day)
""")
    parser.add_argument("--run-once", action="store_true")
    parser.add_argument("--dry-run",  action="store_true", help="Script only — no upload, no API costs beyond Claude")
    parser.add_argument("--topic",    type=str, help="Custom topic for dry-run")
    parser.add_argument("--batch-position", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument("--niche",    type=str)
    parser.add_argument("--count",    type=int, default=1)
    args = parser.parse_args()

    if args.niche and args.niche in NICHE_CONFIGS:
        CONFIG["NICHE"] = args.niche

    if not validate_config(dry_run=args.dry_run):
        sys.exit(1)

    machine = RevenueMachine(dry_run=args.dry_run)

    if args.dry_run:
        for i in range(args.count):
            print(f"\n📝 Dry run {i+1}/{args.count}...")
            machine.run_daily_pipeline(
                dry_run=True,
                topic_override=args.topic,
                batch_position=args.batch_position,
            )
    elif args.run_once:
        for i in range(args.count):
            print(f"\n🎬 Video {i+1}/{args.count}...")
            machine.run_daily_pipeline(dry_run=False)
    else:
        machine.start_scheduler()
