"""Provider interfaces. Each pipeline stage talks to one of these, so backends are swappable."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass
class ScriptRequest:
    brief: str
    prompt: str              # fully rendered instruction for an LLM
    schema: dict[str, Any]   # JSON schema the response must follow
    scenes: int
    language: str
    style_prompt: str


class LLMProvider(Protocol):
    name: str

    def write_storyboard(self, request: ScriptRequest) -> dict[str, Any]:
        """Return a dict matching `request.schema`."""


class ImageProvider(Protocol):
    name: str
    model: str

    def generate(self, prompt: str, aspect: str, out_path: Path) -> None:
        """Write a key frame (any size, roughly `aspect`) as PNG to `out_path`."""


class VideoProvider(Protocol):
    name: str
    model: str

    def animate(self, image_path: Path, prompt: str, aspect: str, out_path: Path) -> None:
        """Turn a key frame into a short MP4 motion clip at `out_path`."""


class TTSProvider(Protocol):
    name: str
    model: str

    def synthesize(self, text: str, language: str, out_path: Path) -> None:
        """Write the spoken `text` as a WAV file to `out_path`."""
