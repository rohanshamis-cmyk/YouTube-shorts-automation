"""Build a cinematic shot pack from a generated script."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from video_providers.runway_adapter import ShotSpec


DEFAULT_PROMPT_IMAGE_URL = "https://images.unsplash.com/photo-1477346611705-65d1883cee1e"


@dataclass(slots=True)
class PlannedShot:
    shot_id: str
    beat: str
    visual_goal: str
    prompt_text: str
    prompt_image: str
    on_screen_text: str
    duration: int
    ratio: str
    metadata: dict[str, Any] | None = None


class ShotPackBuilder:
    """Converts one script into a 6-8 shot faceless mystery pack."""

    _BASE_BEATS = [
        "hook_image",
        "setup",
        "strange_detail",
        "escalation",
        "strongest_reveal",
        "consequence_ending",
    ]
    _EXTRA_BEATS = ["aftershock", "final_echo"]

    _CAMERA_MOVES = [
        "slow push-in",
        "subtle handheld drift",
        "locked frame with slight rack focus",
        "lateral glide past foreground objects",
        "tight close-up with micro shake",
        "over-the-shoulder creep forward",
        "low-angle dolly in",
        "slow pull-back revealing more space",
    ]
    _LIGHTING = [
        "cold overhead practical light",
        "flickering tungsten spill",
        "dim corridor light with deep shadows",
        "single side light cutting the frame",
        "streetlight leakage through blinds",
        "weak emergency glow",
        "mixed warm-cool contrast",
        "moonlit ambient darkness",
    ]
    _FRAMING_CUES = [
        "tight frame near the doorway threshold",
        "medium frame that keeps the corridor depth visible",
        "close insert on the key object",
        "off-center frame with negative space behind the subject",
        "over-shoulder frame toward the tension source",
        "wider frame that shows exit routes and distance",
        "foreground-obstructed frame with shallow depth",
        "static frame that holds the aftermath",
    ]
    _BEAT_FRAMING = {
        "hook_image": [
            "tight frame at the door seam where the note first appears",
            "iconic off-center frame with the threshold dominating the foreground",
        ],
        "setup": [
            "grounded medium frame that establishes the doorway and floor space",
            "wide baseline frame with clear distance between the subject and the door",
        ],
        "strange_detail": [
            "close insert that isolates the off detail near the shoe line",
            "foreground-obstructed frame that makes the wrong detail feel intrusive",
        ],
        "escalation": [
            "tight shoulder-level frame closing in on hands and note",
            "tight frame around the immediate action line",
        ],
        "strongest_reveal": [
            "sharp readable close-up on the note text line",
            "locked close frame that keeps the reveal line legible",
        ],
        "consequence_ending": [
            "mid-wide frame with the doorway, phone hand, and note in one plane",
            "pull-back frame showing the retreat from the doorway with the note still in view",
        ],
        "aftershock": [
            "static aftermath frame with empty negative space where movement just happened",
            "wide hold that lingers on what was left behind",
        ],
        "final_echo": [
            "quiet final frame with distance and unresolved foreground detail",
            "held long frame that leaves one unresolved object in focus",
        ],
    }
    _BEAT_LIGHTING = {
        "hook_image": [
            "cold practical light with a hard edge across the threshold",
            "dim hallway spill with a bright strip under the door",
        ],
        "setup": [
            "flat apartment practicals that still feel normal",
            "soft mixed interior light with no obvious contrast yet",
        ],
        "strange_detail": [
            "uneven corridor light that leaves the odd detail sharply visible",
            "muted practical light with a single harsh highlight on the object",
        ],
        "escalation": [
            "tight side light that deepens shadows around the hands",
            "low practical spill that keeps only the active area visible",
        ],
        "strongest_reveal": [
            "clean side key light so the reveal text reads clearly",
            "focused edge light on the unfolded note with the background dropping away",
        ],
        "consequence_ending": [
            "cold low light with the phone screen adding a small hard glow",
            "dim practical spill with the doorway in shadow and the note still readable",
        ],
        "aftershock": [
            "lingering ambient low light with no motion cues",
            "quiet moonlit spill that keeps the scene drained and still",
        ],
        "final_echo": [
            "weak practical afterlight with long shadows",
            "minimal ambient spill that keeps the final detail visible",
        ],
    }
    _BEAT_CAMERA = {
        "hook_image": ["slow push-in", "subtle lateral creep"],
        "setup": ["locked-off hold", "restrained tripod drift"],
        "strange_detail": ["micro push toward the object", "small rack-focus settle"],
        "escalation": ["slow move inward on the hands", "tight over-shoulder creep"],
        "strongest_reveal": ["hold steady for readability", "minimal forward nudge only"],
        "consequence_ending": ["slow pull-back", "held frame with slight backward drift"],
        "aftershock": ["static hold", "barely perceptible drift"],
        "final_echo": ["static hold", "slow pull-back revealing empty space"],
    }
    _SCENE_BEAT_FRAMING = {
        "room_blackout": {
            "hook_image": [
                "off-center room frame with people silhouettes and dead fixtures",
                "wide interior frame locking the dark room geometry",
            ],
            "setup": [
                "grounded room frame that keeps people positions readable",
                "medium-wide frame across the room with clear spacing",
            ],
            "strange_detail": [
                "tight shoulder-level insert where contact happens in the dark",
                "close room insert that isolates the off movement",
            ],
            "escalation": [
                "compressed room frame as positions stop matching",
                "tight room frame centered on shifting positions",
            ],
            "strongest_reveal": [
                "sharp readable frame isolating the wrong position in the room",
                "locked frame on the reveal position against the room baseline",
            ],
            "consequence_ending": [
                "mid-wide frame with the exit route and room edge in one plane",
                "pull-back room frame that holds the exit sign and retreat path",
            ],
            "aftershock": [
                "static room frame with empty negative space where someone stood",
                "wide hold on the room layout after the movement stops",
            ],
            "final_echo": [
                "quiet room hold with one unresolved silhouette",
                "held final room frame with distance and no movement",
            ],
        },
        "hallway_mirror": {
            "hook_image": [
                "tight frame on the hallway mirror and near wall edge",
                "iconic frame with mirror reflection and doorframe in one line",
            ],
            "setup": [
                "grounded hallway frame with mirror depth and doorframe",
                "medium hallway frame that keeps reflection alignment readable",
            ],
            "strange_detail": [
                "close insert on the mirror edge where motion appears",
                "foreground-obstructed hallway frame around the mirror line",
            ],
            "escalation": [
                "tight mirror-line frame while the subject holds position",
                "shoulder-height hallway frame focused on reflection behavior",
            ],
        },
        "elevator_lobby": {
            "hook_image": [
                "tight frame on elevator doors and floor indicator mismatch",
                "iconic frame on the elevator panel beside the opening doors",
            ],
            "setup": [
                "grounded frame inside the elevator cab with floor panel readable",
                "medium frame from the elevator threshold into the wrong floor lobby",
            ],
            "strange_detail": [
                "close insert on floor numbers that do not match the panel",
                "tight frame on empty lobby while elevator indicator shows a different floor",
            ],
            "escalation": [
                "tight frame on doors cycling open and shut at the same wrong floor",
                "shoulder-height frame on call button and floor panel while the cab idles",
            ],
            "strongest_reveal": [
                "sharp close-up on the call button lighting before anyone appears",
                "locked close frame on the lobby panel as the wrong floor repeats",
            ],
            "consequence_ending": [
                "mid-wide frame from inside the cab as it rides down to the lobby",
                "pull-back frame on elevator interior during a direct ride to lobby level",
            ],
            "aftershock": [
                "static frame on closed elevator doors at lobby level after exit",
                "held lobby frame with elevator indicator still unsettled",
            ],
            "final_echo": [
                "quiet hold on the elevator panel and empty lobby",
                "long frame on elevator doors after the final stop",
            ],
        },
        "door_confession": {
            "hook_image": [
                "tight frame on the closed door and latch at ear level",
                "off-center frame on the door seam where the whisper sits",
            ],
            "setup": [
                "grounded frame outside the door with latch and handle visible",
                "medium frame on the threshold stance and closed door face",
            ],
            "strange_detail": [
                "close insert on the latch while the repeated line lands",
                "tight frame on the door seam with no visible speaker",
            ],
            "escalation": [
                "tight frame on the latch and doorframe during the repeated line",
                "close shoulder-height frame outside the closed door",
            ],
            "consequence_ending": [
                "pull-away frame from the door toward the stairs",
                "mid-wide frame with door behind and escape path ahead",
            ],
        },
        "note_threshold": {
            "escalation": [
                "tight shoulder-level frame closing in on hands and note",
                "close frame on the note held at threshold height",
            ],
        },
    }
    _SCENE_BEAT_LIGHTING = {
        "room_blackout": {
            "hook_image": [
                "lights fully dropped with only exit sign spill",
                "hard blackout with weak emergency glow across silhouettes",
            ],
            "setup": [
                "low room spill with power flicker traces",
                "flat post-blackout lighting with faces mostly in shadow",
            ],
            "strange_detail": [
                "short blackout pulse with a single edge highlight",
                "dim emergency spill isolating the contact point",
            ],
            "escalation": [
                "uneven post-blackout light that makes positions hard to trust",
                "tight emergency spill over shifting silhouettes",
            ],
            "consequence_ending": [
                "cold low room light with exit sign glow as direction cue",
                "dim emergency spill with the exit sign readable in frame",
            ],
        },
        "hallway_mirror": {
            "escalation": [
                "low hallway practicals with reflection contrast in the mirror",
                "narrow overhead spill that keeps mirror movement visible",
            ],
        },
        "elevator_lobby": {
            "hook_image": [
                "cold fluorescent elevator light with hard panel highlights",
                "sterile lobby spill with sharp reflections on elevator doors",
            ],
            "setup": [
                "flat cab lighting with readable floor indicator glow",
                "even lobby practicals with slight green fluorescent cast",
            ],
            "strange_detail": [
                "narrow fluorescent spill isolating floor numbers and call button",
                "cold practical light with deep shadow in the empty lobby edges",
            ],
            "escalation": [
                "harsh panel glow while elevator doors cycle repeatedly",
                "low ambient lobby light with indicator blink as the only pulse",
            ],
            "strongest_reveal": [
                "focused side spill on call button as it lights first",
                "clean practical light on panel details with background dropped",
            ],
            "consequence_ending": [
                "steady cab light during the ride down to lobby",
                "cold lobby practicals with doors opening to streetward exit path",
            ],
        },
        "door_confession": {
            "escalation": [
                "low side spill across the latch and door seam",
                "dim practical light with a hard highlight on the latch",
            ],
        },
    }
    _OBJECT_PATTERNS = {
        "note": r"\bnote\b",
        "door": r"\bdoor\b|\bdoorframe\b",
        "shoe": r"\bshoe\b",
        "phone": r"\bphone\b",
        "glass": r"\bglass\b",
        "hallway": r"\bhallway\b|\bcorridor\b",
        "mirror": r"\bmirror\b|\breflection\b",
        "handle": r"\bhandle\b|\bknob\b|\blatch\b",
        "elevator": r"\belevator\b|\bcab\b",
        "floor": r"\bwrong floor\b|\bmy floor\b|\bfloor number\b|\bfloor indicator\b|\bfloor panel\b|\bindicator\b",
        "lobby": r"\blobby\b",
        "stairwell": r"\bstairwell\b|\bstairs\b|\blanding\b",
        "exit_sign": r"\bexit sign\b|\bexit arrow\b|\barrow\b",
        "apartment": r"\bapartment\b|\bapartment door\b",
        "footsteps": r"\bfootsteps?\b",
        "call_button": r"\bcall button\b",
        "doormat": r"\bdoormat\b",
        "table": r"\btable\b",
        "chair": r"\bchair\b",
        "corner": r"\bcorner\b",
        "bag": r"\bbag\b",
        "name": r"\bname\b|\bnickname\b",
    }
    _ACTION_PATTERNS = {
        "scraped": r"\bscrap(?:e|ed|ing)\b",
        "stopped": r"\bstop(?:ped|ping|s)\b",
        "picked_up": r"\bpick(?:ed)?\s+up\b",
        "whispered": r"\bwhisper(?:ed|ing|s)\b",
        "called": r"\bcall(?:ed|ing|s)\b",
        "backed_away": r"\bback(?:ed)?\s+away\b|\bbacked\b",
        "turned": r"\bturn(?:ed|ing|s)\b",
        "cracked": r"\bcrack(?:ed|ing|s)\b",
        "facing": r"\bfac(?:e|ing|ed)\b",
        "left": r"\bleft\b|\bleave\b",
    }
    _NEUTRAL_GUARD_ACTIONS = {
        "turned",
        "facing",
    }
    _REVEAL_MARKERS = [
        "only one person",
        "nickname",
        "wrong person",
        "every face",
        "everyone",
        "same corner",
        "behind my chair",
        "from the hallway side",
        "no one touched",
    ]
    _CONSEQUENCE_MARKERS = [
        "called",
        "left",
        "walked outside",
        "before sunrise",
        "backed away",
        "security",
        "ran",
        "dropped",
    ]
    _BEAT_GOALS = {
        "hook_image": "Establish the unsettling core image immediately.",
        "setup": "Ground the setting and normal baseline before the shift.",
        "strange_detail": "Show the first off detail that should not be there.",
        "escalation": "Increase pressure with motion or proximity.",
        "strongest_reveal": "Deliver the clearest irreversible reveal.",
        "consequence_ending": "Land on consequence, decision cost, or hard final image.",
        "aftershock": "Show immediate aftermath and emotional recoil.",
        "final_echo": "Leave a lingering visual question in one final beat.",
    }
    _STOPWORDS = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "with",
        "that",
        "this",
        "it",
        "was",
        "were",
        "is",
        "are",
        "be",
        "been",
        "i",
        "you",
        "my",
        "we",
        "they",
        "he",
        "she",
        "from",
        "after",
        "then",
        "when",
    }

    def __init__(
        self,
        ratio: str = "720:1280",
        duration: int = 4,
        default_prompt_image: str | None = None,
    ):
        self.ratio = ratio or "720:1280"
        self.duration = duration if duration else 4
        self.default_prompt_image = default_prompt_image or DEFAULT_PROMPT_IMAGE_URL
        self._validate_defaults()

    def build_from_script(self, script: dict[str, Any]) -> list[PlannedShot]:
        title, hook, narration = self._validate_script(script)
        body = self._strip_hook_prefix(hook=hook, narration=narration)
        body_sentences = self._split_sentences(body)
        if not body_sentences:
            body_sentences = self._split_sentences(narration)
        sentence_corpus = self._split_sentences(f"{hook} {narration}")
        hook = self._normalize_source_text(hook, sentence_corpus=sentence_corpus)
        body_sentences = [
            self._normalize_source_text(sentence, sentence_corpus=sentence_corpus)
            for sentence in body_sentences
            if str(sentence or "").strip()
        ]

        story_details = self.extract_story_details(f"{hook} {narration}")
        scene_profile = self._extract_scene_profile(
            title=title,
            hook=hook,
            narration=narration,
            story_details=story_details,
        )
        shot_count = self._target_shot_count(body_sentences)
        beats = self._beat_sequence(shot_count)
        full_story_text = f"{hook} {narration}"
        beat_lines = self._line_map_for_beats(
            title=title,
            hook=hook,
            body_sentences=body_sentences,
            beats=beats,
            story_details=story_details,
            scene_profile=scene_profile,
        )
        scene_anchor = str(scene_profile.get("anchor") or self._infer_scene_anchor(title=title, hook=hook, narration=narration))

        planned: list[PlannedShot] = []
        seen_beat_names: set[str] = set()
        seen_prompts: set[str] = set()

        for idx, beat in enumerate(beats):
            source_text = self._normalize_source_text(
                str(beat_lines.get(beat, hook) or hook).strip(),
                sentence_corpus=sentence_corpus,
            )
            visual_goal = self._build_visual_goal(
                beat=beat,
                beat_text=source_text,
                story_details=story_details,
                scene_profile=scene_profile,
            )
            prompt_text = self._build_prompt_text(
                beat=beat,
                beat_text=source_text,
                scene_anchor=scene_anchor,
                shot_index=idx,
                story_details=story_details,
                scene_profile=scene_profile,
            )
            prompt_text = self._ensure_unique_prompt(prompt_text, seen_prompts, idx)
            on_screen_text = self._build_on_screen_text(
                beat=beat,
                beat_text=source_text,
                shot_index=idx,
                story_details=story_details,
            )
            visual_goal, on_screen_text, prompt_text = self._enforce_shot_semantic_consistency(
                beat=beat,
                source_text=source_text,
                visual_goal=visual_goal,
                on_screen_text=on_screen_text,
                prompt_text=prompt_text,
                story_details=story_details,
                script_text=full_story_text,
                scene_anchor=scene_anchor,
                shot_index=idx,
                scene_profile=scene_profile,
            )
            previous_caption = planned[-1].on_screen_text if planned else ""
            on_screen_text = self._ensure_adjacent_caption_uniqueness(
                beat=beat,
                beat_text=source_text,
                shot_index=idx,
                story_details=story_details,
                caption=on_screen_text,
                previous_caption=previous_caption,
            )
            shot_id = f"shot_{idx + 1:02d}"

            if beat in seen_beat_names:
                raise ValueError(f"Duplicate beat detected: {beat}")
            seen_beat_names.add(beat)

            planned.append(
                PlannedShot(
                    shot_id=shot_id,
                    beat=beat,
                    visual_goal=visual_goal,
                    prompt_text=prompt_text,
                    prompt_image=self.default_prompt_image,
                    on_screen_text=on_screen_text,
                    duration=self.duration,
                    ratio=self.ratio,
                    metadata={
                        "title": title,
                        "beat_index": idx + 1,
                        "tone": str(script.get("tone", "dark_curiosity")),
                        "niche": str(script.get("niche", "mystery")),
                        "source_text": source_text,
                        "source_beat_role": beat,
                        "story_objects": story_details.get("objects", []),
                        "story_actions": story_details.get("actions", []),
                        "story_reveals": story_details.get("reveal_phrases", []),
                        "scene_profile": scene_profile.get("kind", ""),
                    },
                )
            )

        return planned

    def to_runway_shots(self, planned_shots: list[PlannedShot]) -> list[ShotSpec]:
        runway_shots: list[ShotSpec] = []
        seen_prompts: set[str] = set()
        for shot in planned_shots:
            if shot.prompt_text in seen_prompts:
                raise ValueError(f"Duplicate prompt_text found in planned shots: {shot.shot_id}")
            seen_prompts.add(shot.prompt_text)

            metadata = dict(shot.metadata or {})
            metadata["beat"] = shot.beat
            metadata["visual_goal"] = shot.visual_goal
            metadata["on_screen_text"] = shot.on_screen_text

            runway_shots.append(
                ShotSpec(
                    shot_id=shot.shot_id,
                    prompt_text=shot.prompt_text,
                    prompt_image=shot.prompt_image,
                    ratio=shot.ratio,
                    duration=shot.duration,
                    metadata=metadata,
                )
            )
        return runway_shots

    def _validate_defaults(self) -> None:
        if not re.match(r"^\d+:\d+$", self.ratio):
            raise ValueError("ratio must be in W:H format, for example 720:1280.")
        if not isinstance(self.duration, int) or isinstance(self.duration, bool):
            raise ValueError("duration must be an integer.")
        if self.duration <= 0:
            raise ValueError("duration must be positive.")
        if not isinstance(self.default_prompt_image, str) or not self.default_prompt_image.startswith("https://"):
            raise ValueError("default_prompt_image must be an https:// URL.")

    def _validate_script(self, script: dict[str, Any]) -> tuple[str, str, str]:
        if not isinstance(script, dict):
            raise ValueError("script must be a dict.")

        title = str(script.get("title", "")).strip()
        hook = str(script.get("hook", "")).strip()
        narration = str(script.get("narration", "")).strip()

        if not title:
            raise ValueError("script.title is required.")
        if not hook:
            raise ValueError("script.hook is required.")
        if not narration:
            raise ValueError("script.narration is required.")

        return title, hook, narration

    def _strip_hook_prefix(self, hook: str, narration: str) -> str:
        if narration.lower().startswith(hook.lower()):
            return narration[len(hook):].strip()
        return narration

    def _split_sentences(self, text: str) -> list[str]:
        raw = [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(text or "").strip()) if s.strip()]
        if raw:
            return raw
        return [s.strip() for s in re.split(r"[,\n;]+", str(text or "").strip()) if s.strip()]

    def _normalize_source_text(self, source_text: str, sentence_corpus: list[str] | None = None) -> str:
        text = re.sub(r"\s+", " ", str(source_text or "")).strip()
        text = re.sub(r"\s+([,;:.!?])", r"\1", text)
        text = text.strip(" ,;")
        if not text:
            return ""

        corpus = sentence_corpus or []
        repaired = self._repair_source_from_corpus(text=text, sentence_corpus=corpus)
        if repaired:
            text = repaired

        if self._has_incomplete_tail(text):
            # Prefer restoring the full sentence from narration/script corpus before clipping.
            repaired_tail = self._repair_source_from_corpus(
                text=text,
                sentence_corpus=corpus,
                prefer_overlap=True,
            )
            if repaired_tail:
                text = repaired_tail

        if self._has_incomplete_tail(text):
            words = re.findall(r"[A-Za-z0-9']+", text)
            if words:
                repaired_words = words[:]
                while self._has_incomplete_tail_words(repaired_words) and len(repaired_words) > 3:
                    repaired_words.pop()
                text = " ".join(repaired_words)

        if text and text[-1] not in ".!?":
            text = f"{text}."
        return text

    def _repair_source_from_corpus(self, text: str, sentence_corpus: list[str], prefer_overlap: bool = False) -> str:
        candidate_words = re.findall(r"[A-Za-z0-9']+", text.lower())
        if not candidate_words:
            return ""

        best_match = ""
        best_overlap = 0
        for sentence in sentence_corpus:
            sentence_clean = re.sub(r"\s+", " ", str(sentence or "")).strip()
            sentence_words = re.findall(r"[A-Za-z0-9']+", sentence_clean.lower())
            if len(sentence_words) < len(candidate_words):
                continue

            prefix_overlap = 0
            for idx in range(min(len(candidate_words), len(sentence_words))):
                if candidate_words[idx] != sentence_words[idx]:
                    break
                prefix_overlap += 1

                if prefix_overlap >= max(5, len(candidate_words) - 1):
                    if prefix_overlap > best_overlap or len(sentence_words) > len(re.findall(r"[A-Za-z0-9']+", best_match.lower())):
                        best_match = sentence_clean
                        best_overlap = prefix_overlap

        if best_match:
            return best_match

        # Fallback for clipped/paraphrased source lines: recover the closest grounded sentence.
        if not prefer_overlap:
            return ""
        candidate_tokens = self._meaningful_tokens(text)
        if not candidate_tokens:
            return ""

        best_overlap_match = ""
        best_score = 0.0
        for sentence in sentence_corpus:
            sentence_clean = re.sub(r"\s+", " ", str(sentence or "")).strip()
            sentence_tokens = self._meaningful_tokens(sentence_clean)
            if not sentence_tokens:
                continue
            overlap = candidate_tokens.intersection(sentence_tokens)
            if not overlap:
                continue
            coverage = len(overlap) / max(1, len(candidate_tokens))
            if coverage < 0.6:
                continue
            specificity = len(overlap) / max(1, len(sentence_tokens))
            score = coverage + (specificity * 0.2)
            if score > best_score:
                best_score = score
                best_overlap_match = sentence_clean
        return best_overlap_match

    def _has_incomplete_tail(self, text: str) -> bool:
        words = re.findall(r"[A-Za-z0-9']+", str(text or ""))
        if not words:
            return False
        return self._has_incomplete_tail_words(words)

    def _has_incomplete_tail_words(self, words: list[str]) -> bool:
        if not words:
            return False
        bad_tail = {
            "in",
            "on",
            "at",
            "for",
            "with",
            "without",
            "behind",
            "before",
            "after",
            "while",
            "that",
            "this",
            "my",
            "your",
            "his",
            "her",
            "our",
            "their",
            "someone",
            "something",
            "wasn't",
            "isn't",
            "weren't",
            "than",
            "as",
            "the",
            "old",
            "opened",
            "open",
            "said",
        }
        if words[-1].lower() in bad_tail:
            return True
        if len(words) >= 2 and words[-2].lower() in {"while"}:
            return True
        if len(words) >= 2 and words[-1].lower() in {"my", "your", "his", "her", "our", "their"}:
            return True
        return False

    def _target_shot_count(self, body_sentences: list[str]) -> int:
        sentence_count = len(body_sentences)
        if sentence_count >= 6:
            return 8
        if sentence_count >= 5:
            return 7
        return 6

    def _beat_sequence(self, shot_count: int) -> list[str]:
        if shot_count < 6 or shot_count > 8:
            raise ValueError("shot_count must be between 6 and 8.")
        beats = list(self._BASE_BEATS)
        if shot_count >= 7:
            beats.append(self._EXTRA_BEATS[0])
        if shot_count >= 8:
            beats.append(self._EXTRA_BEATS[1])
        return beats

    def _line_map_for_beats(
        self,
        title: str,
        hook: str,
        body_sentences: list[str],
        beats: list[str],
        story_details: dict[str, Any],
        scene_profile: dict[str, Any],
    ) -> dict[str, str]:
        _ = title
        pool = [line for line in body_sentences if line.strip()]
        if not pool:
            pool = [hook]

        scene_kind = str(scene_profile.get("kind", "generic_interior"))
        reflection_story = self._is_reflection_story_scene(scene_profile=scene_profile, lines=pool, hook=hook)
        if reflection_story:
            hook_line_clean = re.sub(r"\s+", " ", str(hook or "")).strip()
            normalized_pool = {re.sub(r"\s+", " ", str(line or "")).strip() for line in pool}
            if (
                hook_line_clean
                and hook_line_clean not in normalized_pool
                and self._line_is_reflection_baseline(hook_line_clean)
                and not self._line_has_reflection_anomaly(hook_line_clean)
            ):
                pool = [hook_line_clean] + pool
        hook_idx: int | None = None
        if reflection_story:
            hook_idx = self._pick_reflection_hook_index(pool)
        hook_line = pool[hook_idx] if hook_idx is not None else hook

        mapping: dict[str, str] = {"hook_image": hook_line}
        core_beats = [beat for beat in self._BASE_BEATS if beat != "hook_image" and beat in beats]
        prefer_early_reveal = scene_kind not in {"elevator_lobby", "generic_interior"}
        unknown_rank_mode = self._use_unknown_fallback_ranking(
            title=title,
            hook=hook,
            lines=pool,
            scene_profile=scene_profile,
        )

        def setup_scorer(line: str, idx: int, total: int) -> int:
            score = self._setup_line_score(line, idx, total)
            if unknown_rank_mode:
                score += self._unknown_setup_rank_adjustment(line)
            return score

        def strange_scorer(line: str, idx: int, total: int) -> int:
            score = self._strange_line_score(line, idx, total)
            if unknown_rank_mode:
                score += self._unknown_strange_rank_adjustment(line)
            return score

        def escalation_scorer(line: str, idx: int, total: int) -> int:
            score = self._escalation_line_score(line, idx, total)
            if unknown_rank_mode:
                score += self._unknown_escalation_rank_adjustment(line)
            return score

        def reveal_scorer(line: str, idx: int, total: int) -> int:
            score = self._reveal_line_score(line, idx, total)
            if unknown_rank_mode:
                score += self._unknown_reveal_rank_adjustment(line)
            return score

        def consequence_scorer(line: str, idx: int, total: int) -> int:
            score = self._consequence_line_score(line, idx, total)
            if unknown_rank_mode:
                score += self._unknown_consequence_rank_adjustment(line)
            return score

        scorer_by_beat: dict[str, Any] = {
            "setup": setup_scorer,
            "strange_detail": strange_scorer,
            "escalation": escalation_scorer,
            "strongest_reveal": reveal_scorer,
            "consequence_ending": consequence_scorer,
        }

        beat_indices: dict[str, int] = {}
        used: set[int] = set()
        if hook_idx is not None:
            used.add(hook_idx)

        setup_candidates: list[int] | None = None
        if reflection_story:
            setup_candidates = [
                idx
                for idx, line in enumerate(pool)
                if idx not in used and self._line_is_reflection_baseline(line)
            ]
            if not setup_candidates:
                setup_candidates = None

        setup_idx = self._pick_best_index(
            pool,
            used,
            setup_scorer,
            prefer_early=True,
            candidate_indices=setup_candidates,
        )
        beat_indices["setup"] = setup_idx
        used.add(setup_idx)

        reveal_after = min(len(pool) - 1, setup_idx + 1) if len(pool) > 1 else 0
        reveal_idx = self._pick_best_index(
            pool,
            used,
            reveal_scorer,
            after_idx=reveal_after,
            prefer_early=prefer_early_reveal,
        )
        beat_indices["strongest_reveal"] = reveal_idx
        used.add(reveal_idx)

        if unknown_rank_mode:
            strange_candidates = [idx for idx in range(0, reveal_idx) if idx not in used and idx != reveal_idx]
        else:
            strange_candidates = [idx for idx in range(setup_idx, reveal_idx) if idx not in used]
        if not strange_candidates:
            strange_candidates = [idx for idx in range(0, reveal_idx) if idx not in used]
        if not strange_candidates:
            strange_candidates = [idx for idx in range(len(pool)) if idx not in used and idx != reveal_idx]
        strange_idx = self._pick_best_index(
            pool,
            set(),
            strange_scorer,
            after_idx=None,
            prefer_early=True,
            candidate_indices=strange_candidates,
        )
        beat_indices["strange_detail"] = strange_idx
        if strange_idx not in used:
            used.add(strange_idx)

        escalation_candidates = [
            idx
            for idx in range(strange_idx + 1, reveal_idx)
            if idx not in used and idx != reveal_idx
        ]
        if not escalation_candidates:
            escalation_candidates = [
                idx
                for idx in range(setup_idx + 1, reveal_idx)
                if idx not in used and idx != reveal_idx
            ]
        if not escalation_candidates:
            escalation_candidates = [idx for idx in range(len(pool)) if idx not in used and idx != reveal_idx]
        if not escalation_candidates:
            escalation_candidates = [idx for idx in range(len(pool)) if idx != reveal_idx]
        escalation_idx = self._pick_best_index(
            pool,
            set(),
            escalation_scorer,
            prefer_early=True,
            candidate_indices=escalation_candidates,
        )
        beat_indices["escalation"] = escalation_idx
        if escalation_idx not in used:
            used.add(escalation_idx)

        consequence_start = max(reveal_idx, escalation_idx) + 1
        consequence_candidates = [idx for idx in range(consequence_start, len(pool)) if idx not in used]
        if not consequence_candidates:
            consequence_candidates = [idx for idx in range(reveal_idx + 1, len(pool)) if idx not in used]
        if not consequence_candidates:
            consequence_candidates = [idx for idx in range(len(pool)) if idx not in used and idx != reveal_idx]
        if not consequence_candidates:
            consequence_candidates = [idx for idx in range(len(pool)) if idx != reveal_idx]
        consequence_idx = self._pick_best_index(
            pool,
            set(),
            consequence_scorer,
            prefer_early=False,
            candidate_indices=consequence_candidates,
        )
        beat_indices["consequence_ending"] = consequence_idx

        # Guardrail 1: keep source sentence unique across core beats when possible.
        if len(pool) >= len(core_beats):
            seen_indices: dict[int, str] = {}
            for beat in core_beats:
                idx = beat_indices.get(beat, 0)
                if idx not in seen_indices:
                    seen_indices[idx] = beat
                    continue
                replacement = self._next_best_remaining_index(
                    beat=beat,
                    pool=pool,
                    beat_indices=beat_indices,
                    scorer=scorer_by_beat.get(beat, self._setup_line_score),
                    avoid_indices={beat_indices.get("strongest_reveal", -1)},
                )
                if replacement is not None:
                    beat_indices[beat] = replacement
                    seen_indices[replacement] = beat

        # Guardrail 2: adjacent beats cannot use same sentence or same event if alternative exists.
        adjacent_pairs = [
            ("setup", "strange_detail"),
            ("strange_detail", "escalation"),
            ("escalation", "strongest_reveal"),
            ("strongest_reveal", "consequence_ending"),
        ]
        for prev_beat, curr_beat in adjacent_pairs:
            if prev_beat not in beat_indices or curr_beat not in beat_indices:
                continue
            prev_idx = beat_indices[prev_beat]
            curr_idx = beat_indices[curr_beat]
            prev_line = pool[prev_idx]
            curr_line = pool[curr_idx]
            if prev_idx == curr_idx or self._same_event_signature(prev_line, curr_line):
                replacement = self._next_best_remaining_index(
                    beat=curr_beat,
                    pool=pool,
                    beat_indices=beat_indices,
                    scorer=scorer_by_beat.get(curr_beat, self._setup_line_score),
                    avoid_indices={prev_idx, beat_indices.get("strongest_reveal", -1)},
                )
                if replacement is not None:
                    beat_indices[curr_beat] = replacement

        for beat in core_beats:
            idx = beat_indices.get(beat, 0)
            mapping[beat] = pool[idx]

        # Keep escalation unique and never let it fall back to the hook event.
        escalation_text = str(mapping.get("escalation", "")).strip()
        escalation_conflicts = {
            beat_name
            for beat_name in ("hook_image", "setup", "strange_detail", "strongest_reveal", "consequence_ending")
            if beat_name in mapping and escalation_text and mapping[beat_name].strip() == escalation_text
        }
        if escalation_conflicts:
            mapping["escalation"] = self._synthesize_escalation_source(
                hook_line=mapping.get("hook_image", hook),
                setup_line=mapping.get("setup", ""),
                strange_line=mapping.get("strange_detail", ""),
                reveal_line=mapping.get("strongest_reveal", ""),
                consequence_line=mapping.get("consequence_ending", ""),
                story_details=story_details,
                scene_profile=scene_profile,
            )

        tail_candidates = list(pool[::-1])
        for beat in beats:
            if beat in mapping:
                continue
            chosen = ""
            for line in tail_candidates:
                candidate = str(line or "").strip()
                if not candidate:
                    continue
                if candidate not in mapping.values():
                    chosen = candidate
                    break
            mapping[beat] = chosen or pool[-1]

        return mapping

    def extract_story_details(self, text: str) -> dict[str, Any]:
        lowered = str(text or "").lower()
        sentences = self._split_sentences(text)

        objects: list[str] = []
        for label, pattern in self._OBJECT_PATTERNS.items():
            if re.search(pattern, lowered):
                objects.append(label)

        actions: list[str] = []
        for label, pattern in self._ACTION_PATTERNS.items():
            if re.search(pattern, lowered):
                actions.append(label.replace("_", " "))

        reveal_phrases: list[str] = []
        consequence_phrases: list[str] = []
        for sentence in sentences:
            low = sentence.lower()
            if self._contains_any(low, self._REVEAL_MARKERS):
                reveal_phrases.append(sentence.strip())
            if self._contains_any(low, self._CONSEQUENCE_MARKERS):
                consequence_phrases.append(sentence.strip())

        if not reveal_phrases:
            for sentence in sentences:
                low = sentence.lower()
                if "only one person" in low or "no one" in low or "everyone" in low:
                    reveal_phrases.append(sentence.strip())

        return {
            "objects": objects[:8],
            "actions": actions[:8],
            "reveal_phrases": reveal_phrases[:4],
            "consequence_phrases": consequence_phrases[:4],
            "primary_object": objects[0] if objects else "",
            "primary_action": actions[0] if actions else "",
        }

    def _extract_scene_profile(
        self,
        title: str,
        hook: str,
        narration: str,
        story_details: dict[str, Any],
    ) -> dict[str, Any]:
        text = f"{title} {hook} {narration}".lower()
        scores = {
            "room_blackout": 0,
            "hallway_mirror": 0,
            "elevator_lobby": 0,
            "door_confession": 0,
            "note_threshold": 0,
            "generic_interior": 0,
        }

        if self._contains_any(text, ["lights went out", "blackout", "power", "dark room"]):
            scores["room_blackout"] += 4
        if self._contains_any(text, ["room", "table", "people", "ceiling", "inside"]):
            scores["room_blackout"] += 2

        has_mirror_terms = self._contains_any(text, ["mirror", "reflection", "hallway mirror", "stairwell mirror"])
        mirror_terms_are_phone_only = self._contains_any(text, ["phone mirror", "mirror in my phone"]) and not self._contains_any(
            text,
            ["hallway mirror", "stairwell mirror", "reflection moved", "turned before i did", "blinked late", "mirror version"],
        )
        has_mirror_context = self._contains_any(
            text,
            [
                "stairwell",
                "landing",
                "hallway mirror",
                "stairwell mirror",
                "turned before i did",
                "one second after",
                "blinked late",
                "mirror version kept moving",
            ],
        )
        if has_mirror_terms and has_mirror_context and not mirror_terms_are_phone_only:
            scores["hallway_mirror"] += 4
        if self._contains_any(text, ["hallway", "corridor"]) and self._contains_any(text, ["hallway mirror", "stairwell mirror"]):
            scores["hallway_mirror"] += 2

        has_elevator_core_markers = self._contains_any(
            text,
            ["elevator", "call button", "indicator", "cab", "floor indicator", "floor panel", "wrong floor"],
        )
        if has_elevator_core_markers:
            scores["elevator_lobby"] += 4
        if "lobby" in text and has_elevator_core_markers:
            scores["elevator_lobby"] += 1
        if self._contains_any(text, ["wrong floor", "opened on the wrong floor", "rode to the lobby"]):
            scores["elevator_lobby"] += 4

        has_door_confession_cues = self._contains_any(
            text,
            ["confession", "confess", "outside my door", "latch", "knob", "whispered"],
        )
        if has_door_confession_cues:
            scores["door_confession"] += 3
        if has_door_confession_cues and self._contains_any(text, ["door", "doorway", "threshold"]):
            scores["door_confession"] += 1

        if self._contains_any(text, ["note", "under my door", "threshold", "shoe"]):
            scores["note_threshold"] += 3
        if "note" in text and "door" in text:
            scores["note_threshold"] += 2

        scores["generic_interior"] = 1
        if self._contains_any(text, ["exit sign", "exit arrow"]) and self._contains_any(
            text,
            ["wrong way", "wrong direction", "pointed", "different direction", "each arrow"],
        ):
            scores["generic_interior"] += 3
            scores["door_confession"] -= 1

        kind = max(scores.items(), key=lambda item: item[1])[0]
        anchors = {
            "room_blackout": "blackout room interior with people silhouettes and an exit sign glow",
            "hallway_mirror": "narrow hallway with a mirror and deep reflections",
            "elevator_lobby": "elevator cab opening to a mismatched floor or lobby indicator",
            "door_confession": "outside a closed door with latch detail and tense stillness",
            "note_threshold": "door threshold with a note near floor level",
            "generic_interior": "interior scene grounded in the script details",
        }

        scene_nouns: list[str] = []
        noun_markers = [
            "hallway",
            "corridor",
            "room",
            "door",
            "doorway",
            "threshold",
            "mirror",
            "reflection",
            "elevator",
            "floor",
            "lobby",
            "stairwell",
            "landing",
            "exit sign",
            "exit",
            "table",
            "chair",
            "note",
            "shoe",
            "phone",
            "glass",
            "apartment",
            "footsteps",
            "call button",
            "latch",
            "knob",
        ]
        for marker in noun_markers:
            if marker in text:
                scene_nouns.append(marker)

        allow_hallway = "hallway" in scene_nouns or "corridor" in scene_nouns
        door_object_mentioned = bool(re.search(self._OBJECT_PATTERNS.get("door", r"\bdoor\b"), text))
        allow_door = door_object_mentioned or any(item in scene_nouns for item in ("doorway", "threshold", "latch", "knob"))
        allow_mirror = "mirror" in scene_nouns or "reflection" in scene_nouns
        allow_elevator = any(item in scene_nouns for item in ("elevator", "floor", "lobby", "call button"))
        if kind in {"door_confession", "note_threshold"}:
            allow_door = True
        if kind == "hallway_mirror":
            allow_hallway = True
            allow_mirror = True
            allow_elevator = False
        if kind == "elevator_lobby":
            allow_elevator = True
            allow_mirror = False
            allow_hallway = False

        return {
            "kind": kind,
            "anchor": anchors.get(kind, anchors["generic_interior"]),
            "scene_nouns": scene_nouns,
            "allow_hallway": allow_hallway,
            "allow_door": allow_door,
            "allow_mirror": allow_mirror,
            "allow_elevator": allow_elevator,
            "story_objects": list(story_details.get("objects", [])),
        }

    def _pick_best_index(
        self,
        lines: list[str],
        used: set[int],
        scorer: Any,
        after_idx: int | None = None,
        prefer_early: bool = True,
        candidate_indices: list[int] | None = None,
    ) -> int:
        candidates: list[int]
        if candidate_indices is not None:
            candidates = [
                idx
                for idx in candidate_indices
                if idx not in used and 0 <= idx < len(lines) and (after_idx is None or idx >= after_idx)
            ]
        else:
            candidates = [
                idx
                for idx in range(len(lines))
                if idx not in used and (after_idx is None or idx >= after_idx)
            ]
        if not candidates:
            candidates = [idx for idx in range(len(lines)) if idx not in used]
        if not candidates:
            return max(0, len(lines) - 1)

        best_idx = candidates[0]
        best_score = scorer(lines[best_idx], best_idx, len(lines))
        for idx in candidates[1:]:
            score = scorer(lines[idx], idx, len(lines))
            if score > best_score:
                best_idx = idx
                best_score = score
                continue
            if score == best_score:
                if prefer_early and idx < best_idx:
                    best_idx = idx
                if not prefer_early and idx > best_idx:
                    best_idx = idx
        return best_idx

    def _setup_line_score(self, line: str, idx: int, total: int) -> int:
        low = line.lower()
        score = 0
        if idx <= 1:
            score += 2
        if self._contains_any(low, ["at first", "before", "just", "hallway", "room", "door"]):
            score += 2
        if self._line_is_reflection_baseline(low):
            score += 4
        if self._line_has_reflection_anomaly(low):
            score -= 10
        if self._contains_any(low, self._REVEAL_MARKERS):
            score -= 2
        if self._contains_any(low, self._CONSEQUENCE_MARKERS):
            score -= 3
        return score

    def _strange_line_score(self, line: str, idx: int, total: int) -> int:
        low = line.lower()
        score = 0
        if self._contains_any(
            low,
            ["silent", "scrape", "stopped", "cracked", "no one", "impossible", "wrong", "shifted"],
        ):
            score += 4
        if self._line_has_reflection_anomaly(low):
            score += 10
        if self._line_is_soft_atmosphere_cue(low):
            score += 4
        if "under my door" in low or "at my shoe" in low:
            score += 3
        if idx <= max(1, total // 2):
            score += 1
        if self._line_has_irreversible_reveal(low):
            score -= 5
        if self._contains_any(low, self._CONSEQUENCE_MARKERS):
            score -= 2
        return score

    def _escalation_line_score(self, line: str, idx: int, total: int) -> int:
        low = line.lower()
        score = 0
        if self._contains_any(
            low,
            ["then", "every", "turned", "toward", "backed", "pressure", "facing", "louder", "same corner"],
        ):
            score += 4
        if self._contains_any(low, ["again", "kept", "stayed", "waited", "listened", "second"]):
            score += 2
        if idx >= max(1, total // 3):
            score += 1
        if self._line_has_irreversible_reveal(low):
            score -= 7
        if self._contains_any(low, ["called", "walked outside", "before sunrise"]):
            score -= 5
        if self._contains_any(low, self._CONSEQUENCE_MARKERS):
            score -= 2
        return score

    def _reveal_line_score(self, line: str, idx: int, total: int) -> int:
        low = line.lower()
        score = 0
        if "only one person" in low:
            score += 10
        if "nickname" in low:
            score += 8
        if self._line_has_voice_anomaly(low):
            score += 16
        if self._line_has_reflection_anomaly(low):
            score += 18
        if self._contains_any(low, ["wrong person", "every face", "everyone", "from the hallway side", "behind"]):
            score += 6
        if self._line_has_irreversible_reveal(low):
            score += 12
        if "call button" in low and self._contains_any(low, ["lit", "before anyone", "stepped out"]):
            score += 14
        if self._contains_any(low, ["wrong floor", "didn't match", "did not match", "indicator"]):
            score += 6
        if self._contains_any(low, ["cold air", "felt colder", "empty landing", "rail clicked"]) and not (
            self._line_has_reflection_anomaly(low) or self._line_has_voice_anomaly(low) or self._line_has_irreversible_reveal(low)
        ):
            score -= 6
        if self._line_is_soft_atmosphere_cue(low) and not self._line_has_irreversible_reveal(low):
            score -= 4
        if self._contains_any(low, self._REVEAL_MARKERS):
            score += 3
        if self._contains_any(low, self._CONSEQUENCE_MARKERS):
            score -= 6
        if idx >= max(1, total // 2):
            score += 2
        return score

    def _consequence_line_score(self, line: str, idx: int, total: int) -> int:
        low = line.lower()
        score = 0
        if self._contains_any(low, self._CONSEQUENCE_MARKERS):
            score += 8
        if low.startswith("i "):
            score += 2
        if idx >= total - 2:
            score += 2
        if self._contains_any(low, ["stayed inside the elevator", "rode straight to the lobby", "rode down to the lobby"]):
            score += 6
        if self._contains_any(low, ["checked the mirror in my phone", "phone mirror", "waited outside until sunrise", "never opened", "didn't open", "did not open"]):
            score += 8
        if self._line_has_irreversible_reveal(low):
            score -= 6
        if self._contains_any(low, ["only one person", "nickname", "wrong person"]):
            score -= 3
        return score

    def _contains_any(self, text: str, markers: list[str]) -> bool:
        return any(marker in text for marker in markers)

    def _use_unknown_fallback_ranking(
        self,
        title: str,
        hook: str,
        lines: list[str],
        scene_profile: dict[str, Any],
    ) -> bool:
        scene_kind = str(scene_profile.get("kind", "generic_interior"))
        text = " ".join([str(title or ""), str(hook or "")] + [str(line or "") for line in lines]).lower()

        # Keep tuned archetypes on their existing ranking behavior.
        if self._contains_any(text, ["under my door", "note under", "old handwriting", "envelope"]):
            return False
        if self._contains_any(text, ["lights went out", "blackout", "power cut", "went dark"]):
            return False
        if self._contains_any(text, ["confession", "overheard"]):
            return False
        if self._contains_any(text, ["hallway mirror", "stairwell mirror", "reflection"]) and self._contains_any(text, ["stairwell", "landing"]):
            return False

        if scene_kind == "elevator_lobby":
            return True
        if scene_kind != "generic_interior":
            return False
        return self._contains_any(
            text,
            [
                "elevator",
                "wrong floor",
                "floor indicator",
                "call button",
                "exit sign",
                "arrow",
                "latch moved",
                "wrong person",
                "nickname",
                "reflection",
            ],
        )

    def _line_has_personal_impossible_evidence(self, line: str) -> bool:
        low = str(line or "").lower()
        markers = [
            "my own doormat",
            "i saw my own doormat",
            "wrong person in my spot",
            "wrong person in my seat",
            "my old nickname",
            "nickname only one person knew",
            "only one person knew",
            "my reflection",
            "reflection lifted",
            "reflection moved",
            "before i did",
            "one second after mine",
            "wasn't mine",
            "was not mine",
        ]
        return self._contains_any(low, markers)

    def _line_has_object_event_contradiction(self, line: str) -> bool:
        low = str(line or "").lower()
        markers = [
            "call button",
            "floor indicator",
            "wrong floor",
            "didn't match",
            "did not match",
            "same empty floor",
            "opened again",
            "exit sign",
            "arrow flipped",
            "pointed the wrong way",
            "latch moved",
            "latch clicked",
            "handle turned",
            "door handle moved",
        ]
        return self._contains_any(low, markers) or self._line_has_irreversible_reveal(low)

    def _line_has_movement_anomaly(self, line: str) -> bool:
        low = str(line or "").lower()
        markers = [
            "shadow moved",
            "movement",
            "moved against",
            "no one was there",
            "bodyless",
            "without anyone",
            "footsteps stopped",
            "footsteps restarted",
            "shape moved",
            "reflection shift",
        ]
        return self._contains_any(low, markers)

    def _line_has_reflection_anomaly(self, line: str) -> bool:
        low = str(line or "").lower()
        if not self._contains_any(low, ["mirror", "reflection"]):
            return False
        return self._contains_any(
            low,
            [
                "turned before i did",
                "one second after mine",
                "blinked late",
                "kept moving",
                "did not match",
                "didn't match",
                "moved without",
                "version kept moving",
                "lifted its hand",
                "before i did",
            ],
        )

    def _is_reflection_story_scene(self, scene_profile: dict[str, Any], lines: list[str], hook: str) -> bool:
        scene_kind = str(scene_profile.get("kind", "generic_interior"))
        if scene_kind != "hallway_mirror":
            return False
        candidates = [str(hook or "")] + [str(line or "") for line in lines]
        return any(self._line_has_reflection_anomaly(item) for item in candidates)

    def _pick_reflection_hook_index(self, lines: list[str]) -> int | None:
        for idx, line in enumerate(lines):
            if self._line_has_reflection_anomaly(line):
                return idx
        return None

    def _line_is_reflection_baseline(self, line: str) -> bool:
        low = str(line or "").lower()
        if self._line_has_reflection_anomaly(low):
            return False
        if self._contains_any(low, ["cold air", "felt colder", "empty landing", "rail clicked"]):
            return False
        baseline_markers = [
            "stairwell",
            "landing",
            "fire door",
            "watched the hallway mirror",
            "watched the mirror",
            "by the fire door",
            "stayed by the mirror",
        ]
        return self._contains_any(low, baseline_markers)

    def _line_has_voice_anomaly(self, line: str) -> bool:
        low = str(line or "").lower()
        has_voice_marker = self._contains_any(
            low,
            [
                "voice",
                "whisper",
                "nickname",
                "name",
                "knock",
                "breathing",
                "speaker",
                "through my speaker",
            ],
        )
        if not has_voice_marker:
            return False
        return self._contains_any(
            low,
            [
                "old nickname",
                "repeated",
                "same time",
                "through my speaker",
                "no one has used",
                "said my old nickname",
                "voice said",
                "voice repeated",
                "breathing on the other side",
                "soft knock outside",
            ],
        )

    def _line_is_decision_outcome(self, line: str) -> bool:
        low = str(line or "").lower()
        if self._contains_any(low, self._CONSEQUENCE_MARKERS):
            return True
        return self._contains_any(
            low,
            [
                "i stayed inside",
                "i stayed in the elevator",
                "i rode straight to the lobby",
                "i rode down to the lobby",
                "i ran for the stairs",
                "i moved for the stairs",
                "i left the building",
                "i left",
                "i backed away",
                "i called",
                "i decided",
                "i got out",
                "i checked the mirror in my phone",
                "i waited outside until sunrise",
                "never opened",
                "didn't open",
                "did not open",
            ],
        )

    def _line_has_baseline_mismatch(self, line: str) -> bool:
        low = str(line or "").lower()
        if "call button" in low and self._contains_any(low, ["lit", "before anyone", "stepped out"]):
            return False
        return self._contains_any(
            low,
            [
                "opened on the wrong floor",
                "panel showed my floor",
                "different number",
                "wrong floor",
                "didn't match",
                "did not match",
                "floor indicator",
                "floor panel",
            ],
        )

    def _unknown_setup_rank_adjustment(self, line: str) -> int:
        if self._line_is_decision_outcome(line):
            return -18
        if self._line_has_personal_impossible_evidence(line):
            return -24
        if self._line_has_baseline_mismatch(line):
            return 8
        if self._line_has_irreversible_reveal(line):
            return -12
        if self._line_has_object_event_contradiction(line):
            return -8
        if self._line_has_movement_anomaly(line):
            return -6
        if self._line_is_soft_atmosphere_cue(line):
            return -6
        return 1

    def _unknown_strange_rank_adjustment(self, line: str) -> int:
        if self._line_is_decision_outcome(line):
            return -20
        if self._line_has_personal_impossible_evidence(line):
            return 22
        if self._line_has_object_event_contradiction(line):
            return 16
        if self._line_has_movement_anomaly(line):
            return 10
        if self._line_is_soft_atmosphere_cue(line):
            return 2
        return 0

    def _unknown_escalation_rank_adjustment(self, line: str) -> int:
        if self._line_is_decision_outcome(line):
            return -12
        if self._line_has_personal_impossible_evidence(line):
            return -6
        if self._line_has_object_event_contradiction(line):
            return 4
        if self._line_has_movement_anomaly(line):
            return 6
        if self._line_is_soft_atmosphere_cue(line):
            return 2
        return 0

    def _unknown_reveal_rank_adjustment(self, line: str) -> int:
        if self._line_is_decision_outcome(line):
            return -24
        if self._line_has_reflection_anomaly(line):
            return 24
        if self._line_has_voice_anomaly(line):
            return 22
        if self._line_has_personal_impossible_evidence(line):
            return 20
        if self._line_has_object_event_contradiction(line):
            return 18
        if self._line_has_movement_anomaly(line):
            return 8
        if self._line_is_soft_atmosphere_cue(line):
            return -6
        return 0

    def _unknown_consequence_rank_adjustment(self, line: str) -> int:
        if self._line_is_decision_outcome(line):
            return 8
        if self._line_has_personal_impossible_evidence(line) or self._line_has_object_event_contradiction(line):
            return -10
        return 0

    def _line_has_irreversible_reveal(self, line: str) -> bool:
        low = str(line or "").lower()
        if "call button" in low and self._contains_any(low, ["lit", "before anyone", "stepped out"]):
            return True
        if self._line_has_reflection_anomaly(low):
            return True
        if self._line_has_voice_anomaly(low) and self._contains_any(low, ["nickname", "repeated", "same time"]):
            return True
        markers = [
            "before anyone",
            "no one stepped out",
            "no one was there",
            "didn't match",
            "did not match",
            "wrong floor",
            "same empty floor",
            "opened again to the same",
            "every exit sign",
            "arrow flipped",
            "pointed the wrong way",
            "same door again",
            "moved without anyone",
        ]
        return self._contains_any(low, markers)

    def _line_is_soft_atmosphere_cue(self, line: str) -> bool:
        low = str(line or "").lower()
        markers = [
            "air felt colder",
            "felt colder",
            "motor hum",
            "stayed empty",
            "stayed silent",
            "went quiet",
            "stayed still",
            "listened",
            "silence settled",
        ]
        return self._contains_any(low, markers)

    def _is_unknown_object_grounding_scene(self, scene_profile: dict[str, Any]) -> bool:
        return str(scene_profile.get("kind", "generic_interior")) in {"elevator_lobby", "generic_interior"}

    def _anomaly_focus_object(self, beat_text: str) -> str:
        low = str(beat_text or "").lower()
        if self._contains_any(low, ["my own doormat", "doormat"]):
            return "doormat"
        if "call button" in low:
            return "call_button"
        if self._contains_any(
            low,
            [
                "floor indicator",
                "floor panel",
                "floor number",
                "panel showed",
                "wrong floor",
                "different number",
                "didn't match",
                "did not match",
            ],
        ):
            return "floor_indicator"
        if self._contains_any(low, ["latch", "door handle", "handle turned", "door seam"]):
            return "latch"
        if self._contains_any(low, ["mirror", "reflection"]):
            return "mirror"
        if self._contains_any(low, ["exit sign", "exit arrow", "arrow flipped", "pointed the wrong way"]):
            return "exit_sign"
        return ""

    def _focus_phrase_from_object(self, anomaly_object: str, scene_profile: dict[str, Any]) -> str:
        scene_kind = str(scene_profile.get("kind", "generic_interior"))
        if anomaly_object == "doormat":
            if scene_kind == "elevator_lobby":
                return "the doormat at the wrong-floor threshold"
            return "the doormat at the threshold"
        if anomaly_object == "call_button":
            return "the call button panel and its glow"
        if anomaly_object == "floor_indicator":
            return "the floor indicator and panel number mismatch"
        if anomaly_object == "latch":
            return "the latch and door seam"
        if anomaly_object == "mirror":
            return "the mirror reflection line"
        if anomaly_object == "exit_sign":
            return "the exit sign arrow"
        return ""

    def _object_priority_prompt_parts(
        self,
        anomaly_object: str,
        beat: str,
        scene_profile: dict[str, Any],
        clue: str,
    ) -> tuple[str, str, str]:
        scene_kind = str(scene_profile.get("kind", "generic_interior"))
        if anomaly_object == "doormat":
            subject = "the doormat at the wrong-floor threshold" if scene_kind == "elevator_lobby" else "the doormat at the threshold"
            if beat == "setup":
                framing = "grounded medium frame on the threshold with the doormat clearly centered"
            elif beat == "aftershock":
                framing = "static hold on the doormat left at the threshold after the doors close"
            elif beat == "escalation":
                framing = "tight frame on the doormat and landing seam as pressure rises"
            else:
                framing = "tight frame centered on the doormat and threshold seam"
            return subject, framing, "the narrator's own doormat sits on a floor they never lived on"

        if anomaly_object == "call_button":
            if beat == "setup":
                framing = "grounded panel-level frame with the call button readable beside the doors"
            elif beat == "aftershock":
                framing = "static close frame on the call button panel after the doors shut"
            elif beat == "escalation":
                framing = "tight frame on the call button and panel as the cab idles at the wrong stop"
            else:
                framing = "sharp close frame on the call button panel before anyone appears"
            return (
                "the call button panel outside the elevator",
                framing,
                "the call button lights before anyone steps out",
            )

        if anomaly_object == "floor_indicator":
            if beat == "setup":
                framing = "grounded frame that keeps the floor indicator and threshold in the same shot"
            elif beat == "aftershock":
                framing = "held close frame on the floor indicator after the doors close"
            elif beat == "escalation":
                framing = "tight frame on repeating wrong floor numbers as the doors cycle"
            else:
                framing = "close insert on floor indicator and panel numbers in one frame"
            return (
                "the floor indicator and panel number mismatch",
                framing,
                "the panel shows one floor while the doors open to another",
            )

        if anomaly_object == "latch":
            return (
                "the latch and door seam",
                "tight close frame on latch metal and seam line",
                "the latch and seam move when no one is there",
            )

        if anomaly_object == "mirror":
            return (
                "the mirror and reflection line",
                "tight frame on mirror surface and reflected movement path",
                "the reflection behavior does not match the room",
            )

        if anomaly_object == "exit_sign":
            return (
                "the exit sign and direction arrow",
                "tight frame on the sign face and arrow orientation",
                "the exit arrow points the wrong way",
            )

        return "the key event cue from the source line", "tight centered frame on the exact event cue", clue

    def _synthesize_escalation_source(
        self,
        hook_line: str,
        setup_line: str,
        strange_line: str,
        reveal_line: str,
        consequence_line: str,
        story_details: dict[str, Any],
        scene_profile: dict[str, Any],
    ) -> str:
        lines = [
            str(strange_line or "").strip(),
            str(setup_line or "").strip(),
            str(hook_line or "").strip(),
            str(consequence_line or "").strip(),
        ]
        base_line = next((line for line in lines if line), "")
        base_low = " ".join(line.lower() for line in lines if line)
        objects = {str(item).lower() for item in story_details.get("objects", [])}
        actions = {str(item).lower() for item in story_details.get("actions", [])}
        scene_nouns = {str(item).lower() for item in scene_profile.get("scene_nouns", [])}
        allow_hallway = bool(scene_profile.get("allow_hallway", False))
        allow_door = bool(scene_profile.get("allow_door", False))
        scene_kind = str(scene_profile.get("kind", "generic_interior"))

        has_note = "note" in objects or "note" in base_low
        has_door = allow_door and ("door" in objects or "door" in base_low or "doorway" in base_low)
        has_hallway = allow_hallway and (
            "hallway" in objects or "hallway" in base_low or "corridor" in base_low
        )
        has_mirror = "mirror" in scene_nouns or "mirror" in base_low
        has_latch = "latch" in scene_nouns or "latch" in base_low or "knob" in base_low
        has_silence = "silent" in base_low
        has_dark = self._contains_any(base_low, ["lights went out", "blackout", "dark", "exit sign"])
        has_pickup = "picked up" in actions or "picked up" in base_low
        has_back_away = "backed away" in actions or "backed away" in base_low
        has_confession = self._contains_any(base_low, ["confession", "confess", "whispered"])

        if has_note and has_pickup and has_silence:
            candidate = "I held the note still as everything went silent."
        elif has_note and has_pickup and has_dark:
            candidate = "I lifted the note in the blackout and waited."
        elif has_note and has_pickup:
            candidate = "I lifted the note and kept it folded."
        elif has_mirror and has_silence:
            candidate = "I kept my eyes on the mirror as the hallway went quiet."
        elif has_mirror:
            candidate = "I kept my eyes on the mirror and held still."
        elif has_dark and has_silence:
            candidate = "I held still in the blackout and listened."
        elif has_dark:
            candidate = "I held still in the blackout."
        elif has_confession and has_door:
            if has_latch:
                candidate = "I listened at the latch without opening the door."
            else:
                candidate = "I listened at the door without opening it."
        elif has_hallway and has_silence:
            candidate = "I stayed in the hallway and listened."
        elif has_back_away:
            candidate = "I took one step back and listened."
        elif has_silence:
            candidate = "I stayed still as the silence settled in."
        else:
            clue = self._trim_text_words(base_line or hook_line, max_words=6).lower()
            candidate = f"I held still as {clue}."

        if scene_kind == "room_blackout" and "hallway" in candidate.lower() and not allow_hallway:
            candidate = candidate.replace("hallway", "room")
        if not allow_door and self._contains_any(candidate.lower(), ["door", "doorway"]):
            candidate = re.sub(r"\bdoorway\b", "room edge", candidate, flags=re.IGNORECASE)
            candidate = re.sub(r"\bdoor\b", "room", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if not candidate.endswith("."):
            candidate = f"{candidate}."
        if self._contains_any(candidate.lower(), self._REVEAL_MARKERS):
            candidate = "I held still and listened."

        used_lines = {
            str(hook_line or "").strip().lower(),
            str(setup_line or "").strip().lower(),
            str(strange_line or "").strip().lower(),
            str(reveal_line or "").strip().lower(),
            str(consequence_line or "").strip().lower(),
        }
        normalized_candidate = candidate.strip().lower().rstrip(".")
        if normalized_candidate in {line.rstrip(".") for line in used_lines if line}:
            if has_note and has_silence:
                return "I held the note and listened."
            if has_note:
                return "I held the note and waited."
            if has_dark:
                return "I stayed still in the dark."
            return "I held still and listened."

        return candidate

    def _next_best_remaining_index(
        self,
        beat: str,
        pool: list[str],
        beat_indices: dict[str, int],
        scorer: Any,
        avoid_indices: set[int] | None = None,
    ) -> int | None:
        avoid = set(avoid_indices or set())
        current_idx = beat_indices.get(beat, 0)
        occupied = {idx for key, idx in beat_indices.items() if key != beat}
        candidates = [idx for idx in range(len(pool)) if idx not in occupied and idx not in avoid]
        if current_idx in candidates and len(candidates) > 1:
            candidates = [idx for idx in candidates if idx != current_idx]

        if beat != "strongest_reveal":
            reveal_idx = beat_indices.get("strongest_reveal", -1)
            non_reveal_candidates = [idx for idx in candidates if idx != reveal_idx]
            if non_reveal_candidates:
                candidates = non_reveal_candidates

        if not candidates:
            return None

        return self._pick_best_index(
            lines=pool,
            used=set(),
            scorer=scorer,
            prefer_early=(beat != "consequence_ending"),
            candidate_indices=candidates,
        )

    def _same_event_signature(self, left: str, right: str) -> bool:
        left_tokens = self._meaningful_tokens(left)
        right_tokens = self._meaningful_tokens(right)
        if not left_tokens or not right_tokens:
            return False
        overlap = left_tokens.intersection(right_tokens)
        if len(overlap) >= 3:
            return True
        union = left_tokens.union(right_tokens)
        if not union:
            return False
        return (len(overlap) / len(union)) >= 0.7

    def _infer_scene_anchor(self, title: str, hook: str, narration: str) -> str:
        text = f"{title} {hook} {narration}".lower()
        if any(term in text for term in ("hallway", "corridor")):
            return "narrow apartment hallway at night, textured walls, closed doors"
        if any(term in text for term in ("door", "latch", "knob")):
            return "closed interior door in a dim apartment, latch in focus"
        if "room" in text:
            return "small meeting room with a table and scattered objects"
        if any(term in text for term in ("light", "blackout")):
            return "dark interior after a sudden power drop, weak emergency spill"
        return "quiet urban apartment interior, minimal clutter, believable realism"

    def _build_prompt_text(
        self,
        beat: str,
        beat_text: str,
        scene_anchor: str,
        shot_index: int,
        story_details: dict[str, Any],
        scene_profile: dict[str, Any],
    ) -> str:
        subject = self._subject_for_prompt(
            beat=beat,
            beat_text=beat_text,
            scene_anchor=scene_anchor,
            story_details=story_details,
            scene_profile=scene_profile,
        )
        framing = self._pick_scene_beat_phrase(
            beat=beat,
            shot_index=shot_index,
            scene_profile=scene_profile,
            base_map=self._BEAT_FRAMING,
            scene_map=self._SCENE_BEAT_FRAMING,
            fallback=self._FRAMING_CUES,
        )
        lighting = self._pick_scene_beat_phrase(
            beat=beat,
            shot_index=shot_index,
            scene_profile=scene_profile,
            base_map=self._BEAT_LIGHTING,
            scene_map=self._SCENE_BEAT_LIGHTING,
            fallback=self._LIGHTING,
        )
        clue = self._prompt_clue_text(
            beat=beat,
            beat_text=beat_text,
            story_details=story_details,
            scene_profile=scene_profile,
        )
        camera = self._pick_beat_phrase(self._BEAT_CAMERA, beat, shot_index, fallback=self._CAMERA_MOVES)
        subject, framing, lighting, clue = self._apply_event_prompt_overrides(
            beat=beat,
            beat_text=beat_text,
            story_details=story_details,
            scene_profile=scene_profile,
            subject=subject,
            framing=framing,
            lighting=lighting,
            clue=clue,
        )

        subject = self._lock_phrase_to_scene(subject, scene_profile)
        framing = self._lock_phrase_to_scene(framing, scene_profile)
        lighting = self._lock_phrase_to_scene(lighting, scene_profile)
        clue = self._lock_phrase_to_scene(clue, scene_profile)

        subject = self._lock_phrase_to_story_entities(subject, story_details)
        framing = self._lock_phrase_to_story_entities(framing, story_details)
        lighting = self._lock_phrase_to_story_entities(lighting, story_details)
        clue = self._lock_phrase_to_story_entities(clue, story_details)

        if (
            str(scene_profile.get("kind", "")) == "room_blackout"
            and "hallway" not in str(beat_text or "").lower()
            and beat not in {"consequence_ending", "aftershock", "final_echo"}
        ):
            subject = re.sub(r"\bhallway\b|\bcorridor\b", "room", subject, flags=re.IGNORECASE)
            framing = re.sub(r"\bhallway\b|\bcorridor\b", "room", framing, flags=re.IGNORECASE)
            lighting = re.sub(r"\bhallway\b|\bcorridor\b", "room", lighting, flags=re.IGNORECASE)
            clue = re.sub(r"\bhallway\b|\bcorridor\b", "room", clue, flags=re.IGNORECASE)

        style = (shot_index + len(beat)) % 4
        if style == 0:
            return f"{subject}, {framing}, {lighting}, {clue}, with a restrained {camera}."
        if style == 1:
            return (
                f"{subject} in {framing}, lit by {lighting}; "
                f"{clue}; camera movement stays restrained with a {camera}."
            )
        if style == 2:
            return f"{lighting} on {subject}. {framing}. {clue}. Keep a restrained {camera}."
        return (
            f"{subject}. {framing} under {lighting}, {clue}, "
            f"then finish with a restrained {camera}."
        )

    def _build_visual_goal(
        self,
        beat: str,
        beat_text: str,
        story_details: dict[str, Any],
        scene_profile: dict[str, Any],
    ) -> str:
        clue = self._story_clue_for_beat(beat=beat, beat_text=beat_text, story_details=story_details)
        primary_object = str(story_details.get("primary_object", "")).strip()
        scene_kind = str(scene_profile.get("kind", "generic_interior"))
        anomaly_object = self._anomaly_focus_object(beat_text)
        anomaly_focus = self._focus_phrase_from_object(anomaly_object, scene_profile)
        use_unknown_object_grounding = self._is_unknown_object_grounding_scene(scene_profile) and bool(anomaly_focus)
        anchor_by_scene = {
            "room_blackout": "room silhouettes and the exit sign",
            "hallway_mirror": "the hallway mirror",
            "elevator_lobby": "the elevator doors and floor indicator",
            "door_confession": "the closed door and latch",
            "note_threshold": "the note at the threshold",
        }
        baseline_anchor = anchor_by_scene.get(scene_kind, primary_object or "the triggering object")

        if beat == "hook_image":
            return f"Open on the first disturbing image, with {baseline_anchor} clearly visible."
        if beat == "setup":
            if use_unknown_object_grounding:
                return f"Ground the baseline around {anomaly_focus} before escalation."
            if scene_kind == "elevator_lobby":
                return "Ground the baseline with the floor indicator and threshold before the shift."
            setup_anchor = anchor_by_scene.get(scene_kind, primary_object or "a stable room detail")
            return f"Ground the baseline space before the shift, anchored by {setup_anchor}."
        if beat == "strange_detail":
            if use_unknown_object_grounding:
                return f"Center the first impossible detail on {anomaly_focus}: {clue}."
            return f"Hold on the first impossible detail: {clue}."
        if beat == "escalation":
            if use_unknown_object_grounding:
                return f"Raise pressure around {anomaly_focus}: {clue}."
            return f"Raise pressure with motion and proximity around this clue: {clue}."
        if beat == "strongest_reveal":
            if use_unknown_object_grounding:
                return f"Center the irreversible reveal on {anomaly_focus}: {clue}."
            if self._line_has_reflection_anomaly(beat_text):
                return "Center the mirror/body sync break in one irreversible reflection frame."
            if self._line_has_voice_anomaly(beat_text):
                return "Center the spoken-name anomaly and the wrong voice source in one irreversible frame."
            return f"Center the irreversible reveal line: {clue}."
        if beat == "consequence_ending":
            low = str(beat_text or "").lower()
            if self._contains_any(low, ["mirror in my phone", "phone mirror", "checked the mirror"]):
                return "End on checking the mirror in the phone while keeping distance from the door."
            if self._contains_any(low, ["waited outside until sunrise", "until sunrise"]) and self._contains_any(low, ["never opened", "didn't open", "did not open", "without opening"]):
                return "End on waiting outside until sunrise with the door never opened."
            if self._contains_any(low, ["never opened", "didn't open", "did not open", "without opening"]):
                return "End on refusing to open the door."
            if (
                self._contains_any(low, ["stayed inside the elevator", "rode straight to the lobby", "rode down to the lobby"])
                or ("elevator" in low and "lobby" in low and "stayed" in low)
            ):
                return "End on staying inside the elevator and riding directly to the lobby."
            return f"End on the decision and its cost in one hard image: {clue}."
        if beat == "aftershock":
            if scene_kind == "elevator_lobby":
                if anomaly_object == "doormat":
                    return "Show concrete residue: the doormat remains at the wrong-floor threshold after the doors close."
                if anomaly_object == "call_button":
                    return "Show concrete residue: the call button glow remains after the ride down."
                return "Show concrete residue: the wrong floor indicator remains visible after the doors close."
            if use_unknown_object_grounding:
                return f"Show concrete residue centered on {anomaly_focus}: {clue}."
            return f"Show residue after the decision: {clue}."
        return f"Leave a final echo using this unresolved detail: {clue}."

    def _apply_event_prompt_overrides(
        self,
        beat: str,
        beat_text: str,
        story_details: dict[str, Any],
        scene_profile: dict[str, Any],
        subject: str,
        framing: str,
        lighting: str,
        clue: str,
    ) -> tuple[str, str, str, str]:
        anomaly_object = self._anomaly_focus_object(beat_text)
        use_unknown_object_grounding = self._is_unknown_object_grounding_scene(scene_profile) and bool(anomaly_object)

        if use_unknown_object_grounding and beat in {"setup", "strange_detail", "escalation"}:
            object_subject, object_framing, object_clue = self._object_priority_prompt_parts(
                anomaly_object=anomaly_object,
                beat=beat,
                scene_profile=scene_profile,
                clue=clue,
            )
            return object_subject, object_framing, lighting, object_clue

        if beat == "strongest_reveal":
            reveal_type = self._infer_reveal_type(beat_text=beat_text, story_details=story_details)
            if use_unknown_object_grounding and reveal_type == "object_clue":
                object_subject, object_framing, object_clue = self._object_priority_prompt_parts(
                    anomaly_object=anomaly_object,
                    beat=beat,
                    scene_profile=scene_profile,
                    clue=clue,
                )
                return object_subject, object_framing, lighting, object_clue
            return self._reveal_prompt_parts(
                reveal_type=reveal_type,
                beat_text=beat_text,
                scene_profile=scene_profile,
                clue=clue,
            )
        if beat == "consequence_ending":
            consequence_type = self._infer_consequence_type(beat_text=beat_text)
            return self._consequence_prompt_parts(
                consequence_type=consequence_type,
                beat_text=beat_text,
                scene_profile=scene_profile,
                clue=clue,
            )
        if beat == "aftershock":
            aftershock_type = self._infer_aftershock_type(beat_text=beat_text, scene_profile=scene_profile)
            return self._aftershock_prompt_parts(
                aftershock_type=aftershock_type,
                beat_text=beat_text,
                scene_profile=scene_profile,
                anomaly_object=anomaly_object,
                clue=clue,
            )
        return subject, framing, lighting, clue

    def _infer_reveal_type(self, beat_text: str, story_details: dict[str, Any]) -> str:
        low = str(beat_text or "").lower()
        objects = {str(item).lower() for item in story_details.get("objects", [])}

        if self._line_has_reflection_anomaly(low):
            return "reflection_anomaly"
        if self._line_has_voice_anomaly(low):
            return "voice_anomaly"
        if (
            "nickname" in low
            or "only one person" in low
            or ("note" in low and self._contains_any(low, ["name", "line", "written", "wrote", "read"]))
        ):
            if "note" in low or "note" in objects:
                return "text_readable"
            return "voice_anomaly"
        if self._contains_any(low, ["latch", "whisper", "voice", "nobody stood", "no one stood"]):
            return "sound_or_latch"
        if "clicked" in low and self._contains_any(low, ["latch", "door", "doorframe", "seam", "knob", "handle"]):
            return "sound_or_latch"
        if self._contains_any(low, ["wrong person", "in my spot", "changed places", "same corner", "every face"]):
            return "position_mismatch"
        if self._contains_any(low, ["shadow", "movement", "mirror", "reflection", "without a body", "moved back"]):
            return "motion_reveal"
        if "note" in objects:
            return "text_readable"
        return "object_clue"

    def _reveal_prompt_parts(
        self,
        reveal_type: str,
        beat_text: str,
        scene_profile: dict[str, Any],
        clue: str,
    ) -> tuple[str, str, str, str]:
        _ = beat_text
        scene_kind = str(scene_profile.get("kind", "generic_interior"))
        if reveal_type == "reflection_anomaly":
            return (
                "the mirror reflection and body-sync mismatch",
                "tight mirror-line frame with both real hand and reflected hand timing visible",
                "low hallway practicals with contrast on face and reflected motion",
                "the reflection moves out of sync with the body",
            )
        if reveal_type == "voice_anomaly":
            return (
                "the closed door area with phone clock and listening posture",
                "close hallway frame on door seam, phone screen clock, and the listener's paused stance",
                "dim corridor spill with phone-screen glow as the time cue",
                "the old nickname is heard from the wrong place and repeats",
            )
        if reveal_type == "text_readable":
            return (
                "the written reveal line on the unfolded note",
                "tight readable close-up with the key words fully legible",
                "clean side key light tuned for paper readability",
                "the reveal line is clearly readable in frame",
            )
        if reveal_type == "motion_reveal":
            motion_subject = "the reflection shift with no matching body" if scene_kind == "hallway_mirror" else "the shadow movement with no visible source"
            return (
                motion_subject,
                "tight frame on the movement path where continuity breaks",
                "low practical spill with silhouette edges still readable",
                "movement appears where no body should be",
            )
        if reveal_type == "position_mismatch":
            return (
                "the wrong person occupying the narrator's position",
                "locked medium frame comparing expected and actual positions",
                "flicker-return practical light revealing spatial contradiction",
                "the room resets with one person in the wrong spot",
            )
        if reveal_type == "sound_or_latch":
            return (
                "the latch and door seam where the whisper lands",
                "close frame on latch movement and seam-level source point",
                "dim side spill across latch metal and door seam",
                "the latch clicks while the whisper comes from the seam",
            )
        return (
            "the key event cue from the source line",
            "tight centered frame on the exact event cue",
            "focused practical spill keeping the event cue readable",
            clue,
        )

    def _infer_consequence_type(self, beat_text: str) -> str:
        low = str(beat_text or "").lower()
        called_out = "called out" in low
        has_retreat = self._contains_any(low, ["left", "ran", "backed away", "walked", "retreat", "stepped back"])
        has_call = self._contains_any(
            low,
            [
                "i called",
                "called the police",
                "called security",
                "called my brother",
                "dialed",
            ],
        ) and not called_out
        has_exit = self._contains_any(low, ["stairs", "exit", "lobby", "outside", "hallway"])
        has_silent_aftermath = self._contains_any(low, ["no one answered", "silent", "empty", "still"])
        has_mirror_phone_check = self._contains_any(low, ["mirror in my phone", "phone mirror", "checked the mirror"])
        has_wait_outside_sunrise = self._contains_any(low, ["waited outside until sunrise", "until sunrise"])
        has_no_open = self._contains_any(low, ["never opened", "didn't open", "did not open", "without opening"])

        if has_mirror_phone_check:
            return "mirror_phone_check"
        if has_wait_outside_sunrise and has_no_open:
            return "outside_until_sunrise_no_open"
        if has_wait_outside_sunrise:
            return "outside_until_sunrise"
        if has_no_open:
            return "no_opening"

        if has_call and has_retreat and has_exit:
            return "retreat_phone_exit"
        if has_call and has_retreat:
            return "retreat_phone"
        if has_call:
            return "phone_call"
        if has_retreat and has_exit:
            return "retreat_exit"
        if has_retreat:
            return "retreat"
        if has_silent_aftermath:
            return "silent_aftermath"
        return "decision_aftermath"

    def _consequence_prompt_parts(
        self,
        consequence_type: str,
        beat_text: str,
        scene_profile: dict[str, Any],
        clue: str,
    ) -> tuple[str, str, str, str]:
        low = str(beat_text or "").lower()
        scene_kind = str(scene_profile.get("kind", "generic_interior"))
        is_hallway = bool(scene_profile.get("allow_hallway", False))

        if consequence_type == "mirror_phone_check":
            return (
                "a phone screen mirror check while standing away from the door",
                "tight frame on phone screen reflection, door seam, and the listener's still posture",
                "dim hallway spill with phone-screen glow as the only hard cue",
                "the mirror is checked in the phone without approaching the door",
            )
        if consequence_type == "outside_until_sunrise_no_open":
            return (
                "a waiting figure outside at first light with the door still shut",
                "wide hold on the closed door and exterior waiting position through sunrise",
                "cold dawn spill with long shadows and a closed-door silhouette",
                "they wait outside until sunrise and never open the door",
            )
        if consequence_type == "outside_until_sunrise":
            return (
                "a waiting figure outside as dawn arrives",
                "static wide frame holding the outside waiting spot through sunrise",
                "cold dawn light replacing night practicals",
                "the wait outside lasts until sunrise",
            )
        if consequence_type == "no_opening":
            return (
                "the closed door while the figure keeps distance",
                "locked frame on the shut door and untouched handle",
                "low ambient spill with the handle edge still visible",
                "the door is never opened",
            )
        if consequence_type == "retreat_phone_exit":
            return (
                "a retreating figure with phone in hand moving toward the exit path",
                "mid-wide frame linking the threat point to stairs or exit route",
                "cold practical light with phone screen glow on the moving hand",
                "the call starts while retreating toward the exit",
            )
        if consequence_type == "retreat_phone":
            return (
                "a retreating figure placing a call without turning back",
                "pull-back frame keeping caller hand and origin point in one plane",
                "low ambient spill with phone glow cutting the frame",
                "the call is made during retreat",
            )
        if consequence_type == "phone_call":
            return (
                "a raised phone hand while distance is kept from the source area",
                "mid-close frame with caller posture and risk zone visible",
                "dim practicals with phone glow as the hard key",
                "the call is made before anyone answers",
            )
        if consequence_type in {"retreat_exit", "retreat"}:
            if self._contains_any(low, ["stairs", "exit", "lobby"]):
                retreat_subject = "the subject backing away toward stairs or exit"
            else:
                retreat_subject = "the subject backing away from the source area"
            return (
                retreat_subject,
                "pull-back frame that keeps escape path and source area together",
                "dim practical spill over the retreat path",
                "the decision is to leave immediately",
            )
        if consequence_type == "silent_aftermath":
            aftermath_subject = "an empty hallway after the call out" if is_hallway else "an empty room after the call out"
            return (
                aftermath_subject,
                "static wide frame with no figures near the source point",
                "weak ambient light with long still shadows",
                "no one answers after the call out",
            )
        if scene_kind == "room_blackout":
            return (
                "a figure moving toward the exit sign in the blackout room",
                "mid-wide room frame with the exit sign and retreat path visible",
                "emergency spill with the exit sign as the only clear cue",
                "the decision is made under blackout pressure",
            )
        return (
            "the immediate decision moment after the reveal",
            "mid-wide frame that holds both the source and the response",
            "low practical light preserving aftermath detail",
            clue,
        )

    def _infer_aftershock_type(self, beat_text: str, scene_profile: dict[str, Any]) -> str:
        low = str(beat_text or "").lower()
        scene_kind = str(scene_profile.get("kind", "generic_interior"))
        if scene_kind == "elevator_lobby":
            if "doormat" in low:
                return "elevator_threshold_residue"
            if self._contains_any(low, ["stayed inside the elevator", "rode straight to the lobby", "rode down to the lobby"]):
                return "elevator_descending_residue"
            if "call button" in low:
                return "elevator_button_residue"
            return "elevator_indicator_residue"
        if scene_kind == "room_blackout":
            if "exit sign" in low:
                return "wrong_exit_residue"
            return "blackout_room_residue"
        if scene_kind == "hallway_mirror":
            return "silent_hallway_residue"
        if scene_kind == "door_confession":
            return "door_latch_residue"
        if scene_kind == "note_threshold":
            return "threshold_residue"
        if self._contains_any(low, ["silent", "empty", "no one answered"]):
            return "silent_room_residue"
        return "generic_residue"

    def _aftershock_prompt_parts(
        self,
        aftershock_type: str,
        beat_text: str,
        scene_profile: dict[str, Any],
        anomaly_object: str,
        clue: str,
    ) -> tuple[str, str, str, str]:
        _ = beat_text, scene_profile
        if aftershock_type == "elevator_descending_residue":
            return (
                "an empty elevator cab descending toward lobby level",
                "static interior hold on the empty cab as the floor display drops",
                "flat cab practicals with steady panel glow",
                "the empty cab keeps descending after the decision",
            )
        if aftershock_type == "elevator_button_residue":
            return (
                "the call button panel beside closed elevator doors",
                "tight hold on the call button glow with no one in frame",
                "cold lobby practicals with panel glow as the only pulse",
                "the call button glow remains after the doors close",
            )
        if aftershock_type == "elevator_threshold_residue":
            return (
                "the doormat left at the wrong-floor threshold",
                "static threshold frame with the doormat centered and no movement",
                "sterile lobby spill across the doormat and landing seam",
                "the doormat is still there after the ride down",
            )
        if aftershock_type == "elevator_indicator_residue":
            return (
                "closed elevator doors with the wrong floor still displayed",
                "held close frame on the floor indicator after the doors seal",
                "cold panel glow with the lobby edges falling off to shadow",
                "the wrong floor remains on the display after the doors close",
            )
        if aftershock_type == "wrong_exit_residue":
            return (
                "an emptied blackout room with the wrong-way exit sign still glowing",
                "static wide room hold after movement and decisions are over",
                "weak emergency glow anchored on the mispointed exit sign",
                "the exit sign still points the wrong way",
            )
        if aftershock_type == "blackout_room_residue":
            return (
                "the blackout room left empty after the scramble",
                "static room frame with vacant positions and no movement",
                "faint emergency spill with drained contrast",
                "the room stays wrong after everyone moves",
            )
        if aftershock_type == "silent_hallway_residue":
            return (
                "the hallway mirror after the movement stops",
                "held hallway frame with mirror depth and no visible body",
                "low hallway practicals with reflections now unmoving",
                "the hallway stays silent after the retreat",
            )
        if aftershock_type == "door_latch_residue":
            return (
                "the closed door and latch in complete stillness",
                "held frame on the seam after footsteps fade",
                "dim side spill on latch metal with no speaker present",
                "the whisper is gone but the latch remains",
            )
        if aftershock_type == "threshold_residue":
            return (
                "the threshold after the decision, with the clue left behind",
                "static frame on floor line and door base after retreat",
                "low practical spill across the abandoned clue",
                "the clue remains where it was lifted",
            )
        if aftershock_type == "silent_room_residue":
            return (
                "an empty room holding the silence after the decision",
                "static wide room frame with no movement anywhere",
                "weak ambient light and long still shadows",
                "silence remains after the final action",
            )
        if anomaly_object and self._is_unknown_object_grounding_scene(scene_profile):
            object_subject, object_framing, object_clue = self._object_priority_prompt_parts(
                anomaly_object=anomaly_object,
                beat="aftershock",
                scene_profile=scene_profile,
                clue=clue,
            )
            return (
                object_subject,
                object_framing,
                "low ambient spill preserving the last physical cue",
                object_clue,
            )
        return (
            "the emptied scene with one remaining physical cue",
            "held frame on that remaining cue right after the choice",
            "low ambient spill preserving the cue's texture and edges",
            clue,
        )

    def _subject_for_prompt(
        self,
        beat: str,
        beat_text: str,
        scene_anchor: str,
        story_details: dict[str, Any],
        scene_profile: dict[str, Any],
    ) -> str:
        low = str(beat_text or "").lower()
        objects = [str(item) for item in story_details.get("objects", [])]
        actions = [str(item) for item in story_details.get("actions", [])]
        has_note = "note" in objects
        has_door = "door" in objects
        has_shoe = "shoe" in objects
        has_phone = "phone" in objects or "called" in actions
        has_mirror = "mirror" in low or "mirror" in objects
        has_latch = "latch" in low or "handle" in objects
        allow_hallway = bool(scene_profile.get("allow_hallway", False))
        scene_kind = str(scene_profile.get("kind", "generic_interior"))

        if beat == "hook_image" and has_note and has_door:
            return "the note emerging under the door"
        if beat == "setup" and has_note and has_shoe:
            return "the note resting against a shoe at the threshold"
        if beat == "strange_detail" and has_note and has_door:
            return "a folded note near the door seam"
        if beat == "escalation" and has_note and has_door:
            return "a hand lifting the note near the doorway"
        if beat == "strongest_reveal" and ("name" in objects or "note" in objects):
            return "a hand opening the note to the key line"
        if beat == "consequence_ending" and has_phone and has_note and has_door:
            return "a person backing away with a phone in hand, the note still by the doorway"
        if beat == "consequence_ending" and has_phone:
            return "a person stepping back with a phone raised at chest level"
        if beat == "escalation" and has_mirror and allow_hallway:
            return "the hallway mirror line where movement keeps appearing"
        if beat == "escalation" and has_latch and has_door:
            return "the latch and door seam at listening distance"
        if beat == "escalation" and scene_kind == "room_blackout":
            return "the room positions in the split second after blackout"
        return scene_anchor.split(",")[0].strip()

    def _story_clue_for_beat(self, beat: str, beat_text: str, story_details: dict[str, Any]) -> str:
        _ = beat, story_details
        return self._trim_text_words(str(beat_text or "").strip(), max_words=18)

    def _prompt_clue_text(
        self,
        beat: str,
        beat_text: str,
        story_details: dict[str, Any],
        scene_profile: dict[str, Any],
    ) -> str:
        low = str(beat_text or "").lower()
        objects = {str(item).lower() for item in story_details.get("objects", [])}
        actions = {str(item).lower() for item in story_details.get("actions", [])}
        allow_hallway = bool(scene_profile.get("allow_hallway", False))
        allow_door = bool(scene_profile.get("allow_door", False))
        scene_kind = str(scene_profile.get("kind", "generic_interior"))
        clue = self._trim_text_words(beat_text, max_words=16)

        if beat == "hook_image" and "under my door" in low and "note" in low:
            return "the note is caught mid-slide under the door"
        if beat == "setup" and "shoe" in low and "stopped" in low:
            return "it has stopped at the shoe instead of crossing the room"
        if beat == "strange_detail" and ("silent" in low or "dead silent" in low):
            if allow_hallway:
                return "the hallway drops into sudden silence as the note is picked up"
            if scene_kind == "room_blackout":
                return "the room drops into silence right after the lights fail"
            return "everything goes silent the second the detail appears"
        if beat == "escalation" and "note" in objects and ("silent" in low or "hallway" in objects):
            if allow_hallway:
                return "the note is lifted but not unfolded while the hallway stays silent"
            if scene_kind == "room_blackout":
                return "the note is lifted slowly in the blackout without unfolding it"
            return "the note is held in place while everyone stays silent"
        if beat == "strongest_reveal" and ("nickname" in low or "only one person" in low):
            if "note" in objects or "note" in low:
                return "the old nickname is readable on the unfolded note"
            return "the old nickname is heard from the wrong location"
        if beat == "strongest_reveal" and self._line_has_reflection_anomaly(low):
            return "the reflection timing lags behind the real movement"
        if beat == "consequence_ending":
            has_phone = "phone" in objects or "called" in actions or "called" in low
            has_note = "note" in objects or "note" in low
            has_door = "door" in objects or "doorway" in low or "door" in low
            if self._contains_any(low, ["mirror in my phone", "phone mirror", "checked the mirror"]):
                return "the phone mirror check happens while keeping distance from the door"
            if self._contains_any(low, ["waited outside until sunrise", "until sunrise"]):
                return "the wait continues outside until sunrise"
            if self._contains_any(low, ["never opened", "didn't open", "did not open"]):
                return "the door stays closed all night"
            if has_phone and has_note and has_door and allow_door:
                return "phone in hand while backing away from the doorway, the note still visible by the threshold"
            if has_phone and has_door and allow_door:
                return "phone in hand as they back away from the doorway without turning their back"
            if "backed away" in low:
                return "the retreat begins before the next move is decided"

        if clue:
            first = clue[0].lower() + clue[1:] if len(clue) > 1 else clue.lower()
            return first
        return "the silence shifts in a way that feels wrong"

    def _pick_beat_phrase(
        self,
        beat_map: dict[str, list[str]],
        beat: str,
        shot_index: int,
        fallback: list[str],
    ) -> str:
        options = beat_map.get(beat) or []
        if options:
            return options[(shot_index + len(beat)) % len(options)]
        return fallback[shot_index % len(fallback)]

    def _pick_scene_beat_phrase(
        self,
        beat: str,
        shot_index: int,
        scene_profile: dict[str, Any],
        base_map: dict[str, list[str]],
        scene_map: dict[str, dict[str, list[str]]],
        fallback: list[str],
    ) -> str:
        scene_kind = str(scene_profile.get("kind", "generic_interior"))
        scene_options = scene_map.get(scene_kind, {}).get(beat) or []
        if scene_options:
            return scene_options[(shot_index + len(beat)) % len(scene_options)]
        return self._pick_beat_phrase(base_map, beat, shot_index, fallback=fallback)

    def _lock_phrase_to_scene(self, phrase: str, scene_profile: dict[str, Any]) -> str:
        text = str(phrase or "")
        allow_hallway = bool(scene_profile.get("allow_hallway", False))
        allow_door = bool(scene_profile.get("allow_door", False))
        allow_mirror = bool(scene_profile.get("allow_mirror", False))
        allow_elevator = bool(scene_profile.get("allow_elevator", False))
        scene_kind = str(scene_profile.get("kind", "generic_interior"))

        hallway_target = "elevator lobby" if scene_kind == "elevator_lobby" else "room"
        if not allow_hallway:
            text = re.sub(r"\bhallway\b", hallway_target, text, flags=re.IGNORECASE)
            text = re.sub(r"\bcorridor\b", hallway_target, text, flags=re.IGNORECASE)
        if not allow_door:
            doorway_target = "elevator threshold" if scene_kind == "elevator_lobby" else "room edge"
            door_target = "elevator doors" if scene_kind == "elevator_lobby" else "room"
            threshold_target = "cab floor line" if scene_kind == "elevator_lobby" else "floor line"
            text = re.sub(r"\bdoorway\b", doorway_target, text, flags=re.IGNORECASE)
            text = re.sub(r"\bdoor\b", door_target, text, flags=re.IGNORECASE)
            text = re.sub(r"\bthreshold\b", threshold_target, text, flags=re.IGNORECASE)
        if not allow_mirror:
            text = re.sub(r"\bphone mirror\b", "phone_screen_reflection", text, flags=re.IGNORECASE)
            text = re.sub(r"\bmirror in my phone\b", "phone_screen_reflection", text, flags=re.IGNORECASE)
            text = re.sub(r"\bmirror\b", "panel", text, flags=re.IGNORECASE)
            text = re.sub(r"\breflection\b", "surface", text, flags=re.IGNORECASE)
            text = re.sub(r"\bphone_screen_surface\b", "phone_screen_reflection", text, flags=re.IGNORECASE)
            text = re.sub(r"\bphone_screen_reflection\b", "phone mirror", text, flags=re.IGNORECASE)
        if not allow_elevator:
            text = re.sub(r"\belevator\b", "hall", text, flags=re.IGNORECASE)
            text = re.sub(r"\bcab\b", "hall", text, flags=re.IGNORECASE)
            text = re.sub(r"\blobby\b", "hallway", text, flags=re.IGNORECASE)
            text = re.sub(r"\bfloor indicator\b", "wall sign", text, flags=re.IGNORECASE)
            text = re.sub(r"\bcall button\b", "panel button", text, flags=re.IGNORECASE)
            text = re.sub(r"\bwrong floor\b", "wrong level", text, flags=re.IGNORECASE)
        if scene_kind == "elevator_lobby":
            text = re.sub(r"\broom\b", "elevator cab", text, flags=re.IGNORECASE)
            text = re.sub(r"\bhallway\b", "elevator lobby", text, flags=re.IGNORECASE)
            text = re.sub(r"\bcorridor\b", "elevator lobby", text, flags=re.IGNORECASE)
            text = re.sub(r"\bdoorway\b", "elevator threshold", text, flags=re.IGNORECASE)
            text = re.sub(r"\bdoor\b", "elevator doors", text, flags=re.IGNORECASE)
        if scene_kind == "room_blackout":
            text = re.sub(r"\bapartment\b", "blackout room", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip()

    def _lock_phrase_to_story_entities(self, phrase: str, story_details: dict[str, Any]) -> str:
        text = str(phrase or "")
        objects = {str(item).lower() for item in story_details.get("objects", [])}
        actions = {str(item).lower() for item in story_details.get("actions", [])}

        if "note" not in objects:
            text = re.sub(r"\bnote\b", "detail", text, flags=re.IGNORECASE)
        if "shoe" not in objects:
            text = re.sub(r"\bshoe\b", "floor", text, flags=re.IGNORECASE)
        if "apartment" not in objects:
            text = re.sub(r"\bapartment\b", "interior", text, flags=re.IGNORECASE)
        preserve_phone_reference = bool(re.search(r"\bphone\b", text, flags=re.IGNORECASE)) and bool(
            re.search(r"\bmirror\b|\bclock\b|\bscreen\b", text, flags=re.IGNORECASE)
        )
        if "phone" not in objects and "called" not in actions and not preserve_phone_reference:
            text = re.sub(r"\bphone\b", "hand", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip()

    def _trim_text_words(self, text: str, max_words: int) -> str:
        words = re.findall(r"[A-Za-z0-9']+", str(text or ""))
        if not words:
            return "the silence changes all at once"
        if len(words) <= max_words:
            return " ".join(words)

        clipped = words[:max_words]
        cursor = max_words
        max_extension = min(len(words), max_words + 4)
        while cursor < max_extension and self._has_incomplete_tail_words(clipped):
            clipped.append(words[cursor])
            cursor += 1

        while self._has_incomplete_tail_words(clipped) and len(clipped) > 3:
            clipped.pop()

        return " ".join(clipped)

    def _build_on_screen_text(
        self,
        beat: str,
        beat_text: str,
        shot_index: int,
        story_details: dict[str, Any],
    ) -> str:
        candidate = self._caption_from_beat_intent(
            beat=beat,
            beat_text=beat_text,
            story_details=story_details,
        )
        if not candidate:
            candidate = self._caption_from_patterns(beat_text)
        if not candidate:
            candidate = self._caption_from_beat(beat=beat, beat_text=beat_text, story_details=story_details)
        return self._finalize_caption(candidate=candidate, beat=beat, shot_index=shot_index)

    def _caption_from_beat_intent(self, beat: str, beat_text: str, story_details: dict[str, Any]) -> str:
        variants = self._caption_variants_for_beat(beat=beat, beat_text=beat_text, story_details=story_details)
        return variants[0] if variants else ""

    def _reflection_caption_variants(self, text: str) -> list[str]:
        low = str(text or "").lower()
        if "turned before i did" in low:
            return ["It turned before I did", "The mirror turned first"]
        if self._contains_any(low, ["one second after mine", "lifted its hand"]):
            return ["My reflection moved late", "Its hand moved one beat late"]
        if "blinked late" in low:
            return ["My reflection blinked late", "The mirror blinked late"]
        if self._contains_any(low, ["kept moving", "version kept moving"]):
            return ["The mirror kept moving", "It kept moving after me"]
        if self._contains_any(low, ["didn't match", "did not match"]):
            return ["The reflection did not match", "My reflection moved wrong"]
        if self._line_has_reflection_anomaly(low):
            return ["My reflection moved wrong", "The mirror moved out of sync"]
        return []

    def _caption_variants_for_beat(self, beat: str, beat_text: str, story_details: dict[str, Any]) -> list[str]:
        low = str(beat_text or "").lower()
        objects = {str(item).lower() for item in story_details.get("objects", [])}
        variants: list[str] = []

        if beat == "hook_image":
            if self._contains_any(low, ["exit sign", "exit arrow"]) and self._contains_any(
                low,
                ["wrong way", "wrong direction", "pointed", "different direction", "each arrow"],
            ):
                return ["The exit sign pointed wrong", "The arrows pointed different ways"]
            reflection_variants = self._reflection_caption_variants(low)
            if reflection_variants:
                return reflection_variants
            if self._contains_any(low, ["elevator", "wrong floor", "lobby", "floor indicator"]):
                return ["The elevator opened wrong", "The floor number did not match"]
            if "lights went out" in low or ("room dropped" in low and "black" in low):
                return ["The room dropped to black", "Lights cut out at once"]
            if "mirror" in low and self._contains_any(low, ["movement", "moved", "reflection"]):
                return ["Movement flashed in the mirror", "The mirror caught wrong movement"]
            if "confession" in low and self._contains_any(low, ["hallway", "door", "latch"]):
                return ["A hallway confession hit my door", "A whisper reached my door"]
            if "under my door" in low and "note" in low:
                return ["A note slid under my door", "A note appeared at my door"]
            if "latch" in low:
                return ["The latch moved by itself", "The latch moved after the whisper"]
            return ["Something felt wrong immediately", "The first frame felt wrong"]

        if beat == "setup":
            if self._contains_any(low, ["elevator", "wrong floor", "lobby"]):
                return ["The elevator opened wrong", "The floor number did not match"]
            if "watched the hallway mirror" in low:
                return ["I watched the hallway mirror", "I stayed by the mirror"]
            if self._contains_any(low, ["stopped outside the door", "outside the door"]):
                return ["I stopped outside the door", "I stayed outside the door"]
            if "one voice" in low and "door" in low:
                return ["One voice came from the door", "A voice came from the door"]
            if "at first" in low or "normal" in low:
                return ["At first it felt normal", "Everything still felt normal"]
            grounded = self._grounded_caption_from_source_line(beat_text)
            if grounded:
                return [grounded, "I held my place and watched"]
            return ["I held my place and watched", "I stayed and listened first"]

        if beat == "strange_detail":
            reflection_variants = self._reflection_caption_variants(low)
            if reflection_variants:
                return reflection_variants
            if self._contains_any(low, ["panel showed my floor", "different number", "wrong floor", "floor indicator"]):
                return ["The floor number did not match", "The panel number stayed wrong"]
            if "movement at the edge of the doorframe" in low and "without a body" in low:
                return ["Movement appeared without a body", "The doorframe moved without anyone"]
            if "brushed my shoulder" in low:
                return ["Something brushed my shoulder", "A touch came from nowhere"]
            if "same pause before my name" in low:
                return ["The same pause before my name", "It paused before my name again"]
            if "stopped" in low and "shoe" in low:
                return ["It stopped at my shoe", "It stopped right at my shoe"]
            if "silent" in low:
                return ["Everything went dead silent", "The silence hit too fast"]
            return ["One detail felt impossible", "One detail broke the scene"]

        if beat == "escalation":
            reflection_variants = self._reflection_caption_variants(low)
            if reflection_variants:
                return reflection_variants
            if self._contains_any(low, ["call button"]) and self._contains_any(low, ["lit", "before anyone", "stepped out"]):
                return ["The call button lit first", "The button lit before anyone"]
            if (
                self._contains_any(low, ["stayed inside the elevator", "rode straight to the lobby", "rode down to the lobby"])
                or ("elevator" in low and "lobby" in low and "stayed" in low)
            ):
                return ["I stayed in the elevator", "I rode down to the lobby"]
            if self._contains_any(low, ["elevator", "wrong floor", "lobby", "indicator"]):
                return ["I waited as floors mismatched", "The elevator kept stopping wrong"]
            if "kept my eyes on the mirror" in low:
                return ["I kept staring at the mirror", "I could not stop watching"]
            if "listened at the latch" in low:
                return ["I listened at the latch", "I stayed at the latch"]
            if "blackout lasted three seconds" in low or ("power flashed back" in low and "changed places" in low):
                return ["Three seconds and everyone moved", "Power flashed back everyone moved"]
            if "lifted the note" in low or ("note" in objects and "held" in low):
                return ["I held the note shut", "I held the note and waited"]
            if "silent" in low:
                return ["Everything went quiet too fast", "The silence tightened around me"]
            grounded = self._grounded_caption_from_source_line(beat_text)
            if grounded:
                return [grounded, "Pressure built and I froze"]
            return ["Pressure built and I froze", "I froze as pressure built"]

        if beat == "strongest_reveal":
            reflection_variants = self._reflection_caption_variants(low)
            if reflection_variants:
                return reflection_variants
            if self._contains_any(low, ["call button"]) and self._contains_any(low, ["lit", "before anyone", "stepped out"]):
                return ["The call button lit first", "The button lit before anyone"]
            if self._contains_any(low, ["corridor air felt colder", "air felt colder"]):
                return ["The corridor air turned cold", "The air changed outside the cab"]
            if self._contains_any(low, ["wrong floor", "same empty floor", "different number"]):
                return ["The same wrong floor returned", "The floor number changed again"]
            if "wrong person was in my spot" in low:
                return ["The wrong person took my spot", "Someone else stood in my place"]
            if self._contains_any(low, ["voice repeated", "old nickname", "nickname no one has used", "through my speaker at the same time"]):
                return ["The old nickname came again", "The voice used the old nickname"]
            if "door latch clicked" in low and "whisper" in low:
                return ["The latch clicked with the whisper", "The whisper came from the seam"]
            if self._contains_any(low, ["shadow moved back", "reflection moved", "movement"]) and "mirror" in low:
                return ["The reflection moved on its own", "The mirror moved without a body"]
            if "shadow moved back" in low:
                return ["The shadow moved back at me", "The shadow moved toward me"]
            if "only one person" in low and self._contains_any(low, ["name", "nickname"]):
                return ["Only one person knew that name", "Only one person knew that nickname"]
            if "nickname" in low:
                return ["It used the old nickname", "The old nickname was there"]
            return ["That reveal changed everything", "The reveal made no sense"]

        if beat == "consequence_ending":
            if self._contains_any(low, ["mirror in my phone", "phone mirror", "checked the mirror"]):
                return ["I checked the mirror in my phone", "I checked my phone mirror first"]
            if self._contains_any(low, ["waited outside until sunrise", "until sunrise"]):
                return ["I waited outside until sunrise", "I stayed outside until first light"]
            if self._contains_any(low, ["never opened", "didn't open", "did not open"]):
                return ["I never opened that door", "The door stayed closed all night"]
            if (
                self._contains_any(low, ["stayed inside the elevator", "rode straight to the lobby", "rode down to the lobby"])
                or ("elevator" in low and "lobby" in low and "stayed" in low)
            ):
                return ["I stayed in the elevator", "I rode straight to the lobby"]
            if self._contains_any(low, ["ran for the stairs", "stairs"]):
                return ["I ran for the stairs", "I ran before looking back"]
            if "called out" in low and "no one answered" in low:
                return ["I called out no one answered", "No one answered when I called"]
            if self._contains_any(low, ["called the police", "called security", "called my brother"]):
                return ["I called for help and ran", "I called before I left"]
            if self._contains_any(low, ["left the hallway", "backed away", "walked outside"]):
                return ["I left without looking back", "I backed away and left"]
            if "exit sign" in low and self._contains_any(low, ["ran", "left"]):
                return ["I ran for the hallway", "I ran toward the exit"]
            grounded = self._grounded_caption_from_source_line(beat_text)
            if grounded:
                return [grounded, "I chose to leave immediately"]
            return ["I chose to leave immediately", "I acted and got out"]

        if beat == "aftershock":
            if "doormat" in low:
                return ["My doormat stayed on that floor", "The doormat stayed at that threshold"]
            if self._contains_any(low, ["stayed inside the elevator", "rode straight to the lobby", "rode down to the lobby"]):
                return ["The empty cab rode to lobby", "The cab descended in silence"]
            if "call button" in low:
                return ["The call button stayed lit", "The button glow stayed on"]
            if self._contains_any(low, ["wrong floor", "floor indicator", "floor panel", "panel"]):
                return ["The wrong floor still showed", "The panel still showed wrong floor"]
            if "exit sign" in low and "wrong way" in low:
                return ["The exit sign still pointed wrong", "The wrong exit sign kept glowing"]
            if "hallway" in low and "silent" in low:
                return ["The hallway stayed dead silent", "The silence stayed in the hallway"]
            if "mirror" in low:
                return ["The mirror stayed still afterward", "The mirror kept the silence"]
            if "latch" in low or "door" in low:
                return ["The latch stayed still afterward", "The door stayed closed and quiet"]
            return ["The scene stayed wrong afterward", "The silence stayed after I left"]

        if beat == "final_echo":
            return ["I never forgot that frame", "That image still follows me"]

        return variants

    def _caption_from_patterns(self, beat_text: str) -> str:
        low = str(beat_text or "").lower()
        reflection_variants = self._reflection_caption_variants(low)
        if reflection_variants:
            return reflection_variants[0]
        if self._contains_any(low, ["exit sign", "exit arrow"]) and self._contains_any(
            low,
            ["wrong way", "wrong direction", "pointed", "different direction", "each arrow"],
        ):
            return "The exit sign pointed wrong"
        if "movement at the edge of the doorframe" in low:
            return "Movement at the doorframe edge"
        if "lights went out" in low:
            return "The lights went out"
        if "exit sign" in low:
            return "Only the exit sign glowed"
        if "power flashed back" in low and "changed places" in low:
            return "Power flashed back, everyone moved"
        if "call button" in low and self._contains_any(low, ["lit", "before anyone", "stepped out"]):
            return "The call button lit first"
        if self._contains_any(low, ["voice repeated", "old nickname", "through my speaker at the same time"]):
            return "The voice used the old nickname"
        if self._contains_any(low, ["checked the mirror in my phone", "phone mirror"]):
            return "I checked the mirror in my phone"
        if self._contains_any(low, ["waited outside until sunrise", "until sunrise"]):
            return "I waited outside until sunrise"
        if self._contains_any(low, ["never opened", "didn't open", "did not open"]):
            return "I never opened that door"
        if self._contains_any(low, ["stayed inside the elevator", "rode straight to the lobby", "rode down to the lobby"]):
            return "I stayed in the elevator"
        if "wrong person was in my spot" in low:
            return "The wrong person took my spot"
        if "mirror" in low and ("moved" in low or "reflection" in low):
            return "Something moved in the mirror"
        if "kept my eyes on the mirror" in low:
            return "I kept my eyes on the mirror"
        if "i stayed by the doorframe and watched the hallway mirror" in low:
            return "I watched the hallway mirror"
        if "confession" in low and ("door" in low or "outside" in low):
            return "A confession waited at my door"
        if "same pause before my name" in low:
            return "The same pause before my name"
        if "one sentence repeated" in low:
            return "The same sentence repeated"
        if "under my door" in low and "note" in low:
            return "A note slid under my door"
        if "stopped" in low and "shoe" in low:
            return "It stopped at my shoe"
        if "went silent" in low or "dead silent" in low:
            if "hallway" in low or "corridor" in low:
                return "The hallway went dead silent"
            if "room" in low or "blackout" in low:
                return "The room went dead silent"
            return "Everything went dead silent"
        if "lifted the note" in low and "silent" in low:
            return "I lifted the note and froze"
        if "held the note" in low and "before i unfolded it" in low:
            return "I held the note and paused"
        if "paused in the doorway" in low:
            return "I paused in the doorway"
        if "listened at the latch without opening the door" in low:
            return "I listened at the latch"
        if "called out" in low and "no one answered" in low:
            return "I called out, no answer"
        if "only one person" in low and ("nickname" in low or "name" in low):
            return "Only one person knew that name"
        if "nickname" in low:
            return "It used the old nickname"
        if "every face" in low or "everyone" in low and "turned" in low:
            return "Every face turned toward me"
        if "door latch clicked" in low and "whispered" in low:
            return "The latch clicked with the whisper"
        if "called" in low and "brother" in low:
            return "I called my brother immediately"
        if "called security" in low:
            return "I called security before sunrise"
        if "backed away" in low and "door" in low:
            return "I backed away from the door"
        if "glass" in low and "crack" in low:
            return "The glass cracked by itself"
        return ""

    def _caption_from_beat(self, beat: str, beat_text: str, story_details: dict[str, Any]) -> str:
        _ = story_details
        cleaned = re.sub(r"\s+", " ", str(beat_text or "").strip(" .,:;!?"))
        raw_words = re.findall(r"[A-Za-z0-9']+", cleaned)
        if 3 <= len(raw_words) <= 7 and not self._looks_fragment(raw_words):
            return " ".join(raw_words)

        clause_caption = self._best_caption_clause(cleaned)
        clause_words = re.findall(r"[A-Za-z0-9']+", clause_caption)
        if 3 <= len(clause_words) <= 7 and not self._looks_fragment(clause_words):
            return clause_caption
        compressed = self._compress_caption_from_line(cleaned)
        compressed_words = re.findall(r"[A-Za-z0-9']+", compressed)
        if 3 <= len(compressed_words) <= 7 and not self._looks_fragment(compressed_words):
            return compressed

        key_terms = [
            token.lower()
            for token in re.findall(r"[A-Za-z0-9']+", str(beat_text or ""))
            if len(token) > 2 and token.lower() not in self._STOPWORDS
        ]
        lead = " ".join(key_terms[:2]).strip() or "this moment"

        defaults = {
            "hook_image": "Something slid under my door",
            "setup": "Everything felt normal at first",
            "strange_detail": "Then one detail felt wrong",
            "escalation": "The pressure rose too fast",
            "strongest_reveal": "The reveal made no sense",
            "consequence_ending": "I made the call and left",
            "aftershock": "The wrong floor still showed",
            "final_echo": "I never went back inside",
        }
        templates = {
            "hook_image": f"{lead} changed everything",
            "setup": f"{lead} still felt normal",
            "strange_detail": f"{lead} felt wrong instantly",
            "escalation": f"I froze as {lead} built",
            "strongest_reveal": f"{lead} changed everything",
            "consequence_ending": f"I acted after {lead}",
            "aftershock": f"{lead} still remained",
            "final_echo": f"{lead} never left",
        }
        return templates.get(beat, defaults.get(beat, f"Beat {beat.replace('_', ' ')}"))

    def _compress_caption_from_line(self, text: str) -> str:
        low = str(text or "").lower()
        if "movement at the edge of the doorframe" in low:
            return "Movement at the doorframe edge"
        if "wrong person was in my spot" in low:
            return "The wrong person took my spot"
        if "called out but no one answered" in low:
            return "I called out, no answer"
        if "same pause before my name" in low:
            return "The same pause before my name"
        if "call button" in low and self._contains_any(low, ["lit", "before anyone", "stepped out"]):
            return "The call button lit first"
        if self._contains_any(low, ["stayed inside the elevator", "rode straight to the lobby", "rode down to the lobby"]):
            return "I stayed in the elevator"

        words = re.findall(r"[A-Za-z0-9']+", str(text or ""))
        if len(words) < 3:
            return ""
        compressed: list[str] = []
        for idx, word in enumerate(words):
            low_word = word.lower()
            if idx not in (0, len(words) - 1) and low_word in self._STOPWORDS:
                continue
            compressed.append(word)
        if len(compressed) > 7:
            compressed = compressed[:7]
        while compressed and (compressed[-1].lower() in self._STOPWORDS or self._looks_fragment(compressed)) and len(compressed) > 3:
            compressed.pop()
        if 3 <= len(compressed) <= 7 and not self._looks_fragment(compressed):
            return " ".join(compressed)
        return ""

    def _finalize_caption(self, candidate: str, beat: str, shot_index: int) -> str:
        cleaned = re.sub(r"\s+", " ", str(candidate or "").replace("\n", " ")).strip(" .,:;!?")
        cleaned = self._strip_discourse_openers(cleaned)
        words = re.findall(r"[A-Za-z0-9']+", cleaned)

        if len(words) < 3:
            fallback = self._caption_from_beat(beat=beat, beat_text="", story_details={})
            cleaned = fallback
            words = re.findall(r"[A-Za-z0-9']+", cleaned)

        if len(words) > 7:
            compressed = self._caption_from_beat(beat=beat, beat_text=cleaned, story_details={})
            words = re.findall(r"[A-Za-z0-9']+", compressed)
            if len(words) > 7:
                words = words[:7]
            while words and (words[-1].lower() in self._STOPWORDS or self._looks_fragment(words)) and len(words) > 3:
                words.pop()
            cleaned = " ".join(words)
        elif len(words) >= 3:
            while words and (words[-1].lower() in self._STOPWORDS or self._looks_fragment(words)) and len(words) > 3:
                words.pop()
            cleaned = " ".join(words)

        if not cleaned:
            cleaned = f"Beat {shot_index + 1} moment"
        final_words = re.findall(r"[A-Za-z0-9']+", cleaned)
        if self._looks_fragment(final_words):
            repaired = self._caption_from_beat(beat=beat, beat_text="", story_details={})
            repaired_words = re.findall(r"[A-Za-z0-9']+", repaired)
            if 3 <= len(repaired_words) <= 7 and not self._looks_fragment(repaired_words):
                cleaned = repaired
        cleaned = self._strip_discourse_openers(cleaned)
        return cleaned[0].upper() + cleaned[1:] if len(cleaned) > 1 else cleaned.upper()

    def _strip_discourse_openers(self, text: str) -> str:
        words = re.findall(r"[A-Za-z0-9']+", str(text or ""))
        if len(words) >= 4 and words[0].lower() in {"then", "and", "but"}:
            words = words[1:]
        return " ".join(words)

    def _ensure_adjacent_caption_uniqueness(
        self,
        beat: str,
        beat_text: str,
        shot_index: int,
        story_details: dict[str, Any],
        caption: str,
        previous_caption: str,
    ) -> str:
        current = self._finalize_caption(candidate=caption, beat=beat, shot_index=shot_index)
        if not previous_caption or not self._captions_too_similar(previous_caption, current):
            return current

        for candidate in self._caption_variants_for_beat(beat=beat, beat_text=beat_text, story_details=story_details):
            regenerated = self._finalize_caption(candidate=candidate, beat=beat, shot_index=shot_index)
            if regenerated and not self._captions_too_similar(previous_caption, regenerated):
                return regenerated

        regenerated = self._finalize_caption(
            candidate=self._caption_from_beat(beat=beat, beat_text=beat_text, story_details=story_details),
            beat=beat,
            shot_index=shot_index,
        )
        if regenerated and not self._captions_too_similar(previous_caption, regenerated):
            return regenerated

        forced_by_beat = {
            "consequence_ending": "I chose to get out",
            "aftershock": "The wrong floor still showed",
            "strongest_reveal": "That reveal changed everything",
        }
        forced = forced_by_beat.get(beat, f"Beat {shot_index + 1} shifted")
        return self._finalize_caption(candidate=forced, beat=beat, shot_index=shot_index)

    def _captions_too_similar(self, left: str, right: str) -> bool:
        left_clean = re.sub(r"\s+", " ", str(left or "").strip().lower())
        right_clean = re.sub(r"\s+", " ", str(right or "").strip().lower())
        if not left_clean or not right_clean:
            return False
        if left_clean == right_clean:
            return True

        left_tokens = self._meaningful_tokens(left_clean)
        right_tokens = self._meaningful_tokens(right_clean)
        if not left_tokens or not right_tokens:
            return left_clean == right_clean
        overlap = left_tokens.intersection(right_tokens)
        if overlap == left_tokens or overlap == right_tokens:
            return True
        union = left_tokens.union(right_tokens)
        if not union:
            return False
        return (len(overlap) / len(union)) >= 0.7

    def _looks_fragment(self, words: list[str]) -> bool:
        if not words:
            return True
        bad_tail = {
            "and",
            "but",
            "or",
            "to",
            "with",
            "while",
            "before",
            "after",
            "until",
            "than",
            "as",
            "the",
            "that",
            "which",
            "toward",
            "into",
            "behind",
            "near",
            "ended",
            "using",
            "without",
            "someone",
            "something",
        }
        if words[-1].lower() in bad_tail:
            return True
        if len(words) >= 2 and words[-2].lower() in {"wasn't", "isn't", "weren't"}:
            return True
        if words[0].lower() in {"in", "at", "on", "near", "by", "after", "before", "while", "then"} and len(words) <= 4:
            return True
        return False

    def _best_caption_clause(self, text: str) -> str:
        clauses = [
            segment.strip()
            for segment in re.split(r"[;,.]|\\band\\b|\\bbut\\b|\\bwhile\\b|\\bwhen\\b|\\bthen\\b", str(text or ""), flags=re.IGNORECASE)
            if segment.strip()
        ]
        for clause in clauses:
            words = re.findall(r"[A-Za-z0-9']+", clause)
            if 3 <= len(words) <= 7 and not self._looks_fragment(words):
                return " ".join(words)
            if len(words) > 7:
                clipped = words[:6]
                while clipped and (
                    clipped[-1].lower() in self._STOPWORDS or self._looks_fragment(clipped)
                ) and len(clipped) > 3:
                    clipped.pop()
                if 3 <= len(clipped) <= 7 and not self._looks_fragment(clipped):
                    return " ".join(clipped)
        return ""

    def _enforce_shot_semantic_consistency(
        self,
        beat: str,
        source_text: str,
        visual_goal: str,
        on_screen_text: str,
        prompt_text: str,
        story_details: dict[str, Any],
        script_text: str,
        scene_anchor: str,
        shot_index: int,
        scene_profile: dict[str, Any],
    ) -> tuple[str, str, str]:
        sentence_corpus = self._split_sentences(script_text)
        normalized_source = self._normalize_source_text(source_text, sentence_corpus=sentence_corpus)
        if not normalized_source and sentence_corpus:
            normalized_source = self._normalize_source_text(sentence_corpus[0], sentence_corpus=sentence_corpus)
        source_for_guard = normalized_source or self._normalize_source_text(source_text, sentence_corpus=[])

        attempt = 0
        current_visual = visual_goal
        current_on_screen = on_screen_text
        current_prompt = prompt_text

        while attempt < 2:
            issues: list[str] = []
            if self._has_foreign_entities(current_visual, story_details):
                issues.append("foreign_entity_in_visual_goal")
            if not self._is_on_screen_aligned_for_beat(
                beat=beat,
                source_text=source_for_guard,
                on_screen_text=current_on_screen,
                script_text=script_text,
                story_details=story_details,
            ):
                issues.append("on_screen_mismatch")
            if self._introduces_foreign_reveal(current_prompt, script_text):
                issues.append("foreign_reveal_in_prompt")
            if self._has_foreign_entities(current_prompt, story_details):
                issues.append("foreign_entity_in_prompt")

            if not issues:
                return current_visual, current_on_screen, current_prompt

            # Reject current shot text and regenerate strictly from the same source sentence.
            current_visual = self._safe_visual_goal_from_source(beat=beat, source_text=source_for_guard)
            current_on_screen = self._safe_on_screen_from_source(
                beat=beat,
                source_text=source_for_guard,
                shot_index=shot_index,
            )
            current_prompt = self._safe_prompt_from_source(
                beat=beat,
                source_text=source_for_guard,
                scene_anchor=scene_anchor,
                shot_index=shot_index,
                scene_profile=scene_profile,
                story_details=story_details,
            )
            attempt += 1

        raise ValueError(
            f"Semantic consistency guard failed for beat={beat} source='{source_for_guard[:80]}'"
        )

    def _safe_visual_goal_from_source(self, beat: str, source_text: str) -> str:
        clue = self._trim_text_words(source_text, max_words=18)
        anomaly_object = self._anomaly_focus_object(source_text)
        anomaly_focus = self._focus_phrase_from_object(anomaly_object, {"kind": "generic_interior"})
        if beat == "hook_image":
            return f"Show the first disturbing image exactly as stated: {clue}."
        if beat == "setup":
            if anomaly_focus:
                return f"Ground the baseline around {anomaly_focus} before the shift."
            return f"Ground the baseline detail from this line: {clue}."
        if beat == "strange_detail":
            if anomaly_focus:
                return f"Center the first off detail on {anomaly_focus}: {clue}."
            return f"Focus on the first off detail from this line: {clue}."
        if beat == "escalation":
            if anomaly_focus:
                return f"Raise pressure around {anomaly_focus}: {clue}."
            return f"Show pressure rising from this line: {clue}."
        if beat == "strongest_reveal":
            if anomaly_focus:
                return f"Center the irreversible reveal on {anomaly_focus}: {clue}."
            if self._line_has_reflection_anomaly(source_text):
                return "Center the mirror/body sync break from this line."
            if self._line_has_voice_anomaly(source_text):
                return "Center the spoken-name anomaly and wrong voice source from this line."
            return f"Center the irreversible reveal from this line: {clue}."
        if beat == "consequence_ending":
            low = str(source_text or "").lower()
            if self._contains_any(low, ["mirror in my phone", "phone mirror", "checked the mirror"]):
                return "End on checking the mirror in the phone while staying away from the door."
            if self._contains_any(low, ["waited outside until sunrise", "until sunrise"]) and self._contains_any(low, ["never opened", "didn't open", "did not open", "without opening"]):
                return "End on waiting outside until sunrise and never opening the door."
            if self._contains_any(low, ["never opened", "didn't open", "did not open", "without opening"]):
                return "End on never opening the door."
            if (
                self._contains_any(low, ["stayed inside the elevator", "rode straight to the lobby", "rode down to the lobby"])
                or ("elevator" in low and "lobby" in low and "stayed" in low)
            ):
                return "End on staying inside the elevator and riding directly to the lobby."
            return f"End on the decision/cost from this line: {clue}."
        if beat == "aftershock":
            if anomaly_focus:
                return f"Show a concrete residue image centered on {anomaly_focus}: {clue}."
            return f"Show a concrete residue image from this line: {clue}."
        return f"Use this line as the beat anchor: {clue}."

    def _safe_on_screen_from_source(self, beat: str, source_text: str, shot_index: int) -> str:
        source_details = self.extract_story_details(source_text)
        source_low = str(source_text or "").lower()
        if self._line_has_reflection_anomaly(source_low):
            candidates = [
                self._caption_from_beat_intent(beat=beat, beat_text=source_text, story_details=source_details),
                self._caption_from_patterns(source_text),
                self._caption_from_beat(beat=beat, beat_text=source_text, story_details=source_details),
                self._grounded_caption_from_source_line(source_text),
                self._caption_from_beat_intent(beat=beat, beat_text=source_text, story_details={}),
            ]
        else:
            candidates = [
                self._grounded_caption_from_source_line(source_text),
                self._caption_from_patterns(source_text),
                self._caption_from_beat(beat=beat, beat_text=source_text, story_details=source_details),
                self._caption_from_beat_intent(beat=beat, beat_text=source_text, story_details=source_details),
                self._caption_from_beat_intent(beat=beat, beat_text=source_text, story_details={}),
            ]

        fallback = ""
        for candidate in candidates:
            if not candidate:
                continue
            finalized = self._finalize_caption(candidate=candidate, beat=beat, shot_index=shot_index)
            if not fallback:
                fallback = finalized
            if self._is_on_screen_aligned_for_beat(
                beat=beat,
                source_text=source_text,
                on_screen_text=finalized,
                script_text=source_text,
                story_details=source_details,
            ):
                return finalized

        if fallback:
            return fallback
        return self._finalize_caption(
            candidate=self._caption_from_beat(beat=beat, beat_text=source_text, story_details={}),
            beat=beat,
            shot_index=shot_index,
        )

    def _safe_prompt_from_source(
        self,
        beat: str,
        source_text: str,
        scene_anchor: str,
        shot_index: int,
        scene_profile: dict[str, Any],
        story_details: dict[str, Any],
    ) -> str:
        framing = self._pick_scene_beat_phrase(
            beat=beat,
            shot_index=shot_index,
            scene_profile=scene_profile,
            base_map=self._BEAT_FRAMING,
            scene_map=self._SCENE_BEAT_FRAMING,
            fallback=self._FRAMING_CUES,
        )
        lighting = self._pick_scene_beat_phrase(
            beat=beat,
            shot_index=shot_index,
            scene_profile=scene_profile,
            base_map=self._BEAT_LIGHTING,
            scene_map=self._SCENE_BEAT_LIGHTING,
            fallback=self._LIGHTING,
        )
        camera = self._CAMERA_MOVES[shot_index % len(self._CAMERA_MOVES)]
        clue = self._trim_text_words(source_text, max_words=18)
        subject = scene_anchor.split(",")[0].strip()
        safe_source = self._normalize_source_text(source_text, sentence_corpus=self._split_sentences(source_text))
        if not safe_source:
            safe_source = source_text

        base_subject = subject
        base_framing = framing
        base_lighting = lighting
        base_clue = clue

        override_subject, override_framing, override_lighting, override_clue = self._apply_event_prompt_overrides(
            beat=beat,
            beat_text=safe_source,
            story_details=story_details,
            scene_profile=scene_profile,
            subject=subject,
            framing=framing,
            lighting=lighting,
            clue=clue,
        )

        def render_prompt(raw_subject: str, raw_framing: str, raw_lighting: str, raw_clue: str) -> str:
            subject_local = self._lock_phrase_to_scene(raw_subject, scene_profile)
            framing_local = self._lock_phrase_to_scene(raw_framing, scene_profile)
            lighting_local = self._lock_phrase_to_scene(raw_lighting, scene_profile)
            clue_local = self._lock_phrase_to_scene(raw_clue, scene_profile)
            subject_local = self._lock_phrase_to_story_entities(subject_local, story_details)
            framing_local = self._lock_phrase_to_story_entities(framing_local, story_details)
            lighting_local = self._lock_phrase_to_story_entities(lighting_local, story_details)
            clue_local = self._lock_phrase_to_story_entities(clue_local, story_details)
            if str(scene_profile.get("kind", "")) == "room_blackout" and "hallway" not in str(safe_source or "").lower():
                subject_local = re.sub(r"\bhallway\b|\bcorridor\b", "room", subject_local, flags=re.IGNORECASE)
                framing_local = re.sub(r"\bhallway\b|\bcorridor\b", "room", framing_local, flags=re.IGNORECASE)
                lighting_local = re.sub(r"\bhallway\b|\bcorridor\b", "room", lighting_local, flags=re.IGNORECASE)
                clue_local = re.sub(r"\bhallway\b|\bcorridor\b", "room", clue_local, flags=re.IGNORECASE)
            return f"{subject_local}, {framing_local}, {lighting_local}, {clue_local}, with a restrained {camera}."

        base_prompt = render_prompt(
            raw_subject=base_subject,
            raw_framing=base_framing,
            raw_lighting=base_lighting,
            raw_clue=base_clue,
        )
        override_prompt = render_prompt(
            raw_subject=override_subject,
            raw_framing=override_framing,
            raw_lighting=override_lighting,
            raw_clue=override_clue,
        )
        if self._has_foreign_entities(override_prompt, story_details):
            return base_prompt
        if self._introduces_foreign_reveal(override_prompt, safe_source):
            return base_prompt
        return override_prompt

    def _has_foreign_entities(self, text: str, story_details: dict[str, Any]) -> bool:
        normalized = str(text or "").lower()
        allowed_objects = {str(item) for item in story_details.get("objects", [])}
        allowed_actions = {str(item) for item in story_details.get("actions", [])}

        detected_objects = {
            label for label, pattern in self._OBJECT_PATTERNS.items() if re.search(pattern, normalized)
        }
        detected_actions = {
            label.replace("_", " ")
            for label, pattern in self._ACTION_PATTERNS.items()
            if re.search(pattern, normalized)
        }
        detected_actions = {
            action for action in detected_actions if action not in self._NEUTRAL_GUARD_ACTIONS
        }

        # If the script contains a "called" action, allow phone wording as an implied object.
        if "called" in allowed_actions and "phone" in detected_objects:
            detected_objects = {item for item in detected_objects if item != "phone"}

        scene_stabilizers = {"exit_sign", "handle"}
        checked_objects = {label for label in detected_objects if label not in scene_stabilizers}

        if allowed_objects and any(label not in allowed_objects for label in checked_objects):
            return True
        if allowed_actions and any(label not in allowed_actions for label in detected_actions):
            return True
        return False

    def _is_on_screen_aligned(self, source_text: str, on_screen_text: str) -> bool:
        source_tokens = self._meaningful_tokens(source_text)
        caption_tokens = self._meaningful_tokens(on_screen_text)
        if not caption_tokens:
            return False
        if not source_tokens:
            return True
        overlap = source_tokens.intersection(caption_tokens)
        if overlap:
            return True
        source_forms = self._semantic_token_forms(source_text)
        caption_forms = self._semantic_token_forms(on_screen_text)
        return bool(source_forms.intersection(caption_forms))

    def _is_on_screen_aligned_for_beat(
        self,
        beat: str,
        source_text: str,
        on_screen_text: str,
        script_text: str,
        story_details: dict[str, Any],
    ) -> bool:
        if self._is_on_screen_aligned(source_text, on_screen_text):
            return True

        tolerant_beats = {"setup", "escalation", "consequence_ending"}
        if beat not in tolerant_beats:
            return False
        if self._is_generic_fallback_caption(on_screen_text):
            return False
        if not self._is_source_grounded_to_story(source_text, script_text, story_details):
            return False

        source_forms = self._semantic_token_forms(source_text)
        caption_forms = self._semantic_token_forms(on_screen_text)
        if not source_forms or not caption_forms:
            return False
        if source_forms.intersection(caption_forms):
            return True

        story_forms: set[str] = set()
        for item in story_details.get("objects", []):
            story_forms.update(self._semantic_token_forms(str(item)))
        for item in story_details.get("actions", []):
            story_forms.update(self._semantic_token_forms(str(item)))
        for item in story_details.get("reveal_phrases", []):
            story_forms.update(self._semantic_token_forms(str(item)))
        for item in story_details.get("consequence_phrases", []):
            story_forms.update(self._semantic_token_forms(str(item)))
        if caption_forms.intersection(story_forms):
            return True

        suspense_forms = {
            "hallway",
            "door",
            "voice",
            "silence",
            "listen",
            "stillness",
            "stairwell",
            "exit",
            "elevator",
            "floor",
            "footsteps",
            "mirror",
            "reflection",
            "apartment",
        }
        return bool(caption_forms.intersection(suspense_forms) and source_forms.intersection(suspense_forms))

    def _is_source_grounded_to_story(self, source_text: str, script_text: str, story_details: dict[str, Any]) -> bool:
        normalized_source = re.sub(r"\s+", " ", str(source_text or "").strip().lower())
        normalized_script = re.sub(r"\s+", " ", str(script_text or "").strip().lower())
        if normalized_source and normalized_source in normalized_script:
            return True

        source_forms = self._semantic_token_forms(source_text)
        if not source_forms:
            return False

        for sentence in self._split_sentences(script_text):
            sentence_forms = self._semantic_token_forms(sentence)
            if not sentence_forms:
                continue
            overlap = source_forms.intersection(sentence_forms)
            if len(overlap) >= max(2, min(4, len(source_forms) // 2 or 1)):
                return True

        story_forms: set[str] = set()
        for key in ("objects", "actions", "reveal_phrases", "consequence_phrases"):
            for value in story_details.get(key, []):
                story_forms.update(self._semantic_token_forms(str(value)))
        return len(source_forms.intersection(story_forms)) >= 2

    def _semantic_token_forms(self, text: str) -> set[str]:
        forms: set[str] = set()
        aliases = {
            "corridor": "hallway",
            "hall": "hallway",
            "doorway": "door",
            "threshold": "door",
            "latch": "door",
            "knob": "door",
            "stairs": "stairwell",
            "stair": "stairwell",
            "landing": "stairwell",
            "arrow": "exit",
            "arrows": "exit",
            "sign": "exit",
            "whisper": "voice",
            "called": "voice",
            "calling": "voice",
            "nickname": "name",
            "silent": "silence",
            "quiet": "silence",
            "stayed": "stillness",
            "still": "stillness",
            "held": "stillness",
            "froze": "stillness",
            "listened": "listen",
            "listening": "listen",
            "heard": "listen",
            "footstep": "footsteps",
            "mirror": "reflection",
            "reflection": "mirror",
            "turned": "move",
            "turn": "move",
            "moved": "move",
            "moving": "move",
            "blinked": "blink",
            "late": "delay",
            "delayed": "delay",
        }
        for token in self._meaningful_tokens(text):
            forms.add(token)
            forms.add(aliases.get(token, token))
            if token.endswith("ing") and len(token) > 5:
                forms.add(token[:-3])
            if token.endswith("ed") and len(token) > 4:
                forms.add(token[:-2])
            if token.endswith("s") and len(token) > 4:
                forms.add(token[:-1])
        return {item for item in forms if item}

    def _is_generic_fallback_caption(self, caption: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(caption or "").strip().lower())
        if not normalized:
            return True
        exact_generic = {
            "something felt wrong immediately",
            "the first frame felt wrong",
            "i held my place and watched",
            "i stayed and listened first",
            "pressure built and i froze",
            "i froze as pressure built",
            "that reveal changed everything",
            "the reveal made no sense",
            "i chose to leave immediately",
            "i acted and got out",
        }
        if normalized in exact_generic:
            return True
        generic_phrases = [
            "something felt wrong",
            "pressure built",
            "reveal changed everything",
            "reveal made no sense",
            "chose to leave immediately",
            "acted and got out",
        ]
        return any(phrase in normalized for phrase in generic_phrases)

    def _grounded_caption_from_source_line(self, source_text: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(source_text or "")).strip(" .,:;!?")
        if not cleaned:
            return ""

        reflection_variants = self._reflection_caption_variants(cleaned)
        if reflection_variants:
            return reflection_variants[0]

        clause = self._best_caption_clause(cleaned)
        clause_words = re.findall(r"[A-Za-z0-9']+", clause)
        if 3 <= len(clause_words) <= 7 and not self._looks_fragment(clause_words):
            return " ".join(clause_words)

        words = re.findall(r"[A-Za-z0-9']+", cleaned)
        if len(words) < 3:
            return ""
        if len(words) <= 7 and not self._looks_fragment(words):
            return " ".join(words)

        max_start = max(1, min(4, len(words) - 2))
        for start in range(max_start):
            segment = words[start:start + 7]
            while len(segment) > 3 and self._looks_fragment(segment):
                segment.pop()
            if 3 <= len(segment) <= 7 and not self._looks_fragment(segment):
                return " ".join(segment)
        return ""

    def _introduces_foreign_reveal(self, prompt_text: str, script_text: str) -> bool:
        prompt_lower = str(prompt_text or "").lower()
        script_lower = str(script_text or "").lower()
        for marker in self._REVEAL_MARKERS:
            if marker in prompt_lower and marker not in script_lower:
                return True
        return False

    def _meaningful_tokens(self, text: str) -> set[str]:
        return {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9']+", str(text or ""))
            if len(token) > 2 and token.lower() not in self._STOPWORDS
        }

    def _ensure_unique_prompt(self, prompt: str, seen_prompts: set[str], shot_index: int) -> str:
        if prompt not in seen_prompts:
            seen_prompts.add(prompt)
            return prompt
        suffixes = [
            "alternate depth layering",
            "foreground obstruction shifts",
            "different eye-line perspective",
            "wider frame tension",
        ]
        for suffix in suffixes:
            candidate = prompt.rstrip(".") + f", {suffix}."
            if candidate not in seen_prompts:
                seen_prompts.add(candidate)
                return candidate
        candidate = prompt.rstrip(".") + f", shot variation {shot_index + 1}."
        seen_prompts.add(candidate)
        return candidate
