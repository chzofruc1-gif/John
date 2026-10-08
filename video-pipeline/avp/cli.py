"""Command-line interface.

    avp init  SERIES_DIR                     create a series from the template
    avp new   SERIES_DIR "brief" | @brief.md  start an episode
    avp run   EPISODE_DIR [options]          run stages (stops at the review gate until approved)
    avp revise EPISODE_DIR notes.md          apply fact-check / review notes to the script
    avp approve EPISODE_DIR                  unlock production for the current script revision
    avp status SERIES_DIR | EPISODE_DIR
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

from .project import EpisodeDir, Series
from .providers import build_providers
from .stages import ORDER, STAGES
from .stages.common import Context, ReviewRequired
from .stages.writing import revise as revise_script
from .scriptdoc import render_script_md

log = logging.getLogger("avp")
TEMPLATES = Path(__file__).with_name("templates")


def _text_arg(value: str) -> str:
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    if value == "-":
        return sys.stdin.read()
    return value


def _context(episode: EpisodeDir, args) -> Context:
    series = episode.series
    cfg = series.config
    if getattr(args, "provider", None):
        cfg.providers.research = cfg.providers.llm = cfg.providers.image = cfg.providers.tts = args.provider
    if getattr(args, "workers", None):
        cfg.runtime.max_workers = args.workers
    split = lambda v: {x.strip() for x in v.split(",") if x.strip()} if v else set()  # noqa: E731
    return Context(
        episode=episode, series=series, config=cfg, providers=build_providers(cfg),
        force=getattr(args, "force", False), only_scenes=split(getattr(args, "scenes", "")),
        only_outputs=split(getattr(args, "outputs", "")), skip_review=getattr(args, "skip_review", False),
    )


def cmd_init(args) -> None:
    root = Path(args.series_dir)
    if (root / "series.toml").exists():
        sys.exit(f"{root}/series.toml already exists")
    root.mkdir(parents=True, exist_ok=True)
    template = TEMPLATES / f"{args.template}.toml"
    if not template.exists():
        sys.exit(f"unknown template {args.template!r}; available: {', '.join(p.stem for p in TEMPLATES.glob('*.toml'))}")
    shutil.copy(template, root / "series.toml")
    Series(root)  # validates
    print(f"created {root / 'series.toml'} — edit it, then: avp new {root} \"episode brief\"")


def cmd_new(args) -> None:
    series = Series(Path(args.series_dir))
    ep = series.create_episode(_text_arg(args.brief), slug=args.slug, number=args.number)
    print(f"created {ep.root}\nnext: avp run {ep.root}")


def cmd_run(args) -> None:
    episode = EpisodeDir(Path(args.episode_dir))
    ctx = _context(episode, args)
    if args.only:
        stages = [s.strip() for s in args.only.split(",")]
    else:
        start = ORDER.index(args.from_stage) if args.from_stage else 0
        end = ORDER.index(args.to_stage) + 1 if args.to_stage else len(ORDER)
        stages = ORDER[start:end]
    unknown = [s for s in stages if s not in STAGES]
    if unknown:
        sys.exit(f"unknown stage(s): {', '.join(unknown)}; stages: {', '.join(ORDER)}")
    for name in stages:
        log.info("▶ %s", name)
        t0 = time.monotonic()
        try:
            STAGES[name](ctx)
        except ReviewRequired as exc:
            log.info("⏸  %s", exc)
            return
        log.info("  (%s took %.1fs)", name, time.monotonic() - t0)
    log.info("✔ done: %s", episode.root)


def cmd_revise(args) -> None:
    episode = EpisodeDir(Path(args.episode_dir))
    ctx = _context(episode, args)
    revise_script(ctx, _text_arg(args.notes))
    print(f"revised -> {episode.script_md_path} (review again, then `avp approve {episode.root}`)")


def cmd_approve(args) -> None:
    episode = EpisodeDir(Path(args.episode_dir))
    script = episode.load_script()
    problems = script.validate()
    if problems and not args.force:
        sys.exit("script has consistency problems (use --force to approve anyway):\n  " + "\n  ".join(problems))
    script.approved = True
    script.save(episode.script_path)
    episode.script_md_path.write_text(render_script_md(script, episode.series.config), encoding="utf-8")
    print(f"approved revision {script.revision}. next: avp run {episode.root}")


def _episode_status(ep: EpisodeDir) -> str:
    marks = []
    for label, path in [("research", ep.research_path), ("outline", ep.outline_path), ("script", ep.script_path)]:
        marks.append(f"{label} {'✔' if path.exists() else '·'}")
    if ep.script_path.exists():
        script = ep.load_script()
        marks.append(f"r{script.revision} {'approved' if script.approved else 'DRAFT'}")
        art = [s for s in script.scenes if s.kind == "illustration"]
        marks.append(f"art {sum(ep.image_path(s).exists() for s in art)}/{len(art)}")
        outs = sorted(p.name for p in (ep.root / "out").glob("*.mp4")) if (ep.root / "out").exists() else []
        marks.append(f"videos {len(outs)}")
    return f"{ep.root.name}: " + " · ".join(marks)


def cmd_status(args) -> None:
    root = Path(args.path)
    if (root / "series.toml").exists():
        series = Series(root)
        cfg = series.config
        print(f"{cfg.series.name_zh} / {cfg.series.name_en} — outputs: {', '.join(o.id for o in cfg.outputs)}")
        print(f"cast library: {', '.join(series.cast_library()) or '(empty)'}")
        for p in series.episodes():
            print("  " + _episode_status(EpisodeDir(p)))
    else:
        print(_episode_status(EpisodeDir(root)))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="avp", description="AI video pipeline for faceless explainer series")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create a series folder from a template")
    p.add_argument("series_dir")
    p.add_argument("--template", default="econ-history")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("new", help="start a new episode")
    p.add_argument("series_dir")
    p.add_argument("brief", help='episode brief text, "@file.md", or "-" for stdin')
    p.add_argument("--slug")
    p.add_argument("--number", type=int)
    p.set_defaults(func=cmd_new)

    def common(p):
        p.add_argument("--provider", choices=["gemini", "openai", "mock"],
                       help="use this provider for every job (mock = offline placeholders)")
        p.add_argument("--workers", type=int, help="parallel API calls / renders")

    p = sub.add_parser("run", help="run pipeline stages")
    p.add_argument("episode_dir")
    p.add_argument("--only", help=f"comma-separated stages to run ({', '.join(ORDER)})")
    p.add_argument("--from", dest="from_stage", choices=ORDER)
    p.add_argument("--to", dest="to_stage", choices=ORDER)
    p.add_argument("--force", action="store_true", help="regenerate even if up to date")
    p.add_argument("--scenes", help="limit regeneration to these scene ids, e.g. s03,s07")
    p.add_argument("--outputs", help="limit rendering to these output ids")
    p.add_argument("--skip-review", action="store_true", help="produce a draft without approving the script")
    common(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("revise", help="apply review notes to the script")
    p.add_argument("episode_dir")
    p.add_argument("notes", help='notes text, "@notes.md", or "-" for stdin')
    common(p)
    p.set_defaults(func=cmd_revise)

    p = sub.add_parser("approve", help="approve the current script for production")
    p.add_argument("episode_dir")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("status", help="show progress of a series or episode")
    p.add_argument("path")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    for noisy in ("httpx", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        args.func(args)
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        log.error("✖ %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
