"""Series bible: everything that stays constant from episode to episode.

Loaded from `series.toml` in the series folder. Unknown keys are rejected so typos surface early.
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

LANGS = ("zh", "en")
ASPECTS = ("16:9", "9:16", "1:1")


@dataclass
class SeriesInfo:
    name_zh: str = "未命名栏目"
    name_en: str = "Untitled Series"
    # What the channel is about and who it is for; injected into research + script prompts.
    premise: str = ""
    audience_zh: str = "对历史和经济感兴趣的中文观众"
    audience_en: str = "curious English-speaking viewers with no background in Chinese history"
    # Voice and humour of the show.
    tone: str = ""
    # Global art direction appended to every illustration prompt.
    art_style: str = ""
    # Rules the writer must follow (accuracy, sensitivity, house style).
    rules: list[str] = field(default_factory=list)


@dataclass
class EpisodeDefaults:
    minutes: float = 10.0          # target length of the full (English) cut
    seconds_per_scene: float = 18.0
    zh_parts: int = 3              # the Chinese vertical cut is split into this many shorts


@dataclass
class Voice:
    """Voice for one speaker. Characters without an explicit voice get one from the pool."""
    zh: str = ""
    en: str = ""
    style_zh: str = ""
    style_en: str = ""


@dataclass
class CharacterSpec:
    id: str = ""
    name_zh: str = ""
    name_en: str = ""
    # English visual description used for the reference sheet and every illustration.
    look: str = ""
    gender: str = "male"
    voice: Voice = field(default_factory=Voice)


@dataclass
class OutputSpec:
    id: str = ""
    language: str = "en"
    aspect: str = "16:9"
    split_parts: bool = False      # one file per `part` (for short-form platforms)
    subtitles: str = "burn"        # burn | soft | both | none
    platform: str = "youtube"      # youtube | douyin | xiaohongshu | ... (drives publish metadata)


@dataclass
class ProvidersConfig:
    """Which backend each job uses. Mix freely, e.g. research on Gemini (web search), script on a local
    Qwen via Ollama, art on a local Stable Diffusion, voices with Piper."""
    research: str = ""   # "" = same as llm.  anthropic | gemini | openai | mock
    llm: str = "gemini"  # anthropic | gemini | openai | mock
    image: str = "gemini"  # gemini | openai | sdwebui | mock
    tts: str = "gemini"  # gemini | openai | command | mock


@dataclass
class GeminiConfig:
    api_key_env: str = "GEMINI_API_KEY"
    research_model: str = "gemini-2.5-pro"
    script_model: str = "gemini-2.5-pro"
    image_model: str = "gemini-2.5-flash-image"
    tts_model: str = "gemini-2.5-flash-preview-tts"


@dataclass
class AnthropicConfig:
    """Claude via the official SDK (credentials: ANTHROPIC_API_KEY or `ant auth login`)."""
    model: str = "claude-opus-5-5"
    research_model: str = ""       # "" = same as model
    effort: str = "high"           # low | medium | high | xhigh | max
    max_tokens: int = 64000        # requests stream, so long scripts are fine
    web_search_max_uses: int = 15
    fallbacks: bool = True         # server-side fallback model if a request is declined


@dataclass
class EndpointConfig:
    """An OpenAI-compatible HTTP endpoint (cloud or local)."""
    base_url: str = "http://localhost:11434/v1"
    api_key_env: str = ""          # env var holding the key; empty for local servers
    model: str = ""
    research_model: str = ""       # llm only; "" = same as model
    json_mode: bool = True         # send response_format=json_object (turn off if the server rejects it)
    max_tokens: int = 0            # 0 = server default; raise it if long scripts get cut off
    size: str = "1536x1024"        # image only
    timeout: float = 600.0


@dataclass
class OpenAIConfig:
    llm: EndpointConfig = field(default_factory=lambda: EndpointConfig(model="qwen2.5:14b"))
    image: EndpointConfig = field(default_factory=lambda: EndpointConfig(
        base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY", model="gpt-image-1"))
    tts: EndpointConfig = field(default_factory=lambda: EndpointConfig(
        base_url="http://localhost:8880/v1", model="kokoro"))


@dataclass
class SDWebUIConfig:
    base_url: str = "http://127.0.0.1:7860"
    checkpoint: str = ""
    long_side: int = 1344
    steps: int = 28
    cfg_scale: float = 6.0
    sampler: str = "DPM++ 2M"
    negative_prompt: str = "text, letters, watermark, signature, logo, blurry, deformed hands, extra fingers"
    timeout: float = 600.0


@dataclass
class CommandTTSConfig:
    """Shell command per language; text arrives on stdin and in {text_file}; write a WAV to {out}."""
    zh: str = ""
    en: str = ""
    timeout: float = 300.0


@dataclass
class VoicePool:
    """Voices handed to characters that have none assigned (names are provider-specific)."""
    male: list[str] = field(default_factory=lambda: ["Charon", "Fenrir", "Puck", "Orus", "Iapetus"])
    female: list[str] = field(default_factory=lambda: ["Kore", "Aoede", "Zephyr", "Leda", "Despina"])


@dataclass
class RenderConfig:
    height: int = 1080             # short side, pixels
    fps: int = 30
    transition: float = 0.4
    transition_style: str = "fade"
    font: str = ""                 # CJK-capable .ttf/.ttc; auto-detected when empty
    bgm: str = ""                  # optional music bed, looped and ducked under speech
    bgm_volume: float = 0.12
    crf: int = 20
    loudnorm: bool = True
    line_gap: float = 0.25         # silence between consecutive lines inside a scene
    chromium: str = ""             # browser for diagram rendering; auto-detected when empty
    workers: int = 0               # parallel segment renders; 0 = half the CPU cores


@dataclass
class RuntimeConfig:
    max_workers: int = 2
    retries: int = 3


@dataclass
class SeriesConfig:
    series: SeriesInfo = field(default_factory=SeriesInfo)
    episode: EpisodeDefaults = field(default_factory=EpisodeDefaults)
    narrator: Voice = field(default_factory=lambda: Voice(zh="Charon", en="Charon"))
    characters: list[CharacterSpec] = field(default_factory=list)
    outputs: list[OutputSpec] = field(default_factory=lambda: [
        OutputSpec(id="youtube_en", language="en", aspect="16:9", split_parts=False, subtitles="both", platform="youtube"),
        OutputSpec(id="douyin_zh", language="zh", aspect="9:16", split_parts=True, subtitles="burn", platform="douyin"),
    ])
    voices: VoicePool = field(default_factory=VoicePool)
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    openai: OpenAIConfig = field(default_factory=OpenAIConfig)
    sdwebui: SDWebUIConfig = field(default_factory=SDWebUIConfig)
    command_tts: CommandTTSConfig = field(default_factory=CommandTTSConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    @property
    def languages(self) -> list[str]:
        return sorted({o.language for o in self.outputs}, key=LANGS.index)

    def character(self, cid: str) -> CharacterSpec | None:
        return next((c for c in self.characters if c.id == cid), None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        for o in self.outputs:
            if o.language not in LANGS:
                raise ValueError(f"output {o.id}: language must be one of {LANGS}")
            if o.aspect not in ASPECTS:
                raise ValueError(f"output {o.id}: aspect must be one of {ASPECTS}")
            if o.subtitles not in ("burn", "soft", "both", "none"):
                raise ValueError(f"output {o.id}: subtitles must be burn | soft | both | none")
        if len({o.id for o in self.outputs}) != len(self.outputs):
            raise ValueError("output ids must be unique")
        if not 0 <= self.render.transition <= 1.0:
            raise ValueError("render.transition must be between 0 and 1 second")


def frame_size(aspect: str, short_side: int) -> tuple[int, int]:
    """(width, height) with `short_side` as the smaller dimension; even numbers for x264."""
    w, h = (int(x) for x in aspect.split(":"))
    short = short_side - short_side % 2
    if w >= h:
        return round(short * w / h / 2) * 2, short
    return short, round(short * h / w / 2) * 2


def _coerce(value: Any, hint: Any, where: str) -> Any:
    origin = get_origin(hint)
    if is_dataclass(hint):
        if not isinstance(value, dict):
            raise ValueError(f"{where} must be a table")
        obj = hint()
        merge_into(obj, value, where)
        return obj
    if origin is list:
        (item,) = get_args(hint)
        if not isinstance(value, list):
            raise ValueError(f"{where} must be a list")
        return [_coerce(v, item, f"{where}[{i}]") for i, v in enumerate(value)]
    if hint is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{where} must be true/false")
        return value
    if hint in (int, float, str):
        return hint(value)
    return value


def merge_into(target: Any, data: dict[str, Any], where: str = "") -> None:
    hints = get_type_hints(type(target))
    known = {f.name for f in fields(target)}
    for key, value in data.items():
        path = f"{where}.{key}" if where else key
        if key not in known:
            raise ValueError(f"unknown config key: {path}")
        current = getattr(target, key)
        if is_dataclass(current) and isinstance(value, dict):
            merge_into(current, value, path)
        else:
            setattr(target, key, _coerce(value, hints[key], path))


def load_series_config(path: Path) -> SeriesConfig:
    cfg = SeriesConfig()
    with open(path, "rb") as fh:
        merge_into(cfg, tomllib.load(fh))
    cfg.validate()
    return cfg
