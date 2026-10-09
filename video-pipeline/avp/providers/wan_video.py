"""Wan image-to-video (万相) on Alibaba Cloud Model Studio (百炼 / DashScope) — plain HTTPS, no SDK.

The video-synthesis endpoint is asynchronous: submit a task with the still as a base64 data URI, poll
the task until it succeeds, then download the MP4 from a short-lived signed OSS URL.
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

from ..config import WanVideoConfig
from .qwen_image import billing_hint

log = logging.getLogger(__name__)
ENDPOINTS = {
    "cn": "https://dashscope.aliyuncs.com",
    "intl": "https://dashscope-intl.aliyuncs.com",
}
SUBMIT = "/api/v1/services/aigc/video-generation/video-synthesis"
RETRYABLE = {408, 429, 500, 502, 503, 504}
DONE = {"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}


class WanVideo:
    name = "wan"

    def __init__(self, cfg: WanVideoConfig, retries: int):
        if cfg.region not in ENDPOINTS:
            raise ValueError(f"[wan_video].region must be one of {', '.join(ENDPOINTS)}")
        key = os.environ.get(cfg.api_key_env, "")
        if not key and not cfg.auth_via_proxy:
            raise RuntimeError(f"set the {cfg.api_key_env} environment variable for Wan video "
                               "(or [wan_video] auth_via_proxy = true if a proxy injects the key)")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self.http = httpx.Client(base_url=cfg.base_url or ENDPOINTS[cfg.region], headers=headers, timeout=120)
        self.download = httpx.Client(timeout=300, follow_redirects=True)
        self.cfg = cfg
        self.model = cfg.model
        self.retries = retries

    @staticmethod
    def image_data_uri(image: Path, long_side: int = 1280) -> str:
        from PIL import Image

        im = Image.open(image).convert("RGB")
        im.thumbnail((long_side, long_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=92)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    def _payload(self, image: Path, prompt: str) -> dict:
        prompt = " ".join(p for p in (prompt, self.cfg.motion_style) if p)
        inp = {"prompt": prompt, "img_url": self.image_data_uri(image)}
        if self.cfg.negative_prompt:
            inp["negative_prompt"] = self.cfg.negative_prompt
        return {"model": self.model, "input": inp,
                "parameters": {"resolution": self.cfg.resolution, "prompt_extend": self.cfg.prompt_extend}}

    def _post(self, payload: dict) -> str:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.http.post(SUBMIT, json=payload, headers={"X-DashScope-Async": "enable"})
                if resp.status_code < 400:
                    data = resp.json()
                    task = (data.get("output") or {}).get("task_id")
                    if not task:
                        raise RuntimeError(f"Wan returned no task id: {str(data)[:300]}")
                    return task
                if resp.status_code not in RETRYABLE:
                    raise RuntimeError(f"Wan HTTP {resp.status_code}: {resp.text[:400]}{billing_hint(resp.text)}")
                last = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            except httpx.TransportError as exc:
                last = exc
            if attempt < self.retries:
                delay = (20 if "429" in str(last) else 2 ** (attempt + 1)) + random.random()
                log.warning("wan: %s, retrying in %.0fs", last, delay)
                time.sleep(delay)
        raise RuntimeError(f"Wan submit failed: {last}")

    def _wait(self, task: str) -> str:
        deadline = time.monotonic() + self.cfg.timeout
        while time.monotonic() < deadline:
            time.sleep(self.cfg.poll_interval)
            try:
                out = (self.http.get(f"/api/v1/tasks/{task}").json().get("output") or {})
            except (httpx.TransportError, ValueError) as exc:  # a dropped poll is not a failed task
                log.warning("wan: polling %s: %s", task, exc)
                continue
            status = out.get("task_status")
            if status == "SUCCEEDED":
                return out["video_url"]
            if status in DONE:
                raise RuntimeError(f"Wan task {status}: {out.get('code', '')} {out.get('message', '')}".strip())
        raise RuntimeError(f"Wan task {task} did not finish within {self.cfg.timeout:.0f}s")

    def animate(self, image: Path, prompt: str, out_path: Path) -> None:
        url = self._wait(self._post(self._payload(image, prompt)))
        for attempt in range(4):
            try:
                resp = self.download.get(url)
                resp.raise_for_status()
                out_path.write_bytes(resp.content)
                return
            except httpx.TransportError as exc:
                if attempt == 3:
                    raise RuntimeError(f"generated, but could not download the clip from {httpx.URL(url).host} "
                                       f"({exc}); allow that host in your network settings") from exc
                time.sleep(2 ** attempt + random.random())
