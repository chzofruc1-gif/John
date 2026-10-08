"""Provider registry: maps the names used in config ([providers] section) to implementations."""

from __future__ import annotations

from ..config import Config
from .base import ImageProvider, LLMProvider, ScriptRequest, TTSProvider, VideoProvider

__all__ = ["Providers", "ScriptRequest", "build_providers"]


class Providers:
    def __init__(self, llm: LLMProvider, image: ImageProvider, tts: TTSProvider, video: VideoProvider | None):
        self.llm, self.image, self.tts, self.video = llm, image, tts, video


def build_providers(config: Config) -> Providers:
    names = config.providers
    needs_gemini = "gemini" in (names.llm, names.image, names.tts, names.video)
    gemini = None
    if needs_gemini:
        from .gemini import GeminiClient
        gemini = GeminiClient(config.gemini, retries=config.runtime.retries)

    from . import mock

    def pick(kind: str, name: str):
        if name == "mock":
            return {"llm": mock.MockLLM, "image": mock.MockImage, "tts": mock.MockTTS, "video": mock.MockVideo}[kind]()
        if name == "gemini":
            from . import gemini as g
            return {"llm": g.GeminiLLM, "image": g.GeminiImage, "tts": g.GeminiTTS, "video": g.GeminiVeo}[kind](gemini)
        raise ValueError(f"unknown {kind} provider: {name!r} (expected 'gemini' or 'mock')")

    video = None if names.video in ("", "none") else pick("video", names.video)
    return Providers(pick("llm", names.llm), pick("image", names.image), pick("tts", names.tts), video)
