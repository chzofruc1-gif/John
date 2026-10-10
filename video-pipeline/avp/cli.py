"""Command-line interface (a thin layer over avp.api; add --json to any command for machine-readable output).

    avp init    SERIES_DIR                     create a series from a template
    avp new     SERIES_DIR "brief" | @brief.md  start an episode
    avp plan    EPISODE_DIR [options]          what a run would generate / pay for (calls nothing)
    avp run     EPISODE_DIR [options]          run stages (stops at the review gate until approved)
    avp revise  EPISODE_DIR @notes.md          apply fact-check / review notes to the script
    avp approve EPISODE_DIR                    unlock production for the current script revision
    avp check   EPISODE_DIR                    validate script.json, regenerate script.md
    avp script  get|put EPISODE_DIR [file]     read / replace script.json (the hand-off point for other agents)
    avp adopt   EPISODE_DIR                    keep images / clips / voices made by hand or another agent
    avp outputs EPISODE_DIR                    finished files + QC verdict
    avp status  SERIES_DIR | EPISODE_DIR
    avp stages | schema | doctor [SERIES_DIR]
    avp mcp                                    serve all of this to agents over MCP (stdio)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import api
from .stages import ORDER

log = logging.getLogger("avp")


def _text_arg(value: str) -> str:
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    if value == "-":
        return sys.stdin.read()
    return value


def _run_options(args) -> dict:
    return dict(stages=args.only, from_stage=args.from_stage, to_stage=args.to_stage, force=args.force,
                scenes=args.scenes, outputs=args.outputs, skip_review=args.skip_review, draft=args.draft,
                provider=args.provider, workers=args.workers)


# ----- human-readable renderers (the --json flag prints the api result instead) --------

def _show_run(r: dict) -> None:
    if r["status"] in ("planned", "refused"):
        for s in r["stages"]:
            c = s.get("counts") or {}
            detail = s.get("note") or f"would generate {c.get('planned', 0)}, up to date {c.get('cached', 0)}"
            print(f"  {s['name']:<8} {'paid ' if s['paid'] else 'local'}  {detail}")
        if r.get("review_gate"):
            print(f"  ⏸  {r['review_gate']}")
        print(f"paid model calls: {r['paid_calls']}")
        if r["status"] == "refused":
            sys.exit(f"✖ {r['message']}")
        return
    if r["status"] == "paused":
        log.info("⏸  %s", r["message"])
    elif r["status"] == "failed":
        sys.exit(f"✖ {r['message']}")
    else:
        log.info("✔ done: %s", r["episode"])


def _show_status(s: dict) -> None:
    def line(e: dict) -> str:
        marks = [f"{k} {'✔' if e[k] else '·'}" for k in ("research", "outline", "script")]
        if e["script"]:
            marks += [f"r{e['revision']} {'approved' if e['approved'] else 'DRAFT'}",
                      f"art {e['art_done']}/{e['illustrations']}", f"motion {e['motion_done']}"]
        marks.append(f"videos {len(e['videos'])}")
        return f"{e['name']}: " + " · ".join(marks)

    if "episodes" in s:
        print(f"{s['name']['zh']} / {s['name']['en']} — outputs: {', '.join(s['outputs'])}")
        print(f"cast library: {', '.join(s['cast']) or '(empty)'}")
        for e in s["episodes"]:
            print("  " + line(e))
    else:
        print(line(s))


def _show_check(c: dict) -> None:
    print(f"{c['scenes']} scenes · {c['claims']} claims · ~{c['minutes_en']} min EN ({c['words_en']} words) · "
          f"~{c['minutes_zh']} min ZH ({c['chars_zh']} 字) · {'approved' if c['approved'] else 'draft'} r{c['revision']}")
    if c["problems"]:
        sys.exit("problems:\n  " + "\n  ".join(c["problems"]))
    print(f"ok — {c['script_md']} regenerated")


def _show_ok(r: dict, ok_text: str) -> None:
    if not r.get("ok", True):
        sys.exit("problems (fix them, or use --force):\n  " + "\n  ".join(r["problems"]))
    print(ok_text.format(**r))


def _show_outputs(o: dict) -> None:
    for f in o["files"]:
        print(f"{f['video']}  {f.get('duration', '?')}s  {f['bytes'] / 1e6:.1f} MB")
    print(f"QC: {o['qc'] or 'not run'}")


def _show_doctor(d: dict) -> None:
    for c in d["checks"]:
        print(f"{'✔' if c['ok'] else '✖'} {c['check']}  {c.get('detail', '')}" + ("" if c["ok"] else f"  → {c['fix']}"))
    if not d["ok"]:
        sys.exit(1)


# ----- commands ---------------------------------------------------------------------

def dispatch(args) -> tuple[object, callable]:
    """Call the api for a parsed command; return (result, human renderer)."""
    c = args.cmd
    if c == "init":
        return api.init_series(args.series_dir, args.template), lambda r: print(
            f"created {r['config']} — edit it, then: avp new {r['series']} \"episode brief\"")
    if c == "new":
        return api.new_episode(args.series_dir, _text_arg(args.brief), args.slug, args.number), lambda r: print(
            f"created {r['episode']}\nnext: avp run {r['episode']}")
    if c == "plan":
        return api.plan(args.episode_dir, **_run_options(args)), _show_run
    if c == "run":
        return api.run(args.episode_dir, max_paid_calls=args.max_paid, **_run_options(args)), _show_run
    if c == "revise":
        return api.revise(args.episode_dir, _text_arg(args.notes), args.provider), lambda r: print(
            f"revised to r{r['revision']} -> {r['script_md']} (review again, then `avp approve {args.episode_dir}`)")
    if c == "approve":
        return api.approve(args.episode_dir, args.force), lambda r: _show_ok(
            r, "approved revision {revision}. next: avp run " + args.episode_dir)
    if c == "check":
        return api.check(args.episode_dir), _show_check
    if c == "script":
        if args.action == "get":
            return api.get_script(args.episode_dir), lambda r: print(json.dumps(r, ensure_ascii=False, indent=2))
        data = json.loads(_text_arg("@" + args.file if args.file and args.file != "-" else "-"))
        return api.put_script(args.episode_dir, data, keep_approval=args.keep_approval), lambda r: _show_ok(
            r, "script.json saved (r{revision}, approved={approved}, changed={changed})")
    if c == "adopt":
        return api.adopt(args.episode_dir, args.only, args.scenes, args.provider), lambda r: [
            print(f"{n:<7} adopted {v.get('adopted', 0)}, already up to date {v.get('cached', 0)}, "
                  f"missing {v.get('planned', 0)}") for n, v in r["stages"].items()]
    if c == "outputs":
        return api.list_outputs(args.episode_dir), _show_outputs
    if c == "status":
        return api.status(args.path), _show_status
    if c == "stages":
        return api.stages(), lambda r: [print(f"{s['name']:<8} {'paid ' if s['paid'] else 'local'}  {s['description']}") for s in r]
    if c == "schema":
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "avp script.json", **api.script_schema()}
        return schema, lambda r: print(json.dumps(r, ensure_ascii=False, indent=2))
    if c == "doctor":
        return api.doctor(args.series_dir), _show_doctor
    raise ValueError(f"unknown command {c}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="avp", description="AI video pipeline for faceless explainer series")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--json", action="store_true", help="print the result as JSON (logs go to stderr)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create a series folder from a template")
    p.add_argument("series_dir")
    p.add_argument("--template", default="econ-history")

    p = sub.add_parser("new", help="start a new episode")
    p.add_argument("series_dir")
    p.add_argument("brief", help='episode brief text, "@file.md", or "-" for stdin')
    p.add_argument("--slug")
    p.add_argument("--number", type=int)

    def provider_opts(p):
        p.add_argument("--provider", choices=["anthropic", "gemini", "openai", "mock"],
                       help="use this provider for every job (mock = offline placeholders)")

    for name, help_text in (("run", "run pipeline stages"), ("plan", "show what a run would generate, without calling anything")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("episode_dir")
        p.add_argument("--only", help=f"comma-separated stages to run ({', '.join(ORDER)})")
        p.add_argument("--from", dest="from_stage", choices=ORDER)
        p.add_argument("--to", dest="to_stage", choices=ORDER)
        p.add_argument("--force", action="store_true", help="regenerate even if up to date")
        p.add_argument("--scenes", help="limit regeneration to these scene ids, e.g. s03,s07")
        p.add_argument("--outputs", help="limit rendering to these output ids")
        p.add_argument("--skip-review", action="store_true", help="produce a draft without approving the script")
        p.add_argument("--draft", action="store_true", help="render at 540p for a quick preview")
        p.add_argument("--workers", type=int, help="parallel API calls / renders")
        provider_opts(p)
        if name == "run":
            p.add_argument("--max-paid", type=int, help="refuse to start if the run needs more paid model calls")

    p = sub.add_parser("revise", help="apply review notes to the script")
    p.add_argument("episode_dir")
    p.add_argument("notes", help='notes text, "@notes.md", or "-" for stdin')
    provider_opts(p)

    p = sub.add_parser("approve", help="approve the current script for production")
    p.add_argument("episode_dir")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("check", help="validate script.json and regenerate script.md")
    p.add_argument("episode_dir")

    p = sub.add_parser("script", help="read or replace script.json")
    p.add_argument("action", choices=["get", "put"])
    p.add_argument("episode_dir")
    p.add_argument("file", nargs="?", help="put: JSON file to write (default: stdin)")
    p.add_argument("--keep-approval", action="store_true", help="put: keep the approval even if the script changed")

    p = sub.add_parser("adopt", help="accept images / clips / voice files placed by hand or another agent")
    p.add_argument("episode_dir")
    p.add_argument("--only", help="stages to adopt (default: cast,art,motion,voice)")
    p.add_argument("--scenes", help="limit to these scene ids")
    provider_opts(p)

    p = sub.add_parser("outputs", help="list finished files and the QC verdict")
    p.add_argument("episode_dir")

    p = sub.add_parser("status", help="show progress of a series or episode")
    p.add_argument("path")

    sub.add_parser("stages", help="list stages and which ones call paid models")
    sub.add_parser("schema", help="print the JSON Schema of script.json")
    p = sub.add_parser("doctor", help="check ffmpeg, Chromium, fonts and API keys")
    p.add_argument("series_dir", nargs="?")
    sub.add_parser("mcp", help="run the MCP server on stdio (for Claude Code, Cursor and other agents)")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s", stream=sys.stderr)
    for noisy in ("httpx", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    loaded = api.load_env()
    if loaded:
        log.debug("loaded from .env: %s", ", ".join(loaded))
    if args.cmd == "mcp":
        from .mcp_server import serve
        serve()
        return
    try:
        result, show = dispatch(args)
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        log.error("✖ %s", exc)
        sys.exit(1)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        failed = isinstance(result, dict) and (result.get("status") in ("failed", "refused") or result.get("ok") is False)
        if failed:
            sys.exit(1)
    else:
        show(result)


if __name__ == "__main__":
    main()
