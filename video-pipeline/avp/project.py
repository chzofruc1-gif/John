"""A project is a folder holding the brief, storyboard, generated assets and build outputs.

    projects/<name>/
      brief.txt            the original request
      config.json          resolved config snapshot (reused by `avp run`)
      storyboard.json      editable shot list — edit and re-run to regenerate changed shots
      state.json           fingerprints of generated assets (drives incremental re-runs)
      scenes/01/image.png  key frame
      scenes/01/clip.mp4   optional image-to-video motion clip
      scenes/01/voice.wav  narration
      build/               intermediate renders
      final.mp4  subtitles.srt  cover.jpg
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime
from pathlib import Path

from .config import Config
from .models import Scene, Storyboard


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^\w一-鿿-]+", "-", text.strip().lower()).strip("-")
    return slug[:max_len].strip("-") or "video"


def fingerprint(*parts: object) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, ensure_ascii=False, sort_keys=True, default=str).encode())
        h.update(b"\x00")
    return h.hexdigest()[:16]


class Project:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()

    # ----- creation / loading -------------------------------------------------
    @classmethod
    def create(cls, workspace: Path, brief: str, config: Config, name: str | None = None) -> "Project":
        base = slugify(name or brief)
        root = Path(workspace) / base
        if root.exists():
            root = Path(workspace) / f"{base}-{datetime.now():%Y%m%d-%H%M%S}"
        root.mkdir(parents=True)
        project = cls(root)
        project.brief_path.write_text(brief.strip() + "\n", encoding="utf-8")
        project.save_config(config)
        return project

    @classmethod
    def open(cls, root: Path) -> "Project":
        project = cls(Path(root))
        if not project.config_path.exists():
            raise FileNotFoundError(f"not a project folder (missing config.json): {root}")
        return project

    # ----- paths --------------------------------------------------------------
    @property
    def brief_path(self) -> Path: return self.root / "brief.txt"
    @property
    def config_path(self) -> Path: return self.root / "config.json"
    @property
    def storyboard_path(self) -> Path: return self.root / "storyboard.json"
    @property
    def state_path(self) -> Path: return self.root / "state.json"
    @property
    def build_dir(self) -> Path: return self._dir(self.root / "build")
    @property
    def final_path(self) -> Path: return self.root / "final.mp4"
    @property
    def srt_path(self) -> Path: return self.root / "subtitles.srt"
    @property
    def cover_path(self) -> Path: return self.root / "cover.jpg"

    def scene_dir(self, scene: Scene) -> Path: return self._dir(self.root / "scenes" / scene.key)
    def image_path(self, scene: Scene) -> Path: return self.scene_dir(scene) / "image.png"
    def clip_path(self, scene: Scene) -> Path: return self.scene_dir(scene) / "clip.mp4"
    def voice_path(self, scene: Scene) -> Path: return self.scene_dir(scene) / "voice.wav"

    @staticmethod
    def _dir(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ----- documents ----------------------------------------------------------
    @property
    def brief(self) -> str:
        return self.brief_path.read_text(encoding="utf-8").strip()

    def load_config(self) -> Config:
        return Config.from_dict(json.loads(self.config_path.read_text(encoding="utf-8")))

    def save_config(self, config: Config) -> None:
        self.config_path.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def load_storyboard(self) -> Storyboard:
        if not self.storyboard_path.exists():
            raise FileNotFoundError("no storyboard yet — run the 'script' stage first")
        return Storyboard.load(self.storyboard_path)

    # ----- incremental build state -------------------------------------------
    def _state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {}

    def is_fresh(self, key: str, fp: str, path: Path) -> bool:
        """True if `path` exists and was produced from inputs with fingerprint `fp`."""
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
