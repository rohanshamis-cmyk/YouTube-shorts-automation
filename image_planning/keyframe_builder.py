"""Build keyframe prompt specs from planned video shots."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from video_planning.shot_pack_builder import PlannedShot


DEFAULT_NEGATIVE_PROMPT = (
    "low quality, blurry, noisy, artifacting, watermark, logo, text overlay, subtitle burn-in, "
    "bad anatomy, extra limbs, extra fingers, deformed hands, distorted face, duplicated subject, "
    "oversaturated, muddy shadows, flat lighting, compression artifacts"
)


@dataclass(slots=True)
class KeyframeSpec:
    shot_id: str
    image_prompt: str
    negative_prompt: str
    reference_image: str | None
    output_image_path: str
    visual_role: str
    metadata: dict[str, Any] | None = None


class KeyframeBuilder:
    """Creates a local keyframe asset contract for each planned shot."""

    _STYLE_DEFAULT = "photoreal still, vertical 9:16"

    _COMPOSITIONS = [
        "off-center composition with deep negative space near the doorway",
        "center-framed medium shot with converging hallway lines",
        "foreground obstruction partially masking the subject plane",
        "tight crop on key object with background depth cues",
        "over-shoulder perspective aimed at the tension source",
        "low-angle frame that emphasizes spatial pressure",
        "wider frame with empty space where movement should appear",
        "close framing with layered foreground and reflective surfaces",
    ]
    _LIGHTING = [
        "cold practical ceiling light with hard falloff",
        "flickering tungsten spill and soft shadow pooling",
        "dim mixed lighting with one bright edge highlight",
        "streetlight bleed through blinds with dark corners",
        "single side key light and weak fill from the hall",
        "emergency-style low light with subtle haze",
        "quiet moonlit ambient with selective contrast",
        "sodium-vapor exterior leak and muted interior tones",
    ]
    _MOOD_BY_BEAT = {
        "hook_image": "quiet dread",
        "setup": "contained unease",
        "strange_detail": "subtle wrongness",
        "escalation": "rising pressure",
        "strongest_reveal": "inescapable tension",
        "consequence_ending": "cold consequence",
        "aftershock": "aftershock stillness",
        "final_echo": "lingering uncertainty",
    }
    _VISUAL_ROLE_BY_BEAT = {
        "hook_image": "hook_keyframe",
        "setup": "context_keyframe",
        "strange_detail": "detail_keyframe",
        "escalation": "escalation_keyframe",
        "strongest_reveal": "reveal_keyframe",
        "consequence_ending": "ending_keyframe",
        "aftershock": "aftershock_keyframe",
        "final_echo": "echo_keyframe",
    }

    def __init__(self, output_root: str = "generated/keyframes", default_style: str | None = None):
        self.output_root = Path(output_root)
        self.default_style = (default_style or self._STYLE_DEFAULT).strip()
        self.last_output_dir: str | None = None
        self.last_run_id: str | None = None

    def build_from_planned_shots(self, planned_shots: list[PlannedShot]) -> list[KeyframeSpec]:
        if not planned_shots:
            raise ValueError("planned_shots is empty.")

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = self.output_root / run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        keyframes: list[KeyframeSpec] = []
        seen_prompts: set[str] = set()

        for idx, shot in enumerate(planned_shots):
            image_prompt = self._build_image_prompt(shot=shot, shot_index=idx)
            image_prompt = self._ensure_prompt_uniqueness(image_prompt, seen_prompts, idx)

            reference_image = shot.prompt_image.strip() if str(shot.prompt_image or "").strip() else None
            visual_role = self._VISUAL_ROLE_BY_BEAT.get(shot.beat, "support_keyframe")
            output_image_path = str(output_dir / f"{shot.shot_id}.png")

            metadata = dict(shot.metadata or {})
            metadata.update(
                {
                    "beat": shot.beat,
                    "visual_goal": shot.visual_goal,
                    "on_screen_text": shot.on_screen_text,
                    "ratio": shot.ratio,
                    "duration": shot.duration,
                }
            )

            keyframes.append(
                KeyframeSpec(
                    shot_id=shot.shot_id,
                    image_prompt=image_prompt,
                    negative_prompt=DEFAULT_NEGATIVE_PROMPT,
                    reference_image=reference_image,
                    output_image_path=output_image_path,
                    visual_role=visual_role,
                    metadata=metadata,
                )
            )

        self.last_output_dir = str(output_dir)
        self.last_run_id = run_id
        return keyframes

    def attach_keyframes_to_shots(
        self,
        planned_shots: list[PlannedShot],
        keyframes: list[KeyframeSpec],
    ) -> list[PlannedShot]:
        keyframe_by_id = {kf.shot_id: kf for kf in keyframes}
        updated: list[PlannedShot] = []

        for shot in planned_shots:
            keyframe = keyframe_by_id.get(shot.shot_id)
            if not keyframe:
                raise ValueError(f"Missing keyframe for shot_id={shot.shot_id}")

            metadata = dict(shot.metadata or {})
            metadata.update(
                {
                    "keyframe_prompt": keyframe.image_prompt,
                    "keyframe_negative_prompt": keyframe.negative_prompt,
                    "keyframe_output_image_path": keyframe.output_image_path,
                    "keyframe_visual_role": keyframe.visual_role,
                }
            )

            updated.append(
                PlannedShot(
                    shot_id=shot.shot_id,
                    beat=shot.beat,
                    visual_goal=shot.visual_goal,
                    prompt_text=shot.prompt_text,
                    prompt_image=keyframe.output_image_path,
                    on_screen_text=shot.on_screen_text,
                    duration=shot.duration,
                    ratio=shot.ratio,
                    metadata=metadata,
                )
            )

        return updated

    def write_manifest(self, keyframes: list[KeyframeSpec], output_dir: str | Path) -> str:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        manifest_path = output_path / "keyframe_manifest.json"

        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "output_dir": str(output_path),
            "count": len(keyframes),
            "default_style": self.default_style,
            "keyframes": [asdict(kf) for kf in keyframes],
        }
        manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(manifest_path)

    def _build_image_prompt(self, shot: PlannedShot, shot_index: int) -> str:
        base_prompt = self._clean_prompt_text(shot.prompt_text)
        if not base_prompt:
            scene = self._extract_scene_subject(shot.prompt_text)
            composition = self._COMPOSITIONS[shot_index % len(self._COMPOSITIONS)]
            lighting = self._LIGHTING[shot_index % len(self._LIGHTING)]
            clue = self._extract_tension_detail(shot.prompt_text, shot.visual_goal, shot.on_screen_text)
            base_prompt = (
                f"{scene}, {composition}, {lighting}, {clue}, "
                f"camera movement remains restrained."
            )

        beat_addon = self._beat_specific_addon(shot)
        if beat_addon and beat_addon.lower() not in base_prompt.lower():
            base_prompt = f"{base_prompt} {beat_addon}"

        if shot_index == 0 and "9:16" not in base_prompt:
            base_prompt = f"{base_prompt} {self.default_style}."

        return self._clean_prompt_text(base_prompt)

    def _beat_specific_addon(self, shot: PlannedShot) -> str:
        metadata = dict(shot.metadata or {})
        objects = {str(item).lower() for item in metadata.get("story_objects", [])}
        actions = {str(item).lower() for item in metadata.get("story_actions", [])}

        if shot.beat == "hook_image":
            return "Treat this as the first iconic frame."
        if shot.beat == "setup":
            return "Keep spatial grounding clear and stable."
        if shot.beat == "strange_detail":
            return "Hold on the off detail long enough to register the wrongness."
        if shot.beat == "escalation":
            return "Tighten proximity and attention without revealing the final answer."
        if shot.beat == "strongest_reveal":
            return "Keep the reveal line crisp and readable."
        if shot.beat == "consequence_ending":
            has_note = "note" in objects
            has_phone = "phone" in objects or "called" in actions
            has_door = "door" in objects
            if has_note and has_phone and has_door:
                return "Phone in hand while backing away from the doorway, note still visible, silence held in frame."
            if has_note and has_phone:
                return "Phone in hand while backing away, with the note still visible."
            return "Land on a hard decision frame with visible aftermath."
        return ""

    def _clean_prompt_text(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        cleaned = cleaned.replace(" ;", ";").replace(" ,", ",").strip(" ,;")
        if not cleaned:
            return ""
        if cleaned[-1] not in ".!?":
            cleaned = f"{cleaned}."
        return cleaned

    def _extract_scene_subject(self, prompt_text: str) -> str:
        parts = [p.strip().strip(".") for p in prompt_text.split(",") if p.strip()]
        if parts:
            return parts[0]
        fallback = prompt_text.strip().strip(".")
        return fallback or "quiet interior setting with believable detail"

    def _extract_tension_detail(self, prompt_text: str, visual_goal: str, on_screen_text: str) -> str:
        parts = [p.strip().strip(".") for p in prompt_text.split(",") if p.strip()]
        if len(parts) >= 4:
            return parts[3]
        if len(parts) >= 3:
            return parts[2]
        if on_screen_text.strip():
            return on_screen_text.strip()
        return visual_goal.strip() or "an object appears out of place"

    def _ensure_prompt_uniqueness(self, prompt: str, seen_prompts: set[str], shot_index: int) -> str:
        if prompt not in seen_prompts:
            seen_prompts.add(prompt)
            return prompt

        suffixes = [
            "alternate depth layering",
            "different eye-line emphasis",
            "foreground occlusion variation",
            "secondary reflection cue",
        ]
        for suffix in suffixes:
            candidate = f"{prompt}, {suffix}"
            if candidate not in seen_prompts:
                seen_prompts.add(candidate)
                return candidate

        fallback = f"{prompt}, variation {shot_index + 1}"
        seen_prompts.add(fallback)
        return fallback
