"""Qwen-Image (千问图像) on Alibaba Cloud Model Studio (百炼 / DashScope) — plain HTTPS, no SDK.

The multimodal-generation endpoint is synchronous. Character reference sheets are sent as base64 data
URIs next to the prompt, which the Qwen-Image 2.x models use for consistent characters. The result is
a short-lived signed URL on Alibaba Cloud OSS (a different host from the API, e.g.
`dashscope-xxxx.oss-accelerate.aliyuncs.com`), so that host must be reachable too.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import random
import time
from pathlib import Path

import httpx

from ..config import QwenImageConfig

log = logging.getLogger(__name__)
ENDPOINTS = {
    "cn": "https://dashscope.aliyuncs.com",
    "intl": "https://dashscope-intl.aliyuncs.com",
}
PATH = "/api/v1/services/aigc/multimodal-generation/generation"
SIZES = {"16:9": "1664*928", "9:16": "928*1664", "1:1": "1328*1328"}
RETRYABLE = {408, 429, 500, 502, 503, 504}


class QwenImage:
    name = "qwen"

    def __init__(self, cfg: QwenImageConfig, retries: int):
        if cfg.region not in ENDPOINTS:
            raise ValueError(f"[qwen_image].region must be one of {', '.join(ENDPOINTS)}")
        key = os.environ.get(cfg.api_key_env, "")
        if not key and not cfg.auth_via_proxy:
            raise RuntimeError(f"set the {cfg.api_key_env} environment variable for Qwen-Image "
                               "(or [qwen_image] auth_via_proxy = true if a proxy injects the key)")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self.http = httpx.Client(base_url=cfg.base_url or ENDPOINTS[cfg.region], headers=headers, timeout=cfg.timeout)
        self.download = httpx.Client(timeout=cfg.timeout, follow_redirects=True)
        self.cfg = cfg
        self.model = cfg.model
        self.retries = retries

    def _payload(self, prompt: str, aspect: str, references: list[Path]) -> dict:
        content: list[dict] = []
        for ref in references[: self.cfg.max_references]:
            content.append({"image": "data:image/png;base64," + base64.b64encode(ref.read_bytes()).decode()})
        content.append({"text": prompt})
        parameters = {"size": SIZES.get(aspect, SIZES["16:9"]), "watermark": False, "prompt_extend": self.cfg.prompt_extend}
        if self.cfg.negative_prompt:
            parameters["negative_prompt"] = self.cfg.negative_prompt
        return {"model": self.model, "input": {"messages": [{"role": "user", "content": content}]},
                "parameters": parameters}

    @staticmethod
    def image_url(data: dict) -> str:
        if data.get("code"):
            raise RuntimeError(f"Qwen-Image error {data.get('code')}: {data.get('message')}")
        for choice in (data.get("output") or {}).get("choices") or []:
            for part in (choice.get("message") or {}).get("content") or []:
                if part.get("image"):
                    return part["image"]
        raise RuntimeError(f"Qwen-Image returned no image: {str(data)[:300]}")

    def _download(self, url: str, attempts: int = 4) -> httpx.Response:
        """Fetch the signed result URL; transient resets are retried, a blocked host is reported clearly."""
        host = httpx.URL(url).host
        for attempt in range(attempts):
            try:
                img = self.download.get(url)
                if img.status_code == 403 and "oss" not in img.headers.get("server", "").lower():
                    raise RuntimeError(f"generated, but downloading from {host} was refused — allow that host "
                                       "in your network settings")
                img.raise_for_status()
                return img
            except httpx.TransportError as exc:
                if attempt == attempts - 1:
                    raise RuntimeError(f"generated, but could not download the image from {host} ({exc}); if this "
                                       "keeps happening, allow that host in your network settings") from exc
                time.sleep(2 ** attempt + random.random())
        raise AssertionError("unreachable")

    def generate(self, prompt: str, aspect: str, out_path: Path, references: list[Path]) -> None:
        payload = self._payload(prompt, aspect, references)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.http.post(PATH, json=payload)
                if resp.status_code >= 400 and resp.status_code not in RETRYABLE:
                    raise RuntimeError(f"Qwen-Image HTTP {resp.status_code}: {resp.text[:400]}")
                if resp.status_code < 400:
                    url = self.image_url(resp.json())
                    img = self._download(url)
                    from PIL import Image

                    Image.open(io.BytesIO(img.content)).convert("RGB").save(out_path, "PNG")
                    return
                last_error = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            except httpx.TransportError as exc:
                last_error = exc
            if attempt < self.retries:
                delay = (20 if "429" in str(last_error) else 2 ** (attempt + 1)) + random.random()
                log.warning("qwen-image: %s, retrying in %.0fs", last_error, delay)
                time.sleep(delay)
        raise RuntimeError(f"Qwen-Image failed: {last_error}")
