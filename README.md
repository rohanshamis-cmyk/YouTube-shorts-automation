# YouTube Shorts Automation Pipeline

An end-to-end Python pipeline for generating, rendering, and uploading AI-produced YouTube Shorts. Takes a topic as input and outputs a fully rendered MP4 with voiceover, captions, and background imagery — no manual editing required.

## Features

- **Script generation** — Claude, OpenAI, or OpenRouter with fallback scripts and arbitration (live vs fallback scoring before publish)
- **AI voiceover** — ElevenLabs primary with OpenAI TTS fallback; normalized audio (-14 LUFS), silence-trimmed
- **AI image generation** — DALL-E 3 with narration-grounded prompts (each shot references the actual script line)
- **Video rendering** — FFmpeg + PIL pipeline with word-by-word karaoke captions, background footage, and gapless audio
- **Offline-safe mode** — full pipeline runs without any API calls for testing and development
- **Batch generation** — run multiple topics in one command; retry logic handles API failures
- **YouTube upload** — OAuth-authenticated upload with scheduling support
- **Provider health checks** — `provider_doctor.py` shows exactly which APIs are configured and working

## Two Pipelines

### Story Pipeline (`run_story_pipeline.py`)
Horror/suspense narrative Shorts. Generates a suspense story script, narrated over background footage with word-by-word karaoke captions. Includes an arbitration system that compares live AI scripts against high-quality fallback scripts and picks the winner before rendering.

### Pivot Pipeline (`pivot/pipeline/`)
Psychology and behavior-focused Shorts (e.g. "Why you feel exhausted after talking to certain people"). Cleaner, newer architecture. Audio-first render: script → narration → images → assembled MP4.

## Tech Stack

| Layer | Technology |
|---|---|
| Script generation | Anthropic Claude, OpenAI GPT-4o, OpenRouter |
| Voiceover | ElevenLabs, OpenAI TTS |
| Image generation | OpenAI DALL-E 3 |
| Video rendering | FFmpeg, Pillow (PIL) |
| Video generation | Runway ML (optional, experimental) |
| Upload | YouTube Data API v3 |
| Background footage | Pexels API |

## Requirements

- Python 3.11+
- FFmpeg and ffprobe on PATH (`brew install ffmpeg` on macOS)
- API keys for the providers you want to use (see `.env.example`)

## Setup

```bash
# 1. Clone and create a virtual environment
git clone https://github.com/rohanshamis-cmyk/YouTube-shorts-automation.git
cd YouTube-shorts-automation
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY or OPENAI_API_KEY,
# and ELEVENLABS_API_KEY for voiceover.

# 4. Verify providers
python3 provider_doctor.py
```

## Running

### Story pipeline (single topic)
```bash
# Offline-safe mode — no API calls, uses fallback script
python3 run_story_pipeline.py "the mirror in the stairwell" --media-mode offline_safe

# Full pipeline with live AI
python3 run_story_pipeline.py "what happened after the lights went out"
```

### Pivot pipeline (psychology Shorts)
```bash
# Smoke test (single topic)
python3 -m pivot.pipeline.smoke

# Batch (5 topics)
python3 -m pivot.pipeline.batch
```

### Story batch
```bash
python3 run_story_batch.py --topics story_topics.txt
```

### Shell workflow (batch → render → upload)
```bash
# Generate scripts for 3 videos
./run_batch.sh 3 "AI productivity tools"

# Render the batch
./render_latest_batch.sh

# Upload (dry run by default — add --execute to actually upload)
./upload_latest_batch.sh --execute
```

## Environment Variables

Copy `.env.example` to `.env` and fill in your keys. Minimum configuration:

```
ANTHROPIC_API_KEY=   # or OPENAI_API_KEY — required for script generation
ELEVENLABS_API_KEY=  # required for ElevenLabs voiceover
OPENAI_API_KEY=      # required for DALL-E image generation
```

See `.env.example` for all available options including Runway, Pexels, YouTube upload, and Reddit topic discovery.

## Folder Structure

```
.
├── run_story_pipeline.py     # Main story pipeline entrypoint
├── run_story_batch.py        # Batch runner for story pipeline
├── script_generator.py       # Script generation + fallback arbitration
├── render_batch.py           # FFmpeg/PIL video renderer
├── voiceover.py              # ElevenLabs + OpenAI TTS voiceover engine
├── uploader.py               # YouTube upload via OAuth
├── provider_doctor.py        # API provider health check
├── provider_probe.py         # Live API connectivity test
├── benchmark_writer_models.py # Compare AI writer models against fallback
├── main.py                   # Full-pipeline orchestrator (topic → script → voiceover → video → upload)
├── topic_finder.py           # Topic discovery engine (Reddit, Google Trends, RSS, YouTube API)
├── topic_fetcher.py          # AI-news niche config and entry point for the dialogue pipeline
├── dialogue_writer.py        # Two-host dialogue script writer for faceless AI-news Shorts
├── cross_poster.py           # TikTok and Instagram Reels upload client
│
├── pivot/                    # Psychology Shorts pipeline
│   ├── pipeline/             # Core modules (script, tts, images, render, batch)
│   ├── data/                 # Competitor research and topic candidates
│   ├── design/               # Pipeline design specs
│   └── strategy/             # Format and channel strategy docs
│
├── image_providers/          # DALL-E and hybrid image generation
├── video_providers/          # Runway ML adapter
├── video_planning/           # Shot-by-shot planning from scripts
├── asset_bridge/             # Runway image-to-video bridge
│
├── assets/
│   ├── backgrounds/          # Background footage (.mp4 files, gitignored)
│   └── music/                # Ambient music tracks (.mp3 files, gitignored)
│
├── .env.example              # Environment variable template
└── requirements.txt          # Python dependencies
```

## Notes

- `offline_safe` mode always produces output without any API calls — use it for development and testing
- Generated videos land in `generated/` (gitignored)
- The arbitration system in `script_generator.py` compares live AI output against hand-tuned fallback scripts and only uses the live version if it wins on anchor preservation and quality scoring
- `benchmark_writer_models.py` runs all configured writer models against test topics and produces a comparison report

## License

MIT
