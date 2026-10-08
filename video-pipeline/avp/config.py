"""Pipeline configuration: dataclass defaults, overlaid by a TOML file, overlaid by CLI flags."""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


STYLE_PRESETS: dict[str, str] = {
    "cinematic": "cinematic photography, dramatic lighting, shallow depth of field, 35mm film look, rich color grading",
    "documentary": "documentary photography, natural light, realistic, high detail, editorial",
    "anime": "anime illustration, cel shading, vibrant colors, detailed painted backgrounds, key visual quality",
    "3d": "stylized 3D render, soft global illumination, clay-like materials, clean and cute",
    "ink": "traditional Chinese ink wash painting, xuan paper texture, elegant brush strokes, muted palette with red accents",
    "flat": "flat vector illustration, bold geometric shapes, limited pastel palette, clean infographic style",
}

ASPECTS = ("16:9", "9:16", "1:1")


@dataclass
class ProjectConfig:
    workspace: str = "projects"
    language: str = "zh"
    aspect: str = "16:9"
    duration: int = 60
    scenes: int = 6
    style: str = "cinematic"

    @property
    def style_prompt(self) -> str:
        return STYLE_PRESETS.get(self.style, self.style)


@dataclass
class ProvidersConfig:
    llm: str = "gemini"
    image: str = "gemini"
    tts: str = "gemini"
    video: str = "none"


@dataclass
class GeminiConfig:
    api_key_env: str = "GEMINI_API_KEY"
    script_model: str = "gemini-2.5-pro"
    image_model: str = "gemini-2.5-flash-image"
    tts_model: str = "gemini-2.5-flash-preview-tts"
    voice: str = "Kore"
    tts_style: str = "Read this as a warm, engaging documentary narrator:"
    veo_model: str = "veo-3.1-fast-generate-preview"


@dataclass
class RenderConfig:
    height: int = 1080
    fps: int = 30
    transition: float = 0.5
    transition_style: str = "fade"
    subtitles: str = "burn"
    title_card: bool = True
    font: str = ""
    bgm: str = ""
    bgm_volume: float = 0.15
    crf: int = 20
    loudnorm: bool = True

    def frame_size(self, aspect: str) -> tuple[int, int]:
        """Output (width, height); `height` is the short side. Always even for x264."""
        w, h = (int(x) for x in aspect.split(":"))
        short = self.height - self.height % 2
        if w >= h:
            width = round(short * w / h / 2) * 2
            return width, short
        return short, round(short * h / w / 2) * 2


@dataclass
class RuntimeConfig:
    max_workers: int = 2
    retries: int = 3


@dataclass
class Config:
    project: ProjectConfig = field(default_factory=ProjectConfig)
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        cfg = cls()
        merge_into(cfg, data)
        return cfg

    def validate(self) -> None:
        if self.project.aspect not in ASPECTS:
            raise ValueError(f"aspect must be one of {ASPECTS}, got {self.project.aspect!r}")
        if self.project.language not in ("zh", "en"):
            raise ValueError("language must be 'zh' or 'en'")
        if self.project.scenes < 1:
            raise ValueError("scenes must be >= 1")
        if self.render.subtitles not in ("burn", "soft", "both", "none"):
            raise ValueError("render.subtitles must be burn | soft | both | none")
        if self.render.transition < 0:
            raise ValueError("render.transition must be >= 0")


def merge_into(target: Any, data: dict[str, Any]) -> None:
    """Recursively copy known keys from `data` onto a dataclass, coercing to the field's type."""
    known = {f.name: f for f in fields(target)}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"unknown config key: {key!r}")
        current = getattr(target, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"config section {key!r} must be a table")
            merge_into(current, value)
        elif value is not None:
            setattr(target, key, type(current)(value) if not isinstance(current, bool) else bool(value))


def load_config(path: str | Path | None) -> Config:
    cfg = Config()
    if path:
        with open(path, "rb") as fh:
            merge_into(cfg, tomllib.load(fh))
    return cfg
