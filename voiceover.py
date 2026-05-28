"""
Voiceover Engine - Generates professional AI voiceovers using ElevenLabs.

Optimized for:
- Natural pacing for YouTube Shorts
- Multiple voice options for A/B testing
- Audio post-processing (normalization, de-essing)
- Smart retry logic with fallback to OpenAI TTS
"""

import os
import time
import logging
import hashlib
import requests
from pathlib import Path
from datetime import datetime

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False

logger = logging.getLogger("RevenueMachine.Voiceover")


# Best voices for different niches (ElevenLabs voice IDs)
VOICE_OPTIONS = {
    "ai_tools": {
        "primary": "21m00Tcm4TlvDq8ikWAM",   # Rachel - clear, professional
        "secondary": "AZnzlk1XvdvUeBnXmlld",  # Domi - energetic
        "fallback": "EXAVITQu4vr4xnSDxMaL",   # Bella - warm
    },
    "finance": {
        "primary": "VR6AewLTigWG4xSOukaG",    # Arnold - authoritative
        "secondary": "pNInz6obpgDQGcFmaJgB",  # Adam - trustworthy
        "fallback": "21m00Tcm4TlvDq8ikWAM",   # Rachel
    },
    "motivation": {
        "primary": "ODq5zmih8GrVes37Dizd",    # Patrick - inspiring
        "secondary": "VR6AewLTigWG4xSOukaG",  # Arnold
        "fallback": "21m00Tcm4TlvDq8ikWAM",   # Rachel
    },
    "mystery": {
        "primary": "pNInz6obpgDQGcFmaJgB",    # Adam - deep, steady narration
        "secondary": "VR6AewLTigWG4xSOukaG",  # Arnold - low, ominous
        "fallback": "21m00Tcm4TlvDq8ikWAM",   # Rachel
    },
}


class VoiceoverEngine:
    _elevenlabs_skip_logged = False
    _elevenlabs_restricted_for_session = False
    _elevenlabs_restricted_logged = False

    def __init__(self, config: dict):
        self.config = config
        self.api_key = str(config.get("ELEVENLABS_API_KEY", "")).strip()
        self.base_url = "https://api.elevenlabs.io/v1"
        self.niche = config.get("NICHE", "ai_tools")
        self.voice_provider = self._resolve_voice_provider(config)

        # Explicit ElevenLabs voice override; keeps legacy VOICE_ID support.
        self.elevenlabs_voice_id = str(
            config.get("ELEVENLABS_VOICE_ID", "") or os.getenv("ELEVENLABS_VOICE_ID", "")
        ).strip()
        self.voice_id_override = str(config.get("VOICE_ID", "") or os.getenv("VOICE_ID", "")).strip()

        # Voice settings for maximum naturalness
        self.voice_settings = {
            "stability": config.get("VOICE_STABILITY", 0.5),
            "similarity_boost": config.get("VOICE_SIMILARITY", 0.75),
            "style": 0.0,
            "use_speaker_boost": True,
        }

        # Fallback: OpenAI TTS
        self.openai_api_key = str(config.get("OPENAI_API_KEY", "")).strip()

        logger.info(
            f"🎙️  Voiceover engine ready | Provider: {self.voice_provider} | Primary voice: {self._get_voice_id()}"
        )

    def generate(self, text: str, output_dir: Path, voice_id: str = None) -> Path:
        """
        Generates voiceover for the given text.
        Returns path to audio file.
        """
        if not voice_id:
            voice_id = self._get_voice_id()

        # Create cache key to avoid regenerating same audio
        cache_key = hashlib.md5(f"{text}{voice_id}".encode()).hexdigest()[:12]
        output_path = output_dir / f"voice_{cache_key}.mp3"

        if output_path.exists():
            logger.info(f"  📦 Using cached audio: {output_path.name}")
            return output_path

        for provider in self._provider_order():
            if provider == "elevenlabs":
                if self._should_skip_elevenlabs():
                    continue
                try:
                    result = self._elevenlabs_tts(text, voice_id, output_path)
                    if result:
                        return result
                except Exception as e:
                    logger.warning(f"ElevenLabs failed: {e}. Falling back...")
                continue

            if provider == "openai":
                if not self.openai_api_key:
                    continue
                try:
                    result = self._openai_tts(text, output_path)
                    if result:
                        return result
                except Exception as e:
                    logger.error(f"OpenAI TTS failed: {e}")
                continue

            if provider == "local":
                # Keep local synthesis delegated to render_batch.py fallback paths.
                raise RuntimeError("Local TTS fallback required")

        raise RuntimeError("All configured voiceover providers failed")

    def _elevenlabs_tts(self, text: str, voice_id: str, output_path: Path) -> Path:
        """Generate audio using ElevenLabs API."""
        url = f"{self.base_url}/text-to-speech/{voice_id}"

        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": self.api_key,
        }

        payload = {
            "text": self._preprocess_text(text),
            "model_id": "eleven_turbo_v2_5",  # Fastest model, good quality
            "voice_settings": self.voice_settings,
        }

        # Retry logic
        for attempt in range(3):
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=30)

                if response.status_code == 200:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(response.content)

                    # Post-process audio
                    if PYDUB_AVAILABLE:
                        self._post_process_audio(output_path)

                    file_size = output_path.stat().st_size / 1024
                    logger.info(f"  ✅ ElevenLabs audio: {output_path.name} ({file_size:.0f}KB)")
                    return output_path

                elif response.status_code == 429:
                    wait_time = (attempt + 1) * 10
                    logger.warning(f"  Rate limited. Waiting {wait_time}s...")
                    time.sleep(wait_time)

                else:
                    if self._is_elevenlabs_restriction_error(response.status_code, response.text):
                        VoiceoverEngine._elevenlabs_restricted_for_session = True
                        if not VoiceoverEngine._elevenlabs_restricted_logged:
                            logger.warning(
                                "VOICEOVER FALLBACK: ElevenLabs returned HTTP %s (%s). A free-tier "
                                "ElevenLabs key cannot synthesize library/premade voices via the API - "
                                "this render uses OpenAI TTS instead. For ElevenLabs narration, upgrade "
                                "the ElevenLabs plan to a paid tier.",
                                response.status_code,
                                response.text[:120].replace("\n", " "),
                            )
                            VoiceoverEngine._elevenlabs_restricted_logged = True
                        return None
                    logger.error(f"  ElevenLabs error {response.status_code}: {response.text[:200]}")
                    break

            except requests.Timeout:
                logger.warning(f"  Timeout on attempt {attempt + 1}")
                time.sleep(5)

        return None

    def _openai_tts(self, text: str, output_path: Path) -> Path:
        """Fallback: Generate audio using OpenAI TTS."""
        import openai

        client = openai.OpenAI(api_key=self.openai_api_key)

        # OpenAI voices: alloy, echo, fable, onyx, nova, shimmer
        niche_voice_map = {
            "ai_tools": "nova",       # Clear, modern
            "finance": "onyx",        # Deep, authoritative
            "motivation": "fable",    # Warm, engaging
            "mystery": "onyx",        # Deep, suspenseful narration
        }
        voice = niche_voice_map.get(self.niche, "nova")

        response = client.audio.speech.create(
            model="tts-1-hd",  # HD model - noticeably less robotic for narration
            voice=voice,
            input=self._preprocess_text(text),
            speed=1.0,
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        response.stream_to_file(str(output_path))

        file_size = output_path.stat().st_size / 1024
        logger.info(f"  ✅ OpenAI audio: {output_path.name} ({file_size:.0f}KB)")
        return output_path

    def _preprocess_text(self, text: str) -> str:
        """
        Optimizes text for TTS:
        - Adds pauses at sentence boundaries
        - Expands abbreviations
        - Handles numbers and special chars
        """
        # Add slight pause after hook (after first sentence)
        sentences = text.split(". ")
        if len(sentences) > 1:
            # Add SSML-style break after hook
            sentences[0] = sentences[0] + "... "
            text = ". ".join(sentences)

        # Common TTS improvements
        replacements = {
            "AI": "A.I.",
            "GPT": "G.P.T.",
            "URL": "U.R.L.",
            "API": "A.P.I.",
            "%": " percent",
            "&": " and",
            "w/": "with",
            "#": "number",
            "vs": "versus",
            "n8n": "N 8 N",
            "ChatGPT": "Chat G.P.T.",
            "$": "dollar",
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

        return text.strip()

    def _post_process_audio(self, audio_path: Path):
        """
        Post-processes audio for better quality:
        - Normalize volume to -14 LUFS (YouTube standard)
        - Remove silence at beginning/end
        """
        try:
            audio = AudioSegment.from_mp3(str(audio_path))

            # Normalize
            target_dBFS = -14
            change_in_dBFS = target_dBFS - audio.dBFS
            normalized = audio.apply_gain(change_in_dBFS)

            # Strip leading/trailing silence
            silence_threshold = -50
            chunk_size = 10
            start_trim = self._detect_leading_silence(normalized, silence_threshold, chunk_size)
            end_trim = self._detect_leading_silence(normalized.reverse(), silence_threshold, chunk_size)
            trimmed = normalized[start_trim:len(normalized) - end_trim]

            # Export back
            trimmed.export(str(audio_path), format="mp3", bitrate="192k")
        except Exception as e:
            logger.warning(f"Audio post-processing failed (non-critical): {e}")

    @staticmethod
    def _detect_leading_silence(sound, silence_threshold=-50.0, chunk_size=10):
        trim_ms = 0
        while sound[trim_ms:trim_ms + chunk_size].dBFS < silence_threshold:
            trim_ms += chunk_size
            if trim_ms >= len(sound):
                return 0
        return trim_ms

    def _get_voice_id(self) -> str:
        """Gets the best voice ID for the current niche."""
        voices = VOICE_OPTIONS.get(self.niche, VOICE_OPTIONS["ai_tools"])
        if self.elevenlabs_voice_id:
            return self.elevenlabs_voice_id
        if self.voice_id_override:
            return self.voice_id_override
        return voices["primary"]

    def _provider_order(self) -> list[str]:
        if self.voice_provider == "elevenlabs":
            return ["elevenlabs", "openai", "local"]
        if self.voice_provider == "openai":
            return ["openai", "local"]
        if self.voice_provider == "local":
            return ["local"]

        order: list[str] = []
        if self.api_key:
            order.append("elevenlabs")
        if self.openai_api_key:
            order.append("openai")
        order.append("local")
        return order

    def _resolve_voice_provider(self, config: dict) -> str:
        provider = str(config.get("VOICE_PROVIDER", "") or os.getenv("VOICE_PROVIDER", "")).strip().lower()
        if not provider:
            return "auto"

        allowed = {"auto", "elevenlabs", "openai", "local"}
        if provider not in allowed:
            logger.warning(f"Invalid VOICE_PROVIDER='{provider}', defaulting to auto")
            return "auto"
        return provider

    def _should_skip_elevenlabs(self) -> bool:
        if VoiceoverEngine._elevenlabs_restricted_for_session:
            if not VoiceoverEngine._elevenlabs_restricted_logged:
                logger.warning(
                    "ElevenLabs disabled for this render session due to plan/voice restriction; using fallback providers."
                )
                VoiceoverEngine._elevenlabs_restricted_logged = True
            return True

        if self.api_key:
            return False

        if not VoiceoverEngine._elevenlabs_skip_logged:
            logger.warning("ElevenLabs skipped (no API key set)")
            VoiceoverEngine._elevenlabs_skip_logged = True
        return True

    @staticmethod
    def _is_elevenlabs_restriction_error(status_code: int, response_text: str) -> bool:
        if status_code == 402:
            return True

        body = (response_text or "").lower()
        restriction_markers = [
            "paid_plan_required",
            "library voice",
            "voice not allowed",
            "subscription",
            "plan restriction",
            "plan required",
        ]
        return any(marker in body for marker in restriction_markers)

    def list_available_voices(self) -> list:
        """Lists all available ElevenLabs voices (for setup/testing)."""
        if not self.api_key:
            return []

        try:
            resp = requests.get(
                f"{self.base_url}/voices",
                headers={"xi-api-key": self.api_key},
                timeout=10,
            )
            return resp.json().get("voices", [])
        except Exception as e:
            logger.error(f"Failed to list voices: {e}")
            return []
