"""Provider registry: maps names in series.toml [providers] to implementations.

    anthropic  Claude via the Anthropic SDK (research with web search, outline, script)
    gemini   Google Gemini API (research with Google Search grounding, Nano Banana / Imagen, Gemini TTS)
    openai   any OpenAI-compatible endpoint — cloud (OpenAI, DeepSeek, Qwen, Kimi, OpenRouter, Claude …)
             or local (Ollama, LM Studio, vLLM, llama.cpp, Kokoro-FastAPI …); see [openai.*] in series.toml
    sdwebui  local Stable Diffusion WebUI (AUTOMATIC1111 / Forge) for illustrations
    command  any local TTS program (Piper, CosyVoice, F5-TTS, sherpa-onnx …) for voices
    mock     offline placeholders, for testing the pipeline at zero cost
"""

from __future__ import annotations

from ..config import SeriesConfig
from .base import ImageProvider, LLMProvider, ResearchResult, Source, TTSProvider

__all__ = ["Providers", "ResearchResult", "Source", "build_providers", "KINDS"]

KINDS = {
    "research": ("anthropic", "gemini", "openai", "mock"),
    "llm": ("anthropic", "gemini", "openai", "mock"),
    "image": ("gemini", "openai", "sdwebui", "mock"),
    "tts": ("gemini", "openai", "command", "mock"),
}


class Providers:
    def __init__(self, research: LLMProvider, llm: LLMProvider, image: ImageProvider, tts: TTSProvider):
        self.research, self.llm, self.image, self.tts = research, llm, image, tts


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
        if name not in KINDS[kind]:
            raise ValueError(f"unknown {kind} provider {name!r}; choose one of {', '.join(KINDS[kind])}")
        if name == "mock":
            from . import mock
            return {"research": mock.MockLLM, "llm": mock.MockLLM, "image": mock.MockImage, "tts": mock.MockTTS}[kind]()
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
        if name == "sdwebui":
            return oc.SDWebUIImage(config.sdwebui, retries)
        return oc.CommandTTS(config.command_tts)

    return Providers(
        research=pick("research", names.research or names.llm),
        llm=pick("llm", names.llm),
        image=pick("image", names.image),
        tts=pick("tts", names.tts),
    )
