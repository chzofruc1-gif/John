"""Offline providers: deterministic placeholder frames, synthetic voice and a template script.

They let the whole pipeline (including ffmpeg compositing) run without API keys — for tests,
CI, and for dry-running layout/timing before spending money on real generations.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .base import ScriptRequest

CAMERAS = ["zoom-in", "pan-right", "zoom-out", "pan-left", "static"]


def _sentences(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?.；;])\s*|\n+", text) if p.strip()]
    return parts or [text.strip()]


class MockLLM:
    name = "mock"
    model = "template"

    def write_storyboard(self, request: ScriptRequest) -> dict[str, Any]:
        zh = request.language == "zh"
        sentences = _sentences(request.brief)
        topic = sentences[0][:24]
        scenes = []
        for i in range(request.scenes):
            source = sentences[i % len(sentences)]
            if zh:
                narration = f"第{i + 1}幕。{source} 这是一段用于测试流水线的旁白，用来检验配音、字幕和转场的节奏。"
                caption = f"第{i + 1}幕"
            else:
                narration = f"Scene {i + 1}. {source} This placeholder narration checks voice, subtitle and transition timing."
                caption = f"Part {i + 1}"
            scenes.append({
                "narration": narration,
                "visual_prompt": f"Placeholder frame {i + 1} about: {source}",
                "on_screen_text": caption,
                "camera": CAMERAS[i % len(CAMERAS)],
                "motion_prompt": "",
            })
        return {
            "title": topic,
            "logline": ("流水线离线演示" if zh else "Offline pipeline dry run"),
            "visual_style": request.style_prompt,
            "scenes": scenes,
        }


class MockImage:
    name = "mock"
    model = "placeholder"

    def generate(self, prompt: str, aspect: str, out_path: Path) -> None:
        w, h = (int(x) for x in aspect.split(":"))
        width, height = (1920, round(1920 * h / w)) if w >= h else (round(1920 * w / h), 1920)
        digest = hashlib.sha256(prompt.encode()).digest()
        c1, c2 = tuple(digest[0:3]), tuple(digest[3:6])
        img = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(img)
        for y in range(height):  # vertical gradient between two prompt-derived colours
            k = y / max(1, height - 1)
            draw.line([(0, y), (width, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(c1, c2)))
        # A grid + circles so Ken Burns motion is visible in the output.
        step = max(width, height) // 12
        for x in range(0, width, step):
            draw.line([(x, 0), (x, height)], fill=(255, 255, 255), width=1)
        for y in range(0, height, step):
            draw.line([(0, y), (width, y)], fill=(255, 255, 255), width=1)
        r = min(width, height) // 5
        cx, cy = width // 2 + (digest[6] - 128) * width // 800, height // 2
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255), width=8)
        img.save(out_path, "PNG")


class MockTTS:
    name = "mock"
    model = "tone"
    sample_rate = 24000

    def synthesize(self, text: str, language: str, out_path: Path) -> None:
        # Roughly natural pacing: ~4.5 CJK chars/s or ~2.6 English words/s.
        units = len(re.findall(r"[一-鿿]", text)) / 4.5 + len(re.findall(r"[A-Za-z']+", text)) / 2.6
        seconds = max(1.0, units)
        n = int(seconds * self.sample_rate)
        syllable = self.sample_rate // 4
        frames = bytearray()
        for i in range(n):
            env = math.sin(math.pi * (i % syllable) / syllable) ** 2  # "syllable" envelope
            sample = 0.25 * env * math.sin(2 * math.pi * 180 * i / self.sample_rate)
            frames += struct.pack("<h", int(sample * 32767))
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(bytes(frames))


class MockVideo:
    name = "mock"
    model = "ffmpeg-still"

    def animate(self, image_path: Path, prompt: str, aspect: str, out_path: Path) -> None:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-loop", "1", "-t", "4", "-i", str(image_path),
            "-vf", "scale=trunc(iw/4)*2:trunc(ih/4)*2,hue=h=t*40,format=yuv420p",
            "-r", "24", "-c:v", "libx264", "-preset", "veryfast", str(out_path),
        ], check=True)
