"""CosyVoice speech via Alibaba Cloud Model Studio (百炼 / DashScope) — plain HTTPS, no SDK.

Uses the non-realtime SpeechSynthesizer HTTP API in SSE mode, so audio arrives inline as base64 PCM
chunks: no WebSocket and no download from a second (OSS) host, which keeps it working behind strict
proxies. Key: DASHSCOPE_API_KEY (or [cosyvoice].api_key_env), sent as `Authorization: Bearer`.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import time
import wave
from pathlib import Path

import httpx

from ..config import CosyVoiceConfig

log = logging.getLogger(__name__)
ENDPOINTS = {
    "cn": "https://dashscope.aliyuncs.com",
    "intl": "https://dashscope-intl.aliyuncs.com",
}
PATH = "/api/v1/services/audio/tts/SpeechSynthesizer"
SAMPLE_RATE = 24000
RETRYABLE = {408, 429, 500, 502, 503, 504}


class CosyVoiceError(RuntimeError):
    pass


def parse_sse_audio(lines) -> bytes:
    """Collect base64 PCM chunks from a DashScope SSE stream; raise on an error event."""
    pcm = bytearray()
    for raw in lines:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if not line.startswith("data:") or not line[5:].strip():
            continue
        event = json.loads(line[5:])
        if event.get("code"):
            raise CosyVoiceError(f"{event.get('code')}: {event.get('message')}")
        audio = (event.get("output") or {}).get("audio") or {}
        if audio.get("data"):
            pcm += base64.b64decode(audio["data"])
    return bytes(pcm)


class CosyVoiceTTS:
    name = "cosyvoice"

    def __init__(self, cfg: CosyVoiceConfig, retries: int):
        if cfg.region not in ENDPOINTS:
            raise ValueError(f"[cosyvoice].region must be one of {', '.join(ENDPOINTS)}")
        key = os.environ.get(cfg.api_key_env, "")
        if not key and not cfg.auth_via_proxy:
            raise RuntimeError(f"set the {cfg.api_key_env} environment variable for CosyVoice "
                               "(or [cosyvoice] auth_via_proxy = true if a proxy injects the key)")
        if not key:
            log.info("cosyvoice: no %s set; relying on the network proxy to add credentials", cfg.api_key_env)
        headers = {"X-DashScope-SSE": "enable", "Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self.http = httpx.Client(base_url=cfg.base_url or ENDPOINTS[cfg.region], headers=headers, timeout=cfg.timeout)
        self.cfg = cfg
        self.model = cfg.model
        self.retries = retries

    def _request(self, payload: dict) -> bytes:
        with self.http.stream("POST", PATH, json=payload) as resp:
            if resp.status_code >= 400:
                body = resp.read().decode("utf-8", "replace")[:500]
                raise httpx.HTTPStatusError(f"HTTP {resp.status_code}: {body}", request=resp.request, response=resp)
            return parse_sse_audio(resp.iter_lines())

    def synthesize(self, text: str, voice: str, style: str, language: str, out_path: Path) -> None:
        if not voice:
            raise CosyVoiceError(f"no CosyVoice voice configured for language {language!r} (see [narrator] / [voices])")
        payload = {"model": self.model, "input": {
            "text": text, "voice": voice, "format": "pcm", "sample_rate": SAMPLE_RATE,
        }}
        if self.cfg.speech_rate != 1.0:
            payload["input"]["rate"] = self.cfg.speech_rate
        if self.cfg.use_instruction and style:
            payload["input"]["instruction"] = style[:128]  # only some models/voices accept instructions
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                pcm = self._request(payload)
                if not pcm:
                    raise CosyVoiceError("CosyVoice returned no audio")
                with wave.open(str(out_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(SAMPLE_RATE)
                    wf.writeframes(pcm)
                return
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code not in RETRYABLE:
                    break
            except (httpx.TransportError, CosyVoiceError) as exc:
                last_error = exc
                if isinstance(exc, CosyVoiceError) and "Throttling" not in str(exc):
                    break
            if attempt < self.retries:
                delay = 2 ** (attempt + 1) + random.random()
                log.warning("cosyvoice: %s, retrying in %.0fs", last_error, delay)
                time.sleep(delay)
        raise CosyVoiceError(f"CosyVoice failed for voice {voice!r}: {last_error}")
