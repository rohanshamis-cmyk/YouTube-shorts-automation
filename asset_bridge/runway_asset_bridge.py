"""Bridge local keyframe files into valid Runway promptImage values."""

from __future__ import annotations

import base64
import importlib
import importlib.util
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from video_planning.shot_pack_builder import PlannedShot
from video_providers.runway_adapter import ShotSpec


@dataclass(slots=True)
class PromptImageAsset:
    shot_id: str
    source_path: str
    mode: str
    prompt_image_value: str
    media_type: str
    size_bytes: int | None
    metadata: dict[str, Any] | None = None


class RunwayAssetBridge:
    """Creates Runway-compatible promptImage values from local files."""

    _SUPPORTED_TYPES = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    _ALLOWED_MODES = {"data_uri", "ephemeral", "auto"}

    def __init__(self, mode: str = "data_uri", output_root: str = "generated/asset_bridge"):
        normalized_mode = str(mode or "data_uri").strip().lower()
        if normalized_mode not in self._ALLOWED_MODES:
            raise ValueError(f"Unsupported mode '{mode}'. Use data_uri, ephemeral, or auto.")

        self.mode = normalized_mode
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.api_secret = os.getenv("RUNWAYML_API_SECRET", "").strip()

    def detect_media_type(self, path: str | Path) -> str:
        path_obj = Path(path)
        ext = path_obj.suffix.lower()
        media_type = self._SUPPORTED_TYPES.get(ext)
        if not media_type:
            raise ValueError(
                f"Unsupported image type '{ext or 'unknown'}' for {path_obj}. "
                "Supported: .png, .jpg, .jpeg, .webp."
            )
        return media_type

    def file_to_data_uri(self, path: str | Path) -> str:
        path_obj = self._validate_source_path(path)
        media_type = self.detect_media_type(path_obj)
        raw_bytes = path_obj.read_bytes()
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    def upload_ephemeral(self, path: str | Path) -> str:
        path_obj = self._validate_source_path(path)
        media_type = self.detect_media_type(path_obj)

        if not self.api_secret:
            raise RuntimeError("RUNWAYML_API_SECRET is required for ephemeral upload mode.")

        if importlib.util.find_spec("runwayml") is None:
            raise RuntimeError(
                "Official runwayml SDK is not installed. "
                "Install the SDK or use data_uri mode."
            )

        runwayml = importlib.import_module("runwayml")
        client = self._construct_runway_client(runwayml, api_key=self.api_secret)
        return self._try_sdk_upload(client=client, path_obj=path_obj, media_type=media_type)

    def build_asset(self, shot_id: str, source_path: str | Path) -> PromptImageAsset:
        if not isinstance(shot_id, str) or not shot_id.strip():
            raise ValueError("shot_id must be non-empty.")

        path_obj = self._validate_source_path(source_path)
        media_type = self.detect_media_type(path_obj)
        size_bytes = path_obj.stat().st_size
        resolved_mode = self._resolve_mode()

        metadata: dict[str, Any] = {"filename": path_obj.name}
        if resolved_mode == "ephemeral":
            try:
                prompt_value = self.upload_ephemeral(path_obj)
            except Exception as exc:
                if self.mode == "auto":
                    resolved_mode = "data_uri"
                    prompt_value = self.file_to_data_uri(path_obj)
                    metadata["fallback_reason"] = str(exc)
                else:
                    raise
        else:
            prompt_value = self.file_to_data_uri(path_obj)

        return PromptImageAsset(
            shot_id=shot_id,
            source_path=str(path_obj),
            mode=resolved_mode,
            prompt_image_value=prompt_value,
            media_type=media_type,
            size_bytes=size_bytes,
            metadata=metadata,
        )

    def build_assets(self, items: list[tuple[str, str]]) -> list[PromptImageAsset]:
        assets: list[PromptImageAsset] = []
        for shot_id, source_path in items:
            assets.append(self.build_asset(shot_id=shot_id, source_path=source_path))
        return assets

    def write_manifest(self, assets: list[PromptImageAsset], output_dir: str | Path) -> str:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = out_dir / "asset_manifest.json"

        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode,
            "assets": [
                {
                    "shot_id": asset.shot_id,
                    "source_path": asset.source_path,
                    "mode": asset.mode,
                    "prompt_image_value": asset.prompt_image_value,
                    "media_type": asset.media_type,
                    "size_bytes": asset.size_bytes,
                    "metadata": asset.metadata,
                }
                for asset in assets
            ],
        }
        manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(manifest_path)

    def _validate_source_path(self, path: str | Path) -> Path:
        path_obj = Path(path)
        if not path_obj.exists():
            raise FileNotFoundError(f"Source image file does not exist: {path_obj}")
        if not path_obj.is_file():
            raise ValueError(f"Source path is not a file: {path_obj}")
        try:
            _ = path_obj.read_bytes()
        except Exception as exc:
            raise ValueError(f"Source file is not readable: {path_obj} ({exc})") from exc
        return path_obj

    def _resolve_mode(self) -> str:
        if self.mode == "auto":
            if self.api_secret and importlib.util.find_spec("runwayml") is not None:
                return "ephemeral"
            return "data_uri"
        return self.mode

    def _construct_runway_client(self, runwayml_module: Any, api_key: str) -> Any:
        constructor_names = ("RunwayML", "Client")
        for name in constructor_names:
            constructor = getattr(runwayml_module, name, None)
            if callable(constructor):
                try:
                    return constructor(api_key=api_key)
                except TypeError:
                    try:
                        return constructor(apiSecret=api_key)
                    except TypeError:
                        continue
        raise RuntimeError(
            "Runway SDK is installed, but no supported client constructor was found."
        )

    def _try_sdk_upload(self, client: Any, path_obj: Path, media_type: str) -> str:
        raw = path_obj.read_bytes()
        errors: list[str] = []
        attempts = [
            lambda c: c.assets.upload(file=str(path_obj)),
            lambda c: c.assets.upload(file=raw, filename=path_obj.name, mime_type=media_type),
            lambda c: c.uploads.create(file=str(path_obj)),
            lambda c: c.uploads.create(file=raw, filename=path_obj.name, mime_type=media_type),
            lambda c: c.files.upload(path=str(path_obj)),
        ]

        for attempt in attempts:
            try:
                result = attempt(client)
            except Exception as exc:  # pragma: no cover - depends on external SDK
                errors.append(str(exc))
                continue

            uri = self._extract_runway_uri(result)
            if uri:
                return uri
            errors.append("upload call succeeded but no runway:// URI was returned")

        hint = "; ".join(errors[:3]) if errors else "unknown SDK behavior"
        raise RuntimeError(f"Unable to upload ephemeral asset via SDK: {hint}")

    def _extract_runway_uri(self, payload: Any) -> str | None:
        if isinstance(payload, str) and payload.startswith("runway://"):
            return payload

        if isinstance(payload, dict):
            return self._extract_runway_uri_from_dict(payload)

        for attr in ("uri", "runway_uri", "asset_uri", "id"):
            value = getattr(payload, attr, None)
            if isinstance(value, str) and value.startswith("runway://"):
                return value

        as_dict = getattr(payload, "__dict__", None)
        if isinstance(as_dict, dict):
            return self._extract_runway_uri_from_dict(as_dict)
        return None

    def _extract_runway_uri_from_dict(self, value: dict[str, Any]) -> str | None:
        queue: list[Any] = [value]
        while queue:
            current = queue.pop(0)
            if isinstance(current, dict):
                for v in current.values():
                    if isinstance(v, str) and v.startswith("runway://"):
                        return v
                    if isinstance(v, (dict, list)):
                        queue.append(v)
            elif isinstance(current, list):
                for item in current:
                    if isinstance(item, str) and item.startswith("runway://"):
                        return item
                    if isinstance(item, (dict, list)):
                        queue.append(item)
        return None


def apply_assets_to_planned_shots(
    planned_shots: list[PlannedShot],
    assets: list[PromptImageAsset],
) -> list[PlannedShot]:
    """Replace PlannedShot.prompt_image with built prompt_image values."""

    by_id = {asset.shot_id: asset for asset in assets}
    updated: list[PlannedShot] = []
    for shot in planned_shots:
        asset = by_id.get(shot.shot_id)
        if not asset:
            raise ValueError(f"Missing PromptImageAsset for shot_id={shot.shot_id}")
        metadata = dict(shot.metadata or {})
        metadata.update(
            {
                "asset_bridge_mode": asset.mode,
                "asset_bridge_media_type": asset.media_type,
                "asset_bridge_source_path": asset.source_path,
            }
        )
        updated.append(
            PlannedShot(
                shot_id=shot.shot_id,
                beat=shot.beat,
                visual_goal=shot.visual_goal,
                prompt_text=shot.prompt_text,
                prompt_image=asset.prompt_image_value,
                on_screen_text=shot.on_screen_text,
                duration=shot.duration,
                ratio=shot.ratio,
                metadata=metadata,
            )
        )
    return updated


def apply_assets_to_shot_specs(
    shot_specs: list[ShotSpec],
    assets: list[PromptImageAsset],
) -> list[ShotSpec]:
    """Optional helper to replace ShotSpec.prompt_image with bridged asset values."""

    by_id = {asset.shot_id: asset for asset in assets}
    updated: list[ShotSpec] = []
    for shot in shot_specs:
        asset = by_id.get(shot.shot_id)
        if not asset:
            raise ValueError(f"Missing PromptImageAsset for shot_id={shot.shot_id}")
        metadata = dict(shot.metadata or {})
        metadata.update(
            {
                "asset_bridge_mode": asset.mode,
                "asset_bridge_media_type": asset.media_type,
                "asset_bridge_source_path": asset.source_path,
            }
        )
        updated.append(
            ShotSpec(
                shot_id=shot.shot_id,
                prompt_text=shot.prompt_text,
                prompt_image=asset.prompt_image_value,
                ratio=shot.ratio,
                duration=shot.duration,
                seed=shot.seed,
                metadata=metadata,
            )
        )
    return updated

