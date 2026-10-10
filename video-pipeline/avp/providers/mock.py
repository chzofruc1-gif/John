"""Offline providers: deterministic placeholder art, synthetic voice and a template script.

They let the whole pipeline (including ffmpeg compositing) run without API keys — for tests,
CI, and for dry-running layout/timing before spending money on real generations.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
import wave
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .base import ResearchResult, Source

CAMERAS = ["zoom-in", "pan-right", "zoom-out", "pan-left", "static"]
TEMPLATES = ["chapter", "quote", "flow", "compare", "timeline", "stat"]


def _sentences(text: str) -> list[str]:
    parts = [p.strip(" #-*") for p in re.split(r"(?<=[。！？!?.；;])\s*|\n+", text) if p.strip(" #-*")]
    return parts or [text.strip() or "topic"]


def _bi(zh: str, en: str) -> dict:
    return {"zh": zh, "en": en}


class MockLLM:
    """Template-driven stand-in for an LLM: produces structurally valid research, outline and script."""

    name = "mock"
    model = "template"

    def research(self, prompt: str) -> ResearchResult:
        return ResearchResult(
            "# Research dossier (mock)\n\nOffline placeholder. Run with a real LLM provider for actual research.\n",
            [Source("Example source", "https://example.org/source")],
        )

    def text(self, task: str, prompt: str) -> str:
        return f"# {task} (mock)\n\n1. Hook\n2. Context\n3. Mechanism\n4. Debate\n5. Legacy\n"

    def json(self, task: str, prompt: str, schema: dict[str, Any], hints: dict[str, Any]) -> dict[str, Any]:
        if task == "revise":
            return hints["current"]
        n, parts = int(hints["scenes"]), int(hints["parts"])
        topic = _sentences(hints["brief"])[0][:30]
        scenes = []
        part_of = [min(parts, i * parts // n + 1) for i in range(n)]
        for i in range(n):
            part = part_of[i]
            lines = [{"speaker": "narrator", "delivery": "wry",
                      "text": _bi(f"第{i + 1}幕：关于{topic}的旁白。这是一段测试用的中文解说，检查配音和字幕节奏。",
                                  f"Scene {i + 1}: narration about {topic}. This placeholder line checks voice and subtitle timing.")}]
            if i % 3 == 1:
                lines.append({"speaker": "sage", "delivery": "smug",
                              "text": _bi("这个办法，我早就想到了。", "I thought of this long ago, obviously.")})
            scene = {
                "id": f"s{i + 1:02d}", "part": part, "lines": lines, "claims": ["c1"] if i % 2 == 0 else [],
                "on_screen": _bi(f"要点{i + 1}", f"Point {i + 1}"),
                "chapter": _bi(f"第{part}章", f"Chapter {part}") if i == 0 or part != part_of[i - 1] else _bi("", ""),
                "camera": CAMERAS[i % len(CAMERAS)], "characters": [], "visual_prompt": "", "motion": "", "diagram": None,
            }
            if i % 2 == 0:
                scene.update(kind="illustration", visual_prompt=f"Placeholder illustration {i + 1} about {topic}",
                             characters=["sage"] if i % 3 == 1 else [],
                             motion="The sage waves" if i % 4 == 0 else "")
            else:
                template = TEMPLATES[(i // 2) % len(TEMPLATES)]
                items = [{"primary": _bi(f"项目{k + 1}", f"Item {k + 1}"), "secondary": _bi(f"说明{k + 1}", f"Detail {k + 1}")}
                         for k in range(3)]
                if template == "timeline":
                    items = [{"primary": _bi(y, y), "secondary": _bi(f"事件{k + 1}", f"Event {k + 1}")}
                             for k, y in enumerate(["685 BC", "81 BC", "780 AD"])]
                if template == "stat":
                    items = [{"primary": _bi("70%", "70%"), "secondary": _bi("国家收入来自盐", "of revenue came from salt")}]
                if template == "compare":
                    items = [{"primary": _bi("桑弘羊", "Sang Hongyang"), "secondary": _bi("国家专营；平抑物价；充实国库", "State monopoly; Stable prices; Full treasury")},
                             {"primary": _bi("贤良文学", "The Literati"), "secondary": _bi("与民争利；官商腐败；回归农本", "Competes with the people; Corruption; Back to farming")}]
                scene.update(kind="diagram", diagram={
                    "template": template, "title": _bi(f"图解{i + 1}", f"Diagram {i + 1}"), "items": items,
                    "original": "海王之国，谨正盐筴。", "source": "《管子·海王》",
                })
            scenes.append(scene)
        return {
            "number": hints.get("number", 1),
            "title": _bi(f"{topic}（测试）", f"{topic} (test)"),
            "logline": _bi("流水线离线演示。", "An offline pipeline dry run."),
            "cast": [{"id": "sage", "name": _bi("先生", "The Sage"), "gender": "male",
                      "look": "chibi ancient Chinese minister, round face, black hat, green robe",
                      "role": _bi("测试角色", "test character")}],
            "scenes": scenes,
            "claims": [{"id": "c1", "statement": _bi("这是一条测试用的史实。", "This is a placeholder claim."),
                        "source": "《管子·海王》", "quote": "海王之国，谨正盐筴。", "confidence": "traditional",
                        "note": _bi("托名之作。", "Attributed text.")}],
            "tags": {"zh": ["经济史", "测试"], "en": ["economic history", "test"]},
        }


class MockImage:
    name = "mock"
    model = "placeholder"

    def generate(self, prompt: str, aspect: str, out_path: Path, references: list[Path]) -> None:
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


class MockVideo:
    """A one-second slow zoom on the still, so compose's clip-then-hold path runs offline."""
    name = "mock"
    model = "zoom"

    def animate(self, image: Path, prompt: str, out_path: Path) -> None:
        from ..media import run_ffmpeg

        run_ffmpeg(["-loop", "1", "-framerate", "12", "-t", "1", "-i", str(image),
                    "-vf", "scale=640:-2,zoompan=z='1+0.002*on':d=1:s=640x360:fps=12",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out_path)])


class MockTTS:
    name = "mock"
    model = "tone"
    sample_rate = 24000

    def synthesize(self, text: str, voice: str, style: str, language: str, out_path: Path) -> None:
        # Roughly natural pacing: ~4.5 CJK chars/s or ~2.6 English words/s.
        units = len(re.findall(r"[一-鿿]", text)) / 4.5 + len(re.findall(r"[A-Za-z']+", text)) / 2.6
        seconds = max(1.0, units)
        n = int(seconds * self.sample_rate)
        syllable = self.sample_rate // 4
        pitch = 140 + int(hashlib.md5(voice.encode()).hexdigest(), 16) % 120  # distinct per speaker
        frames = bytearray()
        for i in range(n):
            env = math.sin(math.pi * (i % syllable) / syllable) ** 2  # "syllable" envelope
            sample = 0.25 * env * math.sin(2 * math.pi * pitch * i / self.sample_rate)
            frames += struct.pack("<h", int(sample * 32767))
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(bytes(frames))
