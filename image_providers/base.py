"""Base contract for keyframe image generation providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from image_planning.keyframe_builder import KeyframeSpec


@dataclass(slots=True)
class GeneratedImageResult:
    shot_id: str
    status: str
    prompt: str
    revised_prompt: str | None
    output_path: str | None
    width: int | None
    height: int | None
    provider: str
    error: str | None
    metadata: dict[str, Any] | None = None


class ImageProvider(ABC):
    """Abstract image provider contract."""

    @abstractmethod
    def generate_image(self, shot_id: str, prompt: str, output_path: str) -> GeneratedImageResult:
        raise NotImplementedError

    @abstractmethod
    def generate_batch(self, items: list[tuple[str, str, str]]) -> list[GeneratedImageResult]:
        raise NotImplementedError


def render_keyframes_with_provider(
    provider: ImageProvider,
    keyframes: list["KeyframeSpec"],
) -> list[GeneratedImageResult]:
    """Render keyframes to their `output_image_path` values with one provider call."""

    items: list[tuple[str, str, str]] = []
    for keyframe in keyframes:
        out_path = Path(keyframe.output_image_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        items.append((keyframe.shot_id, keyframe.image_prompt, str(out_path)))
    return provider.generate_batch(items)

