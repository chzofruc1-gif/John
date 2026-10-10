"""Episode script model — one bilingual master script drives every output.

Visuals are shared across languages; each line of dialogue carries both a Chinese and an English
version, and every factual statement points at a claim with its source so the script can be
fact-checked before anything is generated.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import LANGS

CAMERA_MOVES = ("zoom-in", "zoom-out", "pan-left", "pan-right", "static")
SCENE_KINDS = ("illustration", "diagram")
DIAGRAM_TEMPLATES = ("chapter", "quote", "flow", "compare", "timeline", "stat")
CONFIDENCE = ("established", "debated", "traditional")
NARRATOR = "narrator"

Bi = dict[str, str]  # {"zh": ..., "en": ...}


def bi(value: Any) -> Bi:
    if isinstance(value, dict):
        return {lang: str(value.get(lang, "") or "").strip() for lang in LANGS}
    text = str(value or "").strip()
    return {lang: text for lang in LANGS}


@dataclass
class Line:
    speaker: str          # "narrator" or a cast id
    text: Bi
    delivery: str = ""    # acting note for TTS, e.g. "deadpan", "smug"


@dataclass
class DiagramItem:
    primary: Bi
    secondary: Bi


@dataclass
class Diagram:
    template: str
    title: Bi
    items: list[DiagramItem] = field(default_factory=list)
    original: str = ""    # classical Chinese source text (quote template)
    source: str = ""      # e.g. 《管子·海王》


@dataclass
class Scene:
    id: str
    part: int             # which short the scene belongs to in split outputs (1-based)
    kind: str
    lines: list[Line]
    visual_prompt: str = ""       # illustration: English prompt for one frame
    motion: str = ""              # illustration: what moves when the frame is animated (image-to-video)
    characters: list[str] = field(default_factory=list)
    camera: str = "zoom-in"
    diagram: Diagram | None = None
    on_screen: Bi = field(default_factory=lambda: bi(""))
    claims: list[str] = field(default_factory=list)
    chapter: Bi = field(default_factory=lambda: bi(""))   # non-empty starts a YouTube chapter


@dataclass
class CastMember:
    id: str
    name: Bi
    look: str             # English visual description, reused verbatim in every prompt
    gender: str = "male"
    role: Bi = field(default_factory=lambda: bi(""))


@dataclass
class Claim:
    id: str
    statement: Bi
    source: str           # primary source or scholarship, as specific as possible
    quote: str = ""       # original wording when citing a classical text
    confidence: str = "established"
    note: Bi = field(default_factory=lambda: bi(""))


@dataclass
class Episode:
    number: int
    title: Bi
    logline: Bi
    cast: list[CastMember]
    scenes: list[Scene]
    claims: list[Claim]
    tags: dict[str, list[str]] = field(default_factory=dict)
    approved: bool = False
    revision: int = 1

    # ----- helpers ------------------------------------------------------------
    def speakers(self) -> set[str]:
        return {line.speaker for s in self.scenes for line in s.lines}

    def cast_member(self, cid: str) -> CastMember | None:
        return next((c for c in self.cast if c.id == cid), None)

    def speaker_name(self, speaker: str, lang: str) -> str:
        member = self.cast_member(speaker)
        return member.name[lang] if member else ""

    def parts(self) -> list[int]:
        return sorted({s.part for s in self.scenes})

    def validate(self) -> list[str]:
        """Return a list of problems (empty if the script is consistent)."""
        problems = []
        cast_ids = {c.id for c in self.cast}
        claim_ids = {c.id for c in self.claims}
        seen = set()
        for s in self.scenes:
            if s.id in seen:
                problems.append(f"duplicate scene id {s.id}")
            seen.add(s.id)
            if s.kind not in SCENE_KINDS:
                problems.append(f"{s.id}: unknown kind {s.kind!r}")
            if s.kind == "diagram" and (not s.diagram or s.diagram.template not in DIAGRAM_TEMPLATES):
                problems.append(f"{s.id}: diagram scene needs a valid diagram.template")
            if s.kind == "illustration" and not s.visual_prompt:
                problems.append(f"{s.id}: illustration scene has no visual_prompt")
            for line in s.lines:
                if line.speaker != NARRATOR and line.speaker not in cast_ids:
                    problems.append(f"{s.id}: speaker {line.speaker!r} is not in the cast")
                for lang in LANGS:
                    if not line.text.get(lang):
                        problems.append(f"{s.id}: a line by {line.speaker} is missing its {lang} text")
            for cid in s.characters:
                if cid not in cast_ids:
                    problems.append(f"{s.id}: character {cid!r} is not in the cast")
            for claim in s.claims:
                if claim not in claim_ids:
                    problems.append(f"{s.id}: unknown claim {claim!r}")
        return problems

    # ----- (de)serialisation --------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Episode":
        def diagram(raw: dict | None) -> Diagram | None:
            if not raw or not raw.get("template"):
                return None
            return Diagram(
                template=raw["template"],
                title=bi(raw.get("title")),
                items=[DiagramItem(bi(i.get("primary")), bi(i.get("secondary"))) for i in raw.get("items", [])],
                original=str(raw.get("original", "")),
                source=str(raw.get("source", "")),
            )

        scenes = []
        for i, s in enumerate(d.get("scenes", []), start=1):
            kind = s.get("kind", "illustration")
            camera = s.get("camera", "zoom-in")
            scenes.append(Scene(
                id=str(s.get("id") or f"s{i:02d}"),
                part=max(1, int(s.get("part", 1) or 1)),
                kind=kind,
                lines=[Line(str(l.get("speaker", NARRATOR)), bi(l.get("text")), str(l.get("delivery", "")))
                       for l in s.get("lines", [])],
                visual_prompt=str(s.get("visual_prompt", "")).strip(),
                motion=str(s.get("motion", "")).strip(),
                characters=[str(c) for c in s.get("characters", [])],
                camera=camera if camera in CAMERA_MOVES else "zoom-in",
                diagram=diagram(s.get("diagram")) if kind == "diagram" else None,
                on_screen=bi(s.get("on_screen")),
                claims=[str(c) for c in s.get("claims", [])],
                chapter=bi(s.get("chapter")),
            ))
        return cls(
            number=int(d.get("number", 1)),
            title=bi(d.get("title")),
            logline=bi(d.get("logline")),
            cast=[CastMember(str(c["id"]), bi(c.get("name")), str(c.get("look", "")), str(c.get("gender", "male")),
                             bi(c.get("role"))) for c in d.get("cast", [])],
            scenes=scenes,
            claims=[Claim(str(c["id"]), bi(c.get("statement")), str(c.get("source", "")), str(c.get("quote", "")),
                          c.get("confidence") if c.get("confidence") in CONFIDENCE else "established",
                          bi(c.get("note"))) for c in d.get("claims", [])],
            tags={k: [str(t) for t in v] for k, v in (d.get("tags") or {}).items()},
            approved=bool(d.get("approved", False)),
            revision=int(d.get("revision", 1)),
        )

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Episode":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
