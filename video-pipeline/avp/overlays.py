"""Text graphics drawn with Pillow as transparent full-frame PNGs and composited by ffmpeg.

Rendering text ourselves avoids depending on ffmpeg builds with libass/freetype and gives
full control over CJK line-wrapping.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .subtitles import is_cjk

log = logging.getLogger(__name__)

FONT_CANDIDATES = [
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Windows
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    # Latin-only fallback
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


@lru_cache(maxsize=None)
def find_font_path(preferred: str = "") -> str | None:
    if preferred:
        if Path(preferred).exists():
            return preferred
        log.warning("font %s not found, auto-detecting", preferred)
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    log.warning("no CJK font found; set render.font in config — Chinese text may not render")
    return None


@lru_cache(maxsize=64)
def _font(path: str | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def wrap(text: str, font, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Greedy wrap: per character for CJK, per word otherwise."""
    tokens = list(text) if is_cjk(text) else text.split(" ")
    sep = "" if is_cjk(text) else " "
    lines, current = [], ""
    for tok in tokens:
        trial = f"{current}{sep}{tok}" if current else tok
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = tok
    if current:
        lines.append(current)
    return lines


class OverlayRenderer:
    def __init__(self, width: int, height: int, font_path: str | None):
        self.w, self.h = width, height
        self.font_path = font_path
        self.unit = min(width, height)  # scale typography with the short side

    def _canvas(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        return img, ImageDraw.Draw(img)

    def _draw_lines(self, draw, lines, font, center_x, top, line_gap, fill, stroke):
        y = top
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke)
            draw.text((center_x - (bbox[2] - bbox[0]) / 2 - bbox[0], y - bbox[1]), line, font=font,
                      fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0, 230))
            y += (bbox[3] - bbox[1]) + line_gap
        return y

    def _block_height(self, draw, lines, font, line_gap, stroke) -> int:
        heights = [draw.textbbox((0, 0), l, font=font, stroke_width=stroke)[3] - draw.textbbox((0, 0), l, font=font, stroke_width=stroke)[1] for l in lines]
        return sum(heights) + line_gap * max(0, len(lines) - 1)

    def subtitle(self, text: str, out: Path) -> Path:
        img, draw = self._canvas()
        font = _font(self.font_path, max(18, int(self.unit * 0.052)))
        stroke = max(2, int(self.unit * 0.004))
        lines = wrap(text, font, int(self.w * 0.86), draw)
        gap = int(self.unit * 0.012)
        block = self._block_height(draw, lines, font, gap, stroke)
        bottom_margin = int(self.h * (0.12 if self.h > self.w else 0.07))
        self._draw_lines(draw, lines, font, self.w / 2, self.h - bottom_margin - block, gap, (255, 255, 255, 255), stroke)
        img.save(out)
        return out

    def caption(self, text: str, out: Path) -> Path:
        """Lower-third style headline in a rounded box, top-left."""
        img, draw = self._canvas()
        font = _font(self.font_path, max(18, int(self.unit * 0.05)))
        pad = int(self.unit * 0.02)
        margin = int(self.unit * 0.05)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        top = margin + (int(self.h * 0.06) if self.h > self.w else 0)
        draw.rounded_rectangle([margin, top, margin + tw + pad * 2, top + th + pad * 2], radius=pad, fill=(0, 0, 0, 150))
        draw.rectangle([margin, top, margin + max(4, pad // 3), top + th + pad * 2], fill=(255, 196, 0, 255))
        draw.text((margin + pad - bbox[0] + pad // 3, top + pad - bbox[1]), text, font=font, fill=(255, 255, 255, 255))
        img.save(out)
        return out

    def title(self, title: str, logline: str, out: Path) -> Path:
        img, draw = self._canvas()
        draw.rectangle([0, 0, self.w, self.h], fill=(0, 0, 0, 110))
        tfont = _font(self.font_path, max(24, int(self.unit * 0.09)))
        sfont = _font(self.font_path, max(16, int(self.unit * 0.04)))
        stroke = max(2, int(self.unit * 0.004))
        gap = int(self.unit * 0.02)
        tlines = wrap(title, tfont, int(self.w * 0.84), draw)
        slines = wrap(logline, sfont, int(self.w * 0.78), draw) if logline else []
        total = self._block_height(draw, tlines, tfont, gap, stroke)
        if slines:
            total += gap * 2 + self._block_height(draw, slines, sfont, gap, stroke)
        y = (self.h - total) / 2
        y = self._draw_lines(draw, tlines, tfont, self.w / 2, y, gap, (255, 255, 255, 255), stroke)
        if slines:
            self._draw_lines(draw, slines, sfont, self.w / 2, y + gap, gap, (235, 235, 235, 255), max(1, stroke // 2))
        img.save(out)
        return out


def make_cover(image_path: Path, title: str, width: int, height: int, font_path: str | None, out: Path) -> Path:
    """Thumbnail: first key frame, cover-cropped, with the title burned in."""
    base = Image.open(image_path).convert("RGB")
    scale = max(width / base.width, height / base.height)
    base = base.resize((round(base.width * scale), round(base.height * scale)), Image.LANCZOS)
    left, top = (base.width - width) // 2, (base.height - height) // 2
    base = base.crop((left, top, left + width, top + height)).convert("RGBA")
    layer_path = out.with_suffix(".title.png")
    OverlayRenderer(width, height, font_path).title(title, "", layer_path)
    Image.alpha_composite(base, Image.open(layer_path)).convert("RGB").save(out, "JPEG", quality=92)
    layer_path.unlink(missing_ok=True)
    return out
