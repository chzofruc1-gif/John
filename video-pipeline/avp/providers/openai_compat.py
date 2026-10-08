"""Vendor-neutral HTTP providers.

* OpenAI-compatible chat / images / speech endpoints — covers OpenAI, DeepSeek, Qwen (DashScope),
  Kimi, GLM, OpenRouter, Anthropic's OpenAI-compatible endpoint, and local servers such as Ollama,
  LM Studio, vLLM, llama.cpp, Kokoro-FastAPI or openedai-speech.
* Stable Diffusion WebUI (AUTOMATIC1111 / Forge) txt2img API for fully local illustrations.
* Any command-line TTS (Piper, CosyVoice, F5-TTS, sherpa-onnx, macOS `say` …) via a command template.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import CommandTTSConfig, EndpointConfig, SDWebUIConfig
from .base import ResearchResult

log = logging.getLogger(__name__)
RETRYABLE = {408, 409, 429, 500, 502, 503, 504}


class HTTPClient:
    def __init__(self, cfg: EndpointConfig, retries: int):
        self.cfg = cfg
        self.retries = retries
        key = os.environ.get(cfg.api_key_env, "") if cfg.api_key_env else ""
        if cfg.api_key_env and not key and not _is_local(cfg.base_url):
            raise RuntimeError(f"set the {cfg.api_key_env} environment variable for {cfg.base_url}")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self.http = httpx.Client(base_url=cfg.base_url.rstrip("/") + "/", headers=headers, timeout=cfg.timeout)

    def post(self, path: str, payload: dict, what: str) -> httpx.Response:
        for attempt in range(self.retries + 1):
            try:
                resp = self.http.post(path.lstrip("/"), json=payload)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise
                delay = 2 ** (attempt + 1) + random.random()
                log.warning("%s: %s, retrying in %.0fs", what, exc, delay)
            else:
                if resp.status_code < 400:
                    return resp
                if resp.status_code not in RETRYABLE or attempt == self.retries:
                    raise RuntimeError(f"{what}: HTTP {resp.status_code}: {resp.text[:500]}")
                delay = (20 if resp.status_code == 429 else 2 ** (attempt + 1)) + random.random()
                log.warning("%s: HTTP %s, retrying in %.0fs", what, resp.status_code, delay)
            time.sleep(delay)
        raise AssertionError("unreachable")


def _is_local(url: str) -> bool:
    return bool(re.match(r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])", url))


def extract_json(text: str) -> dict:
    """Parse a JSON object from a model reply that may wrap it in prose or ``` fences."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model reply contains no JSON object")
    return json.loads(text[start:end + 1])


# ----- LLM ----------------------------------------------------------------------

class OpenAICompatLLM:
    name = "openai"

    def __init__(self, cfg: EndpointConfig, retries: int):
        self.client = HTTPClient(cfg, retries)
        self.cfg = cfg
        self.model = cfg.model

    def _chat(self, prompt: str, what: str, model: str | None = None, json_mode: bool = False,
              temperature: float = 0.8) -> str:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if self.cfg.max_tokens:
            payload["max_tokens"] = self.cfg.max_tokens
        if json_mode and self.cfg.json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = self.client.post("chat/completions", payload, what).json()
        content = data["choices"][0]["message"].get("content") or ""
        # Reasoning models served locally sometimes inline their thinking.
        return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()

    def research(self, prompt: str) -> ResearchResult:
        log.warning("  [research] %s has no web search: the dossier comes from model memory — verify it, "
                    "or write research.md yourself / use a search-capable research provider", self.model)
        text = self._chat(prompt, "research", model=self.cfg.research_model or None, temperature=0.3)
        warning = ("> ⚠️ Generated without web search from model memory. Every fact must be verified "
                   "against the cited sources before use.\n\n")
        return ResearchResult(warning + text, [])

    def text(self, task: str, prompt: str) -> str:
        return self._chat(prompt, task)

    def json(self, task: str, prompt: str, schema: dict[str, Any], hints: dict[str, Any]) -> dict[str, Any]:
        full = (f"{prompt}\n\nReturn ONLY one JSON object (no prose, no code fences) that validates against this "
                f"JSON schema:\n{json.dumps(schema, ensure_ascii=False)}")
        last_error: Exception | None = None
        for attempt in range(2):
            reply = self._chat(full, task, json_mode=True)
            try:
                return extract_json(reply)
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                log.warning("  [%s] reply was not valid JSON (%s), asking again", task, exc)
                full = f"{full}\n\nYour previous reply was not valid JSON ({exc}). Output only the JSON object."
        raise RuntimeError(f"{task}: model did not return valid JSON: {last_error}")


# ----- images ---------------------------------------------------------------------

class OpenAICompatImage:
    name = "openai"

    def __init__(self, cfg: EndpointConfig, retries: int):
        self.client = HTTPClient(cfg, retries)
        self.cfg = cfg
        self.model = cfg.model

    def generate(self, prompt: str, aspect: str, out_path: Path, references: list[Path]) -> None:
        if references:
            log.debug("images endpoint ignores character reference sheets; consistency relies on the text description")
        payload = {"model": self.model, "prompt": prompt, "n": 1, "size": self.cfg.size or "1536x1024"}
        data = self.client.post("images/generations", payload, "image").json()["data"][0]
        if data.get("b64_json"):
            raw = base64.b64decode(data["b64_json"])
        else:
            raw = httpx.get(data["url"], timeout=120).content
        _save_png(raw, out_path)


class SDWebUIImage:
    """AUTOMATIC1111 / Forge WebUI started with --api. Runs entirely on your own GPU."""

    name = "sdwebui"

    def __init__(self, cfg: SDWebUIConfig, retries: int):
        self.cfg = cfg
        self.model = cfg.checkpoint or "webui-default"
        self.client = HTTPClient(EndpointConfig(base_url=cfg.base_url, timeout=cfg.timeout), retries)

    def generate(self, prompt: str, aspect: str, out_path: Path, references: list[Path]) -> None:
        w, h = (int(x) for x in aspect.split(":"))
        long_side = self.cfg.long_side
        width, height = (long_side, round(long_side * h / w / 8) * 8) if w >= h else (round(long_side * w / h / 8) * 8, long_side)
        payload: dict[str, Any] = {
            "prompt": prompt, "negative_prompt": self.cfg.negative_prompt,
            "width": width, "height": height, "steps": self.cfg.steps, "cfg_scale": self.cfg.cfg_scale,
            "sampler_name": self.cfg.sampler,
        }
        if self.cfg.checkpoint:
            payload["override_settings"] = {"sd_model_checkpoint": self.cfg.checkpoint}
        data = self.client.post("sdapi/v1/txt2img", payload, "image").json()
        _save_png(base64.b64decode(data["images"][0]), out_path)


def _save_png(raw: bytes, out_path: Path) -> None:
    import io

    from PIL import Image

    Image.open(io.BytesIO(raw)).convert("RGB").save(out_path, "PNG")


# ----- speech -----------------------------------------------------------------------

class OpenAICompatTTS:
    name = "openai"

    def __init__(self, cfg: EndpointConfig, retries: int):
        self.client = HTTPClient(cfg, retries)
        self.model = cfg.model

    def synthesize(self, text: str, voice: str, style: str, language: str, out_path: Path) -> None:
        payload = {"model": self.model, "input": text, "voice": voice, "response_format": "wav"}
        if style:
            payload["instructions"] = style  # honoured by gpt-4o-mini-tts; ignored by most local servers
        resp = self.client.post("audio/speech", payload, "tts")
        out_path.write_bytes(resp.content)


class CommandTTS:
    """Run a local TTS program. The command template gets the text on stdin and these placeholders:
    {out} output WAV path · {text_file} path of a UTF-8 file with the text · {voice} · {lang}."""

    name = "command"

    def __init__(self, cfg: CommandTTSConfig):
        self.cfg = cfg
        self.model = "local-command"

    def synthesize(self, text: str, voice: str, style: str, language: str, out_path: Path) -> None:
        template = self.cfg.zh if language == "zh" else self.cfg.en
        if not template:
            raise RuntimeError(f"[command_tts] has no command for language {language!r}")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as fh:
            fh.write(text)
            text_file = fh.name
        try:
            cmd = template.format(out=shlex.quote(str(out_path)), text_file=shlex.quote(text_file),
                                  voice=shlex.quote(voice), lang=language)
            proc = subprocess.run(cmd, shell=True, input=text, text=True, capture_output=True, timeout=self.cfg.timeout)
            if proc.returncode != 0 or not out_path.exists():
                raise RuntimeError(f"TTS command failed ({proc.returncode}): {proc.stderr.strip()[-800:]}")
        finally:
            os.unlink(text_file)
