from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from ..config import Config
from ..models import Scene
from ..project import Project
from ..providers import Providers

log = logging.getLogger("avp")


@dataclass
class Context:
    project: Project
    config: Config
    providers: Providers
    force: bool = False
    only_scenes: set[int] = field(default_factory=set)  # empty = all scenes

    def selected(self, scene: Scene) -> bool:
        return not self.only_scenes or scene.index in self.only_scenes

    def forced(self, scene: Scene) -> bool:
        """--force regenerates only the scenes picked with --scenes (or all if none given)."""
        return self.force and self.selected(scene)


def for_each_scene(ctx: Context, scenes: list[Scene], label: str, work: Callable[[Scene], str]) -> None:
    """Run `work` per scene in parallel. `work` returns 'made' or 'cached'. Errors are collected."""
    errors: list[str] = []
    made = cached = 0
    with ThreadPoolExecutor(max_workers=max(1, ctx.config.runtime.max_workers)) as pool:
        futures = {pool.submit(work, s): s for s in scenes}
        for fut in as_completed(futures):
            scene = futures[fut]
            try:
                if fut.result() == "cached":
                    cached += 1
                else:
                    made += 1
                    log.info("  [%s] scene %s done", label, scene.key)
            except Exception as exc:  # keep going; report all failures at the end
                errors.append(f"scene {scene.key}: {exc}")
                log.error("  [%s] scene %s failed: %s", label, scene.key, exc)
    log.info("  [%s] %d generated, %d up to date", label, made, cached)
    if errors:
        raise RuntimeError(f"{label}: {len(errors)} scene(s) failed — fix and re-run:\n  " + "\n  ".join(errors))
