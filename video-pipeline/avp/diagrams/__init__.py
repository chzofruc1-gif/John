"""Animated explainer diagrams: an HTML template posed frame-by-frame in headless Chromium.

The template exposes `setup(data)` and a pure `renderAt(t)`, so every frame is deterministic
(the same idea HyperFrames uses). Frames are piped as JPEGs straight into ffmpeg.
"""

from __future__ import annotations

import glob
import logging
import os
import subprocess
import threading
from pathlib import Path

from ..models import Diagram

log = logging.getLogger(__name__)
TEMPLATE = Path(__file__).with_name("template.html")

CHROMIUM_GLOBS = [
    "/opt/pw-browsers/chromium-*/chrome-linux/chrome",
    os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
    os.path.expanduser("~/Library/Caches/ms-playwright/chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"),
]


def diagram_data(diagram: Diagram, lang: str) -> dict:
    return {
        "template": diagram.template,
        "title": diagram.title[lang],
        "items": [{"primary": i.primary[lang], "secondary": i.secondary[lang]} for i in diagram.items],
        "original": diagram.original,
        "source": diagram.source,
        "seal": "史" if lang == "zh" else "史",
    }


class DiagramRenderer:
    """Owns one headless browser. Playwright's sync API is bound to the creating thread, so use one
    renderer per thread (`with DiagramRenderer() as r: ...`)."""

    def __init__(self, chromium: str = ""):
        self.chromium = chromium or os.environ.get("AVP_CHROMIUM", "")
        self._pw = self._browser = None
        self.lock = threading.Lock()

    def _start(self):
        if self._browser:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("diagram rendering needs `pip install playwright` (and a Chromium build)") from exc
        self._pw = sync_playwright().start()
        candidates = [self.chromium] if self.chromium else [""] + [p for g in CHROMIUM_GLOBS for p in sorted(glob.glob(g))]
        errors = []
        for exe in candidates:
            try:
                self._browser = self._pw.chromium.launch(executable_path=exe or None)
                return
            except Exception as exc:  # try the next candidate
                errors.append(f"{exe or 'playwright default'}: {str(exc).splitlines()[0]}")
        self.close()
        raise RuntimeError("could not launch Chromium for diagrams; run `playwright install chromium` or set "
                           "render.chromium / AVP_CHROMIUM.\n  " + "\n  ".join(errors))

    def _page(self, data: dict, width: int, height: int, duration: float):
        page = self._browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        page.goto(TEMPLATE.as_uri())
        page.evaluate("d => window.setup(d)", {**data, "width": width, "height": height, "duration": duration})
        page.evaluate("document.fonts.ready")
        return page

    def still(self, diagram: Diagram, lang: str, width: int, height: int, t: float, out: Path) -> Path:
        with self.lock:
            self._start()
            page = self._page(diagram_data(diagram, lang), width, height, max(t, 1.0))
            try:
                page.evaluate("t => window.renderAt(t)", t)
                page.screenshot(path=str(out))
            finally:
                page.close()
        return out

    def render(self, diagram: Diagram, lang: str, width: int, height: int, duration: float, fps: int,
               out: Path, crf: int = 20) -> Path:
        frames = max(1, round(duration * fps))
        with self.lock:
            self._start()
            page = self._page(diagram_data(diagram, lang), width, height, duration)
            enc = subprocess.Popen([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
                "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
                "-pix_fmt", "yuv420p", "-r", str(fps), str(out),
            ], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                settle = page.evaluate("window.settleTime()")
                frame = b""
                for i in range(frames):
                    t = i / fps
                    if not frame or t <= settle:  # once settled, every frame is identical
                        page.evaluate("t => window.renderAt(t)", t)
                        frame = page.screenshot(type="jpeg", quality=92)
                    enc.stdin.write(frame)
                enc.stdin.close()
                if enc.wait() != 0:
                    raise RuntimeError(f"ffmpeg failed encoding diagram: {enc.stderr.read().decode()[-2000:]}")
            finally:
                if enc.poll() is None:
                    enc.kill()
                page.close()
        return out

    def __enter__(self) -> "DiagramRenderer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._pw:
            self._pw.stop()
            self._pw = None

