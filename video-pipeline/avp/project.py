"""On-disk layout of a series and its episodes.

    <series>/
      series.toml                 series bible (style, cast, voices, outputs)
      cast/<id>.png               character reference sheets, shared by all episodes
      episodes/<NN-slug>/
        brief.md                  what this episode is about
        research.md               research dossier with sources
        outline.md                beat sheet
        script.json               bilingual master script (edit this, then `avp approve`)
        script.md                 human-readable script with footnotes, for fact-checking
        state.json                fingerprints of generated assets (drives incremental re-runs)
        assets/<scene>/image.png  illustrations
        assets/<scene>/<lang>_<n>.wav  voice lines
        build/                    intermediate renders
        out/                      final videos, subtitles, publish metadata, QC sheets
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

from .config import CharacterSpec, SeriesConfig, Voice, load_series_config
from .models import CastMember, Episode, Scene


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^\w一-鿿-]+", "-", text.strip().lower()).strip("-")
    return slug[:max_len].strip("-") or "episode"


def fingerprint(*parts: object) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, ensure_ascii=False, sort_keys=True, default=str).encode())
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


class Series:
    def __init__(self, root: Path):
        self.root = Path(root)
        if not self.config_path.exists():
            raise FileNotFoundError(f"not a series folder (missing series.toml): {root}")
        self.config: SeriesConfig = load_series_config(self.config_path)

    @property
    def config_path(self) -> Path: return self.root / "series.toml"
    @property
    def cast_dir(self) -> Path: return _mkdir(self.root / "cast")
    @property
    def episodes_dir(self) -> Path: return _mkdir(self.root / "episodes")

    def cast_sheet(self, cid: str) -> Path:
        return self.cast_dir / f"{cid}.png"

    def cast_library(self) -> dict[str, CharacterSpec]:
        """Recurring characters: series.toml entries win over ones registered by earlier episodes."""
        library: dict[str, CharacterSpec] = {}
        for path in sorted(self.cast_dir.glob("*.json")):
            d = json.loads(path.read_text(encoding="utf-8"))
            library[d["id"]] = CharacterSpec(id=d["id"], name_zh=d["name_zh"], name_en=d["name_en"],
                                             look=d["look"], gender=d.get("gender", "male"), voice=Voice(**d.get("voice", {})))
        for spec in self.config.characters:
            library[spec.id] = spec
        return library

    def register_cast(self, member: CastMember) -> CharacterSpec:
        """Add an episode's new character to the library so later episodes draw it the same way."""
        library = self.cast_library()
        if member.id in library:
            return library[member.id]
        spec = CharacterSpec(id=member.id, name_zh=member.name["zh"], name_en=member.name["en"],
                             look=member.look, gender=member.gender)
        (self.cast_dir / f"{member.id}.json").write_text(json.dumps({
            "id": spec.id, "name_zh": spec.name_zh, "name_en": spec.name_en, "look": spec.look,
            "gender": spec.gender, "voice": {"zh": "", "en": "", "style_zh": "", "style_en": ""},
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return spec

    def episodes(self) -> list[Path]:
        return sorted(p for p in self.episodes_dir.iterdir() if (p / "brief.md").exists())

    def next_number(self) -> int:
        numbers = [int(m.group(1)) for p in self.episodes() if (m := re.match(r"(\d+)", p.name))]
        return max(numbers, default=0) + 1

    def create_episode(self, brief: str, slug: str | None = None, number: int | None = None) -> "EpisodeDir":
        number = number or self.next_number()
        first_line = brief.strip().splitlines()[0] if brief.strip() else "episode"
        root = self.episodes_dir / f"{number:02d}-{slugify(slug or first_line)}"
        if root.exists():
            raise FileExistsError(f"episode folder already exists: {root}")
        root.mkdir(parents=True)
        (root / "brief.md").write_text(brief.strip() + "\n", encoding="utf-8")
        (root / "meta.json").write_text(json.dumps({"number": number}) + "\n", encoding="utf-8")
        return EpisodeDir(root)


class EpisodeDir:
    def __init__(self, root: Path):
        self.root = Path(root)
        if not self.brief_path.exists():
            raise FileNotFoundError(f"not an episode folder (missing brief.md): {root}")
        self._lock = threading.Lock()

    @property
    def series(self) -> Series:
        return Series(self.root.parent.parent)

    @property
    def number(self) -> int:
        meta = self.root / "meta.json"
        return json.loads(meta.read_text())["number"] if meta.exists() else 1

    # ----- documents ----------------------------------------------------------
    @property
    def brief_path(self) -> Path: return self.root / "brief.md"
    @property
    def research_path(self) -> Path: return self.root / "research.md"
    @property
    def outline_path(self) -> Path: return self.root / "outline.md"
    @property
    def script_path(self) -> Path: return self.root / "script.json"
    @property
    def script_md_path(self) -> Path: return self.root / "script.md"
    @property
    def state_path(self) -> Path: return self.root / "state.json"

    @property
    def brief(self) -> str:
        return self.brief_path.read_text(encoding="utf-8").strip()

    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    def load_script(self) -> Episode:
        if not self.script_path.exists():
            raise FileNotFoundError("no script.json yet — run the 'script' stage first")
        return Episode.load(self.script_path)

    # ----- asset paths --------------------------------------------------------
    @property
    def build_dir(self) -> Path: return _mkdir(self.root / "build")
    @property
    def out_dir(self) -> Path: return _mkdir(self.root / "out")

    def scene_dir(self, scene: Scene) -> Path: return _mkdir(self.root / "assets" / scene.id)
    def image_path(self, scene: Scene) -> Path: return self.scene_dir(scene) / "image.png"
    def voice_path(self, scene: Scene, lang: str, index: int) -> Path:
        return self.scene_dir(scene) / f"{lang}_{index + 1:02d}.wav"

    # ----- incremental build state -------------------------------------------
    def _state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {}

    def is_fresh(self, key: str, fp: str, path: Path) -> bool:
        with self._lock:
            return path.exists() and self._state().get(key) == fp

    def recorded(self, key: str) -> str | None:
        with self._lock:
            return self._state().get(key)

    def mark(self, key: str, fp: str) -> None:
        with self._lock:
            state = self._state()
            state[key] = fp
            self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
