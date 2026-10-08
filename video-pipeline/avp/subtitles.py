"""Split narration into subtitle cues and write SRT."""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CHARS = {"cjk": 16, "latin": 42}
_CJK = re.compile(r"[㐀-鿿豈-﫿]")
_BREAK = re.compile(r"(?<=[，。！？；、,.!?;:：])\s*")
_TRAILING = re.compile(r"[，。；、,;：:]+$")


@dataclass
class Cue:
    start: float
    end: float
    text: str


def is_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


def split_narration(text: str) -> list[str]:
    """Break narration into short lines: on punctuation first, then by length."""
    cjk = is_cjk(text)
    limit = MAX_CHARS["cjk" if cjk else "latin"]
    clauses = [c.strip() for c in _BREAK.split(re.sub(r"\s+", " ", text)) if c.strip()]

    lines: list[str] = []
    current = ""
    for clause in clauses:
        joined = (current + clause if cjk else f"{current} {clause}") if current else clause
        if len(joined) <= limit:
            current = joined
            continue
        if current:
            lines.append(current)
        current = clause
        while len(current) > limit:  # hard-wrap a clause that is still too long
            cut = limit
            if not cjk:
                space = current.rfind(" ", 0, limit)
                if space > limit // 2:
                    cut = space
            lines.append(current[:cut].strip())
            current = current[cut:].strip()
    if current:
        lines.append(current)
    return [stripped for line in lines if (stripped := _TRAILING.sub("", line).strip())]


def cues_for(text: str, start: float, duration: float) -> list[Cue]:
    """Spread the lines of `text` over [start, start+duration], proportional to their length."""
    lines = split_narration(text)
    total = sum(len(line) for line in lines) or 1
    cues, t = [], start
    for line in lines:
        d = duration * len(line) / total
        cues.append(Cue(round(t, 3), round(t + d, 3), line))
        t += d
    return cues


def _ts(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: list[Cue]) -> str:
    return "\n".join(f"{i}\n{_ts(c.start)} --> {_ts(c.end)}\n{c.text}\n" for i, c in enumerate(cues, start=1))
