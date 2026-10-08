"""Storyboard data model — the contract between the script stage and everything downstream."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CAMERA_MOVES = ("zoom-in", "zoom-out", "pan-left", "pan-right", "static")


@dataclass
class Scene:
    index: int
    narration: str
    visual_prompt: str
    on_screen_text: str = ""
    camera: str = "zoom-in"
    # Extra direction for the image-to-video model (Veo); falls back to visual_prompt.
    motion_prompt: str = ""

    @property
    def key(self) -> str:
        return f"{self.index:02d}"


@dataclass
class Storyboard:
    title: str
    logline: str
    visual_style: str
    language: str
    aspect: str
    scenes: list[Scene] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Storyboard":
        scenes = []
        for i, raw in enumerate(data.get("scenes", []), start=1):
            camera = raw.get("camera", "zoom-in")
            scenes.append(Scene(
                index=i,  # re-number so hand edits (insert/delete) stay consistent
                narration=str(raw.get("narration", "")).strip(),
                visual_prompt=str(raw.get("visual_prompt", "")).strip(),
                on_screen_text=str(raw.get("on_screen_text", "")).strip(),
                camera=camera if camera in CAMERA_MOVES else "zoom-in",
                motion_prompt=str(raw.get("motion_prompt", "")).strip(),
            ))
        if not scenes:
            raise ValueError("storyboard has no scenes")
        return cls(
            title=str(data.get("title", "")).strip(),
            logline=str(data.get("logline", "")).strip(),
            visual_style=str(data.get("visual_style", "")).strip(),
            language=data.get("language", "zh"),
            aspect=data.get("aspect", "16:9"),
            scenes=scenes,
        )

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Storyboard":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
