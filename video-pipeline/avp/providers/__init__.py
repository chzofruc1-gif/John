"""Provider registry: maps names in series.toml [providers] to implementations.

    anthropic  Claude via the Anthropic SDK (research with web search, outline, script)
    gemini   Google Gemini API (research with Google Search grounding, Nano Banana / Imagen, Gemini TTS)
    openai   any OpenAI-compatible endpoint — cloud (OpenAI, DeepSeek, Qwen, Kimi, OpenRouter, Claude …)
             or local (Ollama, LM Studio, vLLM, llama.cpp, Kokoro-FastAPI …); see [openai.*] in series.toml
    qwen     Qwen-Image illustrations on Alibaba Cloud Model Studio (百炼, DASHSCOPE_API_KEY)
    cosyvoice  CosyVoice speech on Alibaba Cloud Model Studio (百炼, DASHSCOPE_API_KEY)
    wan      Wan image-to-video on Alibaba Cloud Model Studio (百炼), animates illustrations
    sdwebui  local Stable Diffusion WebUI (AUTOMATIC1111 / Forge) for illustrations
    command  any local TTS program (Piper, CosyVoice, F5-TTS, sherpa-onnx …) for voices
    mock     offline placeholders, for testing the pipeline at zero cost

Any job can also name a custom provider as "package.module:ClassName" (see AGENTS.md → plugins). The class is
built as ClassName(config, kind) and must implement the matching protocol in base.py; options for it can live
in series.toml under [plugins.<anything>] and are read from config.plugins.
"""

from __future__ import annotations

from ..config import SeriesConfig
from .base import ImageProvider, LLMProvider, ResearchResult, Source, TTSProvider, VideoProvider

__all__ = ["Providers", "ResearchResult", "Source", "build_providers", "KINDS", "load_plugin"]

KINDS = {
    "research": ("anthropic", "gemini", "openai", "mock"),
    "llm": ("anthropic", "gemini", "openai", "mock"),
    "image": ("gemini", "qwen", "openai", "sdwebui", "mock"),
    "tts": ("gemini", "cosyvoice", "openai", "command", "mock"),
    "video": ("wan", "mock"),
}


def is_plugin(name: str) -> bool:
    return ":" in name


def load_plugin(spec: str, config: SeriesConfig, kind: str):
    """Instantiate a custom provider from "package.module:ClassName"."""
    import importlib

    module_name, _, attr = spec.partition(":")
    try:
        cls = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"cannot load {kind} provider {spec!r}: {exc}") from exc
    provider = cls(config, kind)
    method = {"research": "research", "llm": "json", "image": "generate", "tts": "synthesize", "video": "animate"}[kind]
    if not callable(getattr(provider, method, None)):
        raise ValueError(f"{kind} provider {spec!r} has no {method}() method (see avp/providers/base.py)")
    for attr_name in ("name", "model"):
        if not hasattr(provider, attr_name):
            setattr(provider, attr_name, spec if attr_name == "name" else "")
    return provider


class _Lazy:
    """Builds a provider on first use, so a run that only voices lines never needs image or LLM keys."""

    def __init__(self, factory):
        self._factory, self._obj = factory, None

    def __getattr__(self, attr):
        if self._obj is None:
            self._obj = self._factory()
        return getattr(self._obj, attr)


class Providers:
    def __init__(self, research: LLMProvider, llm: LLMProvider, image: ImageProvider, tts: TTSProvider,
                 tts_en: TTSProvider | None = None, video: VideoProvider | None = None):
        self.research, self.llm, self.image, self.tts = research, llm, image, tts
        self._tts_en = tts_en
        self.video = video  # None: illustrations stay stills (camera moves only)

    def tts_for(self, language: str) -> TTSProvider:
        return self._tts_en if language == "en" and self._tts_en else self.tts


def build_providers(config: SeriesConfig) -> Providers:
    names = config.providers
    retries = config.runtime.retries
    cache: dict[str, object] = {}

    def gemini_client():
        if "gemini" not in cache:
            from .gemini import GeminiClient
            cache["gemini"] = GeminiClient(config.gemini, retries=retries)
        return cache["gemini"]

    def pick(kind: str, name: str):
        if is_plugin(name):
            return load_plugin(name, config, kind)
        if name not in KINDS[kind]:
            raise ValueError(f"unknown {kind} provider {name!r}; choose one of {', '.join(KINDS[kind])}")
        if name == "mock":
            from . import mock
            return {"research": mock.MockLLM, "llm": mock.MockLLM, "image": mock.MockImage, "tts": mock.MockTTS,
                    "video": mock.MockVideo}[kind]()
        if name == "anthropic":
            from .claude import ClaudeLLM
            if "anthropic" not in cache:
                cache["anthropic"] = ClaudeLLM(config.anthropic, retries)
            return cache["anthropic"]
        if name == "gemini":
            from . import gemini
            cls = {"research": gemini.GeminiLLM, "llm": gemini.GeminiLLM, "image": gemini.GeminiImage,
                   "tts": gemini.GeminiTTS}[kind]
            return cls(gemini_client())
        from . import openai_compat as oc
        if name == "openai":
            if kind in ("research", "llm"):
                return oc.OpenAICompatLLM(config.openai.llm, retries)
            if kind == "image":
                return oc.OpenAICompatImage(config.openai.image, retries)
            return oc.OpenAICompatTTS(config.openai.tts, retries)
        if name == "qwen":
            from .qwen_image import QwenImage
            return QwenImage(config.qwen_image, retries)
        if name == "cosyvoice":
            from .cosyvoice import CosyVoiceTTS
            if "cosyvoice" not in cache:
                cache["cosyvoice"] = CosyVoiceTTS(config.cosyvoice, retries)
            return cache["cosyvoice"]
        if name == "wan":
            from .wan_video import WanVideo
            return WanVideo(config.wan_video, retries)
        if name == "sdwebui":
            return oc.SDWebUIImage(config.sdwebui, retries)
        return oc.CommandTTS(config.command_tts)

    def lazy(kind: str, name: str):
        if not is_plugin(name) and name not in KINDS[kind]:  # fail fast on typos, even for unused providers
            raise ValueError(f"unknown {kind} provider {name!r}; choose one of {', '.join(KINDS[kind])}")
        return _Lazy(lambda: pick(kind, name))

    return Providers(
        research=lazy("research", names.research or names.llm),
        llm=lazy("llm", names.llm),
        image=lazy("image", names.image),
        tts=lazy("tts", names.tts),
        tts_en=lazy("tts", names.tts_en) if names.tts_en and names.tts_en != names.tts else None,
        video=lazy("video", names.video) if names.video else None,
    )
