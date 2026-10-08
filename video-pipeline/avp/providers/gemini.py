"""Google Gemini backends: Gemini (script), Nano Banana / Imagen (frames), Gemini TTS (voice), Veo (motion)."""

from __future__ import annotations

import io
import json
import logging
import os
import random
import time
import wave
from pathlib import Path
from typing import Any, Callable, TypeVar

from ..config import GeminiConfig
from .base import ScriptRequest

log = logging.getLogger(__name__)
T = TypeVar("T")

TTS_SAMPLE_RATE = 24000  # Gemini TTS returns 16-bit mono PCM at 24 kHz
RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}


class GeminiClient:
    def __init__(self, cfg: GeminiConfig, retries: int = 3):
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Gemini providers need `pip install 'avp[gemini]'` (google-genai)") from exc
        api_key = os.environ.get(cfg.api_key_env)
        if not api_key:
            raise RuntimeError(f"set the {cfg.api_key_env} environment variable (or use --provider mock)")
        self.cfg = cfg
        self.types = types
        self.client = genai.Client(api_key=api_key)
        self.retries = retries

    def call(self, fn: Callable[[], T], what: str) -> T:
        """Run an API call, retrying rate limits and transient server/network errors with backoff."""
        import httpx
        from google.genai import errors

        for attempt in range(self.retries + 1):
            try:
                return fn()
            except errors.APIError as exc:
                if exc.code not in RETRYABLE_CODES or attempt == self.retries:
                    raise
                delay = (20 if exc.code == 429 else 2 ** (attempt + 1)) + random.random()
                log.warning("%s: HTTP %s, retrying in %.0fs", what, exc.code, delay)
            except (httpx.TransportError, ConnectionError, TimeoutError) as exc:
                if attempt == self.retries:
                    raise
                delay = 2 ** (attempt + 1) + random.random()
                log.warning("%s: %s, retrying in %.0fs", what, exc, delay)
            time.sleep(delay)
        raise AssertionError("unreachable")


class GeminiLLM:
    name = "gemini"

    def __init__(self, g: GeminiClient):
        self.g, self.model = g, g.cfg.script_model

    def write_storyboard(self, request: ScriptRequest) -> dict[str, Any]:
        t = self.g.types
        resp = self.g.call(lambda: self.g.client.models.generate_content(
            model=self.model,
            contents=request.prompt,
            config=t.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=request.schema,
                temperature=0.9,
            ),
        ), "storyboard")
        if not resp.text:
            raise RuntimeError("script model returned an empty response")
        return json.loads(resp.text)


class GeminiImage:
    name = "gemini"

    def __init__(self, g: GeminiClient):
        self.g, self.model = g, g.cfg.image_model

    def generate(self, prompt: str, aspect: str, out_path: Path) -> None:
        t = self.g.types
        data: bytes | None = None
        if self.model.startswith("imagen"):
            resp = self.g.call(lambda: self.g.client.models.generate_images(
                model=self.model,
                prompt=prompt,
                config=t.GenerateImagesConfig(number_of_images=1, aspect_ratio=aspect),
            ), "image")
            if resp.generated_images and resp.generated_images[0].image:
                data = resp.generated_images[0].image.image_bytes
        else:
            resp = self.g.call(lambda: self.g.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=t.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=t.ImageConfig(aspect_ratio=aspect),
                ),
            ), "image")
            for cand in resp.candidates or []:
                for part in (cand.content.parts if cand.content else None) or []:
                    if part.inline_data and part.inline_data.data:
                        data = part.inline_data.data
                        break
        if not data:
            raise RuntimeError("image model returned no image (possibly blocked by safety filters — rephrase the visual_prompt)")
        from PIL import Image

        Image.open(io.BytesIO(data)).convert("RGB").save(out_path, "PNG")


class GeminiTTS:
    name = "gemini"

    def __init__(self, g: GeminiClient):
        self.g, self.model = g, g.cfg.tts_model

    def synthesize(self, text: str, language: str, out_path: Path) -> None:
        t = self.g.types
        style = self.g.cfg.tts_style.strip()
        prompt = f"{style}\n{text}" if style else text
        resp = self.g.call(lambda: self.g.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=t.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=t.SpeechConfig(
                    voice_config=t.VoiceConfig(prebuilt_voice_config=t.PrebuiltVoiceConfig(voice_name=self.g.cfg.voice)),
                ),
            ),
        ), "tts")
        part = resp.candidates[0].content.parts[0] if resp.candidates and resp.candidates[0].content else None
        pcm = part.inline_data.data if part and part.inline_data else None
        if not pcm:
            raise RuntimeError("TTS model returned no audio")
        if pcm[:4] == b"RIFF":
            out_path.write_bytes(pcm)
            return
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(TTS_SAMPLE_RATE)
            wf.writeframes(pcm)


class GeminiVeo:
    name = "gemini"
    poll_seconds = 10
    timeout_seconds = 10 * 60

    def __init__(self, g: GeminiClient):
        self.g, self.model = g, g.cfg.veo_model

    def animate(self, image_path: Path, prompt: str, aspect: str, out_path: Path) -> None:
        t, client = self.g.types, self.g.client
        # Veo supports 16:9 and 9:16; square projects get a 16:9 clip that compose crops.
        veo_aspect = aspect if aspect in ("16:9", "9:16") else "16:9"
        op = self.g.call(lambda: client.models.generate_videos(
            model=self.model,
            prompt=prompt,
            image=t.Image(image_bytes=image_path.read_bytes(), mime_type="image/png"),
            config=t.GenerateVideosConfig(number_of_videos=1, aspect_ratio=veo_aspect),
        ), "veo")
        started = time.monotonic()
        while not op.done:
            if time.monotonic() - started > self.timeout_seconds:
                raise TimeoutError(f"Veo did not finish within {self.timeout_seconds}s")
            time.sleep(self.poll_seconds)
            op = self.g.call(lambda: client.operations.get(op), "veo poll")
        if op.error:
            raise RuntimeError(f"Veo failed: {op.error}")
        videos = op.response.generated_videos if op.response else None
        if not videos or not videos[0].video:
            raise RuntimeError("Veo returned no video (possibly blocked by safety filters)")
        video = videos[0].video
        if not video.video_bytes:
            client.files.download(file=video)
        video.save(str(out_path))
