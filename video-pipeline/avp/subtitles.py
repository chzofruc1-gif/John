"""Split narration into subtitle cues and write SRT."""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_CHARS = {"cjk": 18, "latin": 42}
_CJK = re.compile(r"[㐀-鿿豈-﫿]")
_BREAK = re.compile(r"(?<=[，。！？；、,.!?;:：])\s*")
_TRAILING = re.compile(r"[，。；、,;：:]+$")


@dataclass
class Cue:
    start: float
    end: float
    text: str


def is_cjk(text: str) -> bool:
    """Mostly-Chinese text (an English line quoting 管仲 still wraps on words)."""
    cjk = len(_CJK.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return cjk > 0 and cjk >= latin / 3


_OPEN, _CLOSE = "《“‘（(「", "》”’）)」"


def _protected(text: str) -> set[int]:
    """Indexes where a cut would split a bracketed or quoted span (e.g. 《国富论》)."""
    blocked, depth = set(), 0
    for i, ch in enumerate(text):
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth = max(0, depth - 1)
        if depth > 0 or ch in _CLOSE:
            blocked.add(i + 1)  # cutting at i+1 would separate text[i] from text[i+1]
        if ch in _OPEN:
            blocked.add(i + 1)
    return blocked


def _word_boundaries(text: str) -> set[int] | None:
    """Positions between Chinese words (via jieba, if installed), so cuts never split a word."""
    try:
        import jieba
    except ImportError:  # pragma: no cover - optional dependency
        return None
    jieba.setLogLevel(60)
    bounds, pos = set(), 0
    for word in jieba.lcut(text):
        pos += len(word)
        bounds.add(pos)
    return bounds


def _balanced_cuts(text: str, limit: int, cjk: bool) -> list[str]:
    """Split into the fewest roughly equal pieces, moving each cut to a nearby allowed position."""
    n = -(-len(text) // limit)
    size = len(text) / n
    blocked = _protected(text)
    words = _word_boundaries(text) if cjk else None
    if words is not None:
        blocked |= {i for i in range(1, len(text)) if i not in words}
    pieces, start = [], 0
    for k in range(1, n):
        target = round(size * k)
        best = None
        for delta in range(0, max(4, limit // 3)):
            for pos in (target - delta, target + delta):
                if start < pos < len(text) and pos not in blocked and (cjk or text[pos - 1] == " " or text[pos] == " "):
                    best = pos
                    break
            if best:
                break
        best = best or target
        pieces.append(text[start:best].strip())
        start = best
    pieces.append(text[start:].strip())
    return [p for p in pieces if p]


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
        if len(current) > limit:  # a clause that is still too long: cut into balanced pieces
            pieces = _balanced_cuts(current, limit, cjk)
            lines.extend(pieces[:-1])
            current = pieces[-1]
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
