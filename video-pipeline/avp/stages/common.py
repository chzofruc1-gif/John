from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Iterable, TypeVar

from ..config import SeriesConfig
from ..project import EpisodeDir, Series
from ..providers import Providers

log = logging.getLogger("avp")
T = TypeVar("T")


class ReviewRequired(RuntimeError):
    pass


@dataclass
class Context:
    episode: EpisodeDir
    series: Series
    config: SeriesConfig
    providers: Providers
    force: bool = False
    only_scenes: set[str] = field(default_factory=set)   # empty = all scenes
    only_outputs: set[str] = field(default_factory=set)  # empty = all outputs
    skip_review: bool = False
    dry_run: bool = False          # plan only: count what would be generated, call no provider
    adopt: bool = False            # accept files already on disk (made by hand or another agent) as up to date
    report: dict[str, dict] = field(default_factory=dict)   # per-stage counts, read by avp.api
    planned: set[str] = field(default_factory=set)          # dry run: state keys that would be regenerated

    def note(self, stage: str, **counts) -> None:
        entry = self.report.setdefault(stage, {})
        for key, value in counts.items():
            entry[key] = entry.get(key, 0) + value if isinstance(value, int) else value

    def forced(self, scene_id: str) -> bool:
        """--force regenerates only the scenes picked with --scenes (or everything if none given)."""
        return self.force and (not self.only_scenes or scene_id in self.only_scenes)

    def require_approval(self, stage: str) -> None:
        script = self.episode.load_script()
        if not script.approved and not self.skip_review:
            raise ReviewRequired(
                f"{stage}: script.json is not approved yet. Review script.md (and fact-check it), edit "
                f"script.json or run `avp revise`, then `avp approve {self.episode.root}`. "
                "Use --skip-review to generate a draft anyway."
            )


def parallel(ctx: Context, items: Iterable[T], label: str, work: Callable[[T], str], name: Callable[[T], str]) -> None:
    """Run `work` over items with the configured concurrency.
    `work` returns 'made', 'cached', 'skipped' (gave up but non-fatal), 'planned' (dry run) or 'adopted'."""
    errors: list[str] = []
    made = cached = skipped = planned = adopted = 0
    with ThreadPoolExecutor(max_workers=max(1, ctx.config.runtime.max_workers)) as pool:
        futures = {pool.submit(work, item): item for item in items}
        for fut in as_completed(futures):
            item = futures[fut]
            try:
                result = fut.result()
                if result == "cached":
                    cached += 1
                elif result == "skipped":
                    skipped += 1
                elif result == "planned":
                    planned += 1
                elif result == "adopted":
                    adopted += 1
                else:
                    made += 1
                    log.info("  [%s] %s", label, name(item))
            except Exception as exc:  # keep going; report every failure at the end
                errors.append(f"{name(item)}: {exc}")
                log.error("  [%s] %s failed: %s", label, name(item), exc)
    ctx.note(label, made=made, cached=cached, skipped=skipped, planned=planned, adopted=adopted, failed=len(errors))
    if ctx.dry_run:
        log.info("  [%s] would generate %d, %d up to date", label, planned, cached)
    else:
        log.info("  [%s] %d generated, %d up to date%s", label, made, cached, f", {skipped} skipped" if skipped else "")
    if errors:
        raise RuntimeError(f"{label}: {len(errors)} item(s) failed — fix and re-run:\n  " + "\n  ".join(errors))
