"""CosyVoice speech via Alibaba Cloud Model Studio (百炼 / DashScope), using the official `dashscope` SDK.

Key: DASHSCOPE_API_KEY (or the variable named in [cosyvoice].api_key_env). Voice ids such as
`longcheng_v2` come from the Model Studio voice list for the chosen model.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from pathlib import Path

from ..config import CosyVoiceConfig

log = logging.getLogger(__name__)
ENDPOINTS = {
    "cn": "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
    "intl": "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference",
}


class CosyVoiceTTS:
    name = "cosyvoice"
    _setup_lock = threading.Lock()

    def __init__(self, cfg: CosyVoiceConfig, retries: int):
        try:
            import dashscope
            from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("CosyVoice needs `pip install 'avp[cosyvoice]'` (dashscope SDK)") from exc
        key = os.environ.get(cfg.api_key_env, "")
        if not key:
            raise RuntimeError(f"set the {cfg.api_key_env} environment variable for CosyVoice")
        if cfg.region not in ENDPOINTS:
            raise ValueError(f"[cosyvoice].region must be one of {', '.join(ENDPOINTS)}")
        with self._setup_lock:  # the SDK reads these module globals
            dashscope.api_key = key
            dashscope.base_websocket_api_url = ENDPOINTS[cfg.region]
        self.cfg = cfg
        self.model = cfg.model
        self.retries = retries
        self._synth_cls, self._format = SpeechSynthesizer, AudioFormat.WAV_24000HZ_MONO_16BIT

    def synthesize(self, text: str, voice: str, style: str, language: str, out_path: Path) -> None:
        if not voice:
            raise RuntimeError(f"no CosyVoice voice configured for language {language!r} (see [narrator] / [voices])")
        kwargs = {"model": self.model, "voice": voice, "format": self._format, "speech_rate": self.cfg.speech_rate}
        if self.cfg.use_instruction and style:
            kwargs["instruction"] = style[:128]  # only some models/voices accept instructions
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                audio = self._synth_cls(**kwargs).call(text)  # a synthesizer instance serves one call
                if audio:
                    out_path.write_bytes(audio)
                    return
                last_error = RuntimeError("CosyVoice returned no audio")
            except Exception as exc:  # SDK raises plain exceptions for network / API errors
                last_error = exc
            if attempt < self.retries:
                delay = 2 ** (attempt + 1) + random.random()
                log.warning("cosyvoice: %s, retrying in %.0fs", last_error, delay)
                time.sleep(delay)
        raise RuntimeError(f"CosyVoice failed for voice {voice!r}: {last_error}")
