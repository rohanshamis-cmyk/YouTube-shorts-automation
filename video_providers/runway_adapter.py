"""Runway image-to-video adapter with offline-first workflow.

This module keeps request semantics aligned with Runway's image-to-video API:
- Auth env var: RUNWAYML_API_SECRET
- Endpoint: POST https://api.dev.runwayml.com/v1/image_to_video
- Header: Authorization: Bearer <key>
- Header: X-Runway-Version: 2024-11-06
- Default model: gen4_turbo
- promptImage required
- duration constrained to 2..10
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

RUNWAY_ENDPOINT = "https://api.dev.runwayml.com/v1/image_to_video"
RUNWAY_VERSION = "2024-11-06"
DEFAULT_MODEL = "gen4_turbo"
DEFAULT_RATIO = "720:1280"
DEFAULT_DURATION = 4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(slots=True)
class ShotSpec:
    shot_id: str
    prompt_text: str
    prompt_image: str
    ratio: str = DEFAULT_RATIO
    duration: int = DEFAULT_DURATION
    seed: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class ProviderResult:
    shot_id: str
    mode: str
    status: str
    request_payload_path: str | None
    output_stub_path: str | None
    runway_task_id: str | None
    error: str | None


class RunwayAdapter:
    """Offline-first Runway adapter with optional live submit mode."""

    _PROMPT_IMAGE_RE = re.compile(r"^(https://|runway://|data:image/)", re.IGNORECASE)
    _RATIO_RE = re.compile(r"^\d+:\d+$")
    _ALLOWED_MODES = {"offline", "live", "auto"}
    _LOCAL_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

    def __init__(self, mode: str = "offline", output_root: str = "generated/runway"):
        env_mode = os.getenv("RUNWAY_MODE", "").strip().lower()
        resolved_mode = (env_mode or mode or "offline").lower()
        if resolved_mode not in self._ALLOWED_MODES:
            raise ValueError(f"Invalid RUNWAY mode '{resolved_mode}'. Use offline/live/auto.")

        self.mode = resolved_mode
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)

        self.api_secret = os.getenv("RUNWAYML_API_SECRET", "").strip()
        self.model = os.getenv("RUNWAY_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self.default_ratio = os.getenv("RUNWAY_RATIO", DEFAULT_RATIO).strip() or DEFAULT_RATIO
        self.default_duration = _env_int("RUNWAY_DURATION", DEFAULT_DURATION)
        if not self._RATIO_RE.match(self.default_ratio):
            self.default_ratio = DEFAULT_RATIO
        if not isinstance(self.default_duration, int) or not (2 <= self.default_duration <= 10):
            self.default_duration = DEFAULT_DURATION

        self.last_manifest_path: str | None = None

    def validate_shot(self, shot: ShotSpec) -> None:
        if not isinstance(shot.shot_id, str) or not shot.shot_id.strip():
            raise ValueError("shot_id must be a non-empty string.")
        if not isinstance(shot.prompt_text, str) or not shot.prompt_text.strip():
            raise ValueError("prompt_text must be non-empty.")
        if not isinstance(shot.prompt_image, str):
            raise ValueError("prompt_image must be a string.")
        prompt_image_value = shot.prompt_image.strip()
        if not (
            self._PROMPT_IMAGE_RE.match(prompt_image_value)
            or self._is_local_prompt_image_path(prompt_image_value)
        ):
            raise ValueError(
                "prompt_image must start with https://, runway://, data:image/, "
                "or be a local image path ending in .png/.jpg/.jpeg/.webp."
            )

        ratio = (shot.ratio or self.default_ratio).strip()
        if not self._RATIO_RE.match(ratio):
            raise ValueError("ratio must be in W:H format like 720:1280.")

        duration = shot.duration if shot.duration is not None else self.default_duration
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise ValueError("duration must be an integer between 2 and 10.")
        if duration < 2 or duration > 10:
            raise ValueError("duration must be within 2..10.")

    def build_payload(self, shot: ShotSpec) -> dict[str, Any]:
        self.validate_shot(shot)
        payload: dict[str, Any] = {
            "model": self.model,
            "promptText": shot.prompt_text.strip(),
            "promptImage": shot.prompt_image.strip(),
            "ratio": (shot.ratio or self.default_ratio).strip(),
            "duration": int(shot.duration if shot.duration is not None else self.default_duration),
        }
        if shot.seed is not None:
            payload["seed"] = shot.seed
        return payload

    def submit_live(self, shot: ShotSpec) -> ProviderResult:
        payload_path, summary_path = self._shot_paths(shot.shot_id)
        try:
            payload = self.build_payload(shot)
        except Exception as exc:
            return self._error_result(shot_id=shot.shot_id, mode="live", error=str(exc))

        self._write_json(payload_path, payload)

        if self._is_local_prompt_image_path(shot.prompt_image):
            return self._error_result(
                shot_id=shot.shot_id,
                mode="live",
                error=(
                    "Live submit requires prompt_image as https://, runway://, or data:image/. "
                    "Local file paths are offline-only."
                ),
                request_payload_path=str(payload_path),
            )

        if not self.api_secret:
            return self._error_result(
                shot_id=shot.shot_id,
                mode="live",
                error="RUNWAYML_API_SECRET is missing.",
                request_payload_path=str(payload_path),
            )

        headers = {
            "Authorization": f"Bearer {self.api_secret}",
            "X-Runway-Version": RUNWAY_VERSION,
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(RUNWAY_ENDPOINT, headers=headers, json=payload, timeout=30)
        except requests.RequestException as exc:
            return self._error_result(
                shot_id=shot.shot_id,
                mode="live",
                error=f"Runway request failed: {exc}",
                request_payload_path=str(payload_path),
            )

        if response.status_code >= 400:
            detail = self._response_error_detail(response)
            return self._error_result(
                shot_id=shot.shot_id,
                mode="live",
                error=f"Runway live submit failed ({response.status_code}): {detail}",
                request_payload_path=str(payload_path),
            )

        try:
            body = response.json()
        except ValueError:
            body = {}

        task_id = self._extract_task_id(body)
        if not task_id:
            return self._error_result(
                shot_id=shot.shot_id,
                mode="live",
                error="Runway response did not include a task id.",
                request_payload_path=str(payload_path),
            )

        live_stub = {
            "shot_id": shot.shot_id,
            "submitted_at": _utc_now_iso(),
            "endpoint": RUNWAY_ENDPOINT,
            "runway_task_id": task_id,
            "polling_hint": "Use task id with a future polling helper; this adapter submits only.",
            "response_body": body,
        }
        self._write_json(summary_path.with_suffix(".live.json"), live_stub)

        return ProviderResult(
            shot_id=shot.shot_id,
            mode="live",
            status="submitted",
            request_payload_path=str(payload_path),
            output_stub_path=str(summary_path.with_suffix(".live.json")),
            runway_task_id=task_id,
            error=None,
        )

    def submit_offline(self, shot: ShotSpec) -> ProviderResult:
        payload_path, summary_path = self._shot_paths(shot.shot_id)
        try:
            payload = self.build_payload(shot)
        except Exception as exc:
            return self._error_result(shot_id=shot.shot_id, mode="offline", error=str(exc))

        self._write_json(payload_path, payload)
        self._write_text(summary_path, self._offline_summary_markdown(shot=shot, payload=payload))

        return ProviderResult(
            shot_id=shot.shot_id,
            mode="offline",
            status="mocked",
            request_payload_path=str(payload_path),
            output_stub_path=str(summary_path),
            runway_task_id=None,
            error=None,
        )

    def render_shot(self, shot: ShotSpec) -> ProviderResult:
        if self.mode == "offline":
            return self.submit_offline(shot)
        if self.mode == "live":
            return self.submit_live(shot)
        if self.mode == "auto":
            if not self.api_secret:
                return self.submit_offline(shot)
            return self.submit_live(shot)
        return self._error_result(shot_id=shot.shot_id, mode=self.mode, error=f"Unknown mode '{self.mode}'.")

    def render_batch(self, shots: list[ShotSpec]) -> list[ProviderResult]:
        results: list[ProviderResult] = []
        for shot in shots:
            try:
                results.append(self.render_shot(shot))
            except Exception as exc:  # pragma: no cover - defensive
                results.append(
                    self._error_result(
                        shot_id=getattr(shot, "shot_id", "unknown"),
                        mode=self.mode,
                        error=str(exc),
                    )
                )

        manifest_path = self._write_batch_manifest(shots=shots, results=results)
        self.last_manifest_path = str(manifest_path)
        return results

    def poll_task_stub(self, runway_task_id: str) -> dict[str, str]:
        """Helper placeholder for future task polling implementation."""
        return {
            "runway_task_id": runway_task_id,
            "note": "Polling is intentionally not implemented yet.",
        }

    def _shot_paths(self, shot_id: str) -> tuple[Path, Path]:
        shots_dir = self.output_root / "shots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        return shots_dir / f"{shot_id}.payload.json", shots_dir / f"{shot_id}.summary.md"

    def _write_batch_manifest(self, shots: list[ShotSpec], results: list[ProviderResult]) -> Path:
        result_map = {res.shot_id: res for res in results}
        items: list[dict[str, Any]] = []
        for shot in shots:
            result = result_map.get(shot.shot_id)
            row = {
                "shot": asdict(shot),
                "result": asdict(result) if result else None,
            }
            items.append(row)

        manifest = {
            "created_at": _utc_now_iso(),
            "requested_mode": self.mode,
            "model": self.model,
            "endpoint": RUNWAY_ENDPOINT,
            "runway_version": RUNWAY_VERSION,
            "output_root": str(self.output_root),
            "shots": items,
        }
        manifest_path = self.output_root / "batch_manifest.json"
        self._write_json(manifest_path, manifest)
        return manifest_path

    def _offline_summary_markdown(self, shot: ShotSpec, payload: dict[str, Any]) -> str:
        metadata = shot.metadata or {}
        metadata_json = json.dumps(metadata, indent=2, ensure_ascii=False) if metadata else "{}"
        return "\n".join(
            [
                f"# Offline Runway Shot {shot.shot_id}",
                "",
                "- mode: offline",
                f"- model: {payload.get('model')}",
                f"- ratio: {payload.get('ratio')}",
                f"- duration: {payload.get('duration')}",
                "",
                "## Prompt",
                "",
                shot.prompt_text.strip(),
                "",
                "## Prompt Image",
                "",
                shot.prompt_image.strip(),
                "",
                "## Metadata",
                "",
                "```json",
                metadata_json,
                "```",
                "",
                "## Next Step",
                "",
                "Switch adapter mode to `live` (or `auto` with RUNWAYML_API_SECRET set) to submit this payload.",
            ]
        )

    def _error_result(
        self,
        shot_id: str,
        mode: str,
        error: str,
        request_payload_path: str | None = None,
        output_stub_path: str | None = None,
    ) -> ProviderResult:
        return ProviderResult(
            shot_id=shot_id,
            mode=mode,
            status="error",
            request_payload_path=request_payload_path,
            output_stub_path=output_stub_path,
            runway_task_id=None,
            error=error,
        )

    def _response_error_detail(self, response: requests.Response) -> str:
        try:
            body = response.json()
            if isinstance(body, dict):
                for key in ("error", "message", "detail"):
                    value = body.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            return json.dumps(body, ensure_ascii=False)[:500]
        except ValueError:
            text = (response.text or "").strip()
            return text[:500] if text else "Unknown error"

    def _extract_task_id(self, body: dict[str, Any]) -> str | None:
        if not isinstance(body, dict):
            return None

        direct_keys = ("id", "task_id", "taskId")
        for key in direct_keys:
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        nested = body.get("data")
        if isinstance(nested, dict):
            for key in direct_keys:
                value = nested.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _is_local_prompt_image_path(self, value: str) -> bool:
        candidate = str(value or "").strip()
        if not candidate:
            return False
        lowered = candidate.lower()
        if lowered.startswith(("https://", "http://", "runway://", "data:image/")):
            return False
        if lowered.startswith("file://"):
            suffix = Path(candidate[7:]).suffix.lower()
            return suffix in self._LOCAL_IMAGE_SUFFIXES
        suffix = Path(candidate).suffix.lower()
        return suffix in self._LOCAL_IMAGE_SUFFIXES
