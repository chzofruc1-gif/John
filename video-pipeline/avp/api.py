"""Stable programmatic interface — for other agents, scripts and the MCP server.

Every function takes plain arguments and returns plain JSON-serialisable data (dicts, lists, strings,
numbers), so the same calls work from Python, from `avp <command> --json`, and over MCP (`avp mcp`).

    from avp import api
    api.new_episode("channels/econ", "盐铁论：一场两千年前的经济辩论", slug="salt-iron")
    api.plan("channels/econ/episodes/02-salt-iron")             # what would be generated (and paid for)
    api.run("channels/econ/episodes/02-salt-iron", to_stage="script")
    script = api.get_script(ep)          # read / edit / write back the master script
    api.put_script(ep, script)
    api.approve(ep)
    api.run(ep, max_paid_calls=200)      # refuses to start if the plan needs more paid calls than that

Paid model calls only happen in the stages listed by `stages()` with "paid": True, and only for items whose
inputs changed. `plan()` counts them without calling anything.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .config import load_series_config
from .models import Episode
from .project import EpisodeDir, Series
from .providers import build_providers
from .scriptdoc import render_script_md
from .stages import ORDER, STAGES
from .stages.common import Context, ReviewRequired

log = logging.getLogger("avp")
TEMPLATES = Path(__file__).with_name("templates")
API_VERSION = 1

STAGE_INFO = {
    "research": (True, "brief -> research.md: web-grounded research dossier with sources (LLM)"),
    "outline": (True, "research -> outline.md: acts, beats, diagrams, comedy beats (LLM)"),
    "script": (True, "outline -> script.json + script.md: bilingual master script with sourced claims (LLM)"),
    "cast": (True, "character reference sheets, shared by all episodes of the series (image model)"),
    "art": (True, "one 16:9 illustration per illustration scene (image model)"),
    "motion": (True, "illustrations animated into ~5 s clips, if providers.video is set (video model)"),
    "voice": (True, "one WAV per line per language (TTS)"),
    "compose": (False, "every output video: camera moves, diagrams, subtitles, mix (local ffmpeg + Chromium)"),
    "publish": (False, "titles, descriptions, chapters, sources, hashtags per output (local)"),
    "qc": (False, "duration / audio / loudness checks + contact sheets (local)"),
}
PAID_STAGES = [name for name in ORDER if STAGE_INFO[name][0]]


# ----- discovery ------------------------------------------------------------------

def stages() -> list[dict]:
    """The pipeline stages in order: name, whether it calls paid models, what it produces."""
    return [{"name": n, "paid": STAGE_INFO[n][0], "description": STAGE_INFO[n][1]} for n in ORDER]


def templates() -> list[str]:
    return sorted(p.stem for p in TEMPLATES.glob("*.toml"))


def script_schema() -> dict:
    """JSON Schema of script.json (what put_script accepts and what the script stage produces)."""
    from .stages.writing import SCRIPT_SCHEMA
    return SCRIPT_SCHEMA


# ----- series and episodes ---------------------------------------------------------

def init_series(series_dir: str | Path, template: str = "econ-history") -> dict:
    root = Path(series_dir)
    if (root / "series.toml").exists():
        raise FileExistsError(f"{root}/series.toml already exists")
    src = TEMPLATES / f"{template}.toml"
    if not src.exists():
        raise ValueError(f"unknown template {template!r}; available: {', '.join(templates())}")
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, root / "series.toml")
    Series(root)  # validates
    return {"series": str(root), "config": str(root / "series.toml")}


def new_episode(series_dir: str | Path, brief: str, slug: str | None = None, number: int | None = None) -> dict:
    ep = Series(Path(series_dir)).create_episode(brief, slug=slug, number=number)
    return {"episode": str(ep.root), "number": ep.number}


def _episode_status(ep: EpisodeDir) -> dict:
    info: dict[str, Any] = {
        "episode": str(ep.root), "name": ep.root.name, "number": ep.number,
        "research": ep.research_path.exists(), "outline": ep.outline_path.exists(), "script": ep.script_path.exists(),
    }
    if ep.script_path.exists():
        script = ep.load_script()
        art = [s for s in script.scenes if s.kind == "illustration"]
        info.update(
            title=script.title, revision=script.revision, approved=script.approved,
            scenes=len(script.scenes), illustrations=len(art),
            art_done=sum(ep.image_path(s).exists() for s in art),
            motion_done=sum(ep.motion_path(s).exists() for s in art),
            problems=script.validate(),
        )
    info["videos"] = sorted(p.name for p in (ep.root / "out").glob("*.mp4")) if (ep.root / "out").exists() else []
    return info


def status(path: str | Path) -> dict:
    """Progress of a series (all episodes) or of one episode."""
    root = Path(path)
    if (root / "series.toml").exists():
        series = Series(root)
        cfg = series.config
        return {
            "series": str(root), "name": {"zh": cfg.series.name_zh, "en": cfg.series.name_en},
            "outputs": [o.id for o in cfg.outputs],
            "providers": {k: getattr(cfg.providers, k) for k in ("research", "llm", "image", "video", "tts", "tts_en")},
            "cast": sorted(series.cast_library()),
            "episodes": [_episode_status(EpisodeDir(p)) for p in series.episodes()],
        }
    return _episode_status(EpisodeDir(root))


# ----- running ----------------------------------------------------------------------

def make_context(episode: EpisodeDir, *, provider: str | None = None, draft: bool = False, workers: int | None = None,
                 force: bool = False, scenes=None, outputs=None, skip_review: bool = False,
                 dry_run: bool = False) -> Context:
    series = episode.series
    cfg = series.config
    if provider:
        if provider == "anthropic":  # Claude writes; it has no image or speech models
            cfg.providers.research = cfg.providers.llm = "anthropic"
        else:
            cfg.providers.research = cfg.providers.llm = cfg.providers.image = cfg.providers.tts = provider
            cfg.providers.tts_en = ""
            cfg.providers.video = "mock" if provider == "mock" else ""  # only mock and wan animate
    if draft:
        cfg.render.height = 540  # fast low-res preview; full-res renders are cached separately
    if workers:
        cfg.runtime.max_workers = workers
    as_set = lambda v: {x.strip() for x in v.split(",") if x.strip()} if isinstance(v, str) else set(v or ())  # noqa: E731
    return Context(episode=episode, series=series, config=cfg, providers=build_providers(cfg), force=force,
                   only_scenes=as_set(scenes), only_outputs=as_set(outputs), skip_review=skip_review, dry_run=dry_run)


def _select(stages_: list[str] | str | None, from_stage: str | None, to_stage: str | None) -> list[str]:
    if stages_:
        names = [s.strip() for s in stages_.split(",")] if isinstance(stages_, str) else list(stages_)
    else:
        start = ORDER.index(from_stage) if from_stage else 0
        end = ORDER.index(to_stage) + 1 if to_stage else len(ORDER)
        names = ORDER[start:end]
    unknown = [s for s in names if s not in STAGES]
    if unknown:
        raise ValueError(f"unknown stage(s): {', '.join(unknown)}; stages: {', '.join(ORDER)}")
    return names


def _paid_total(result: dict) -> int:
    return sum((s.get("counts") or {}).get("planned", 0) for s in result["stages"] if s.get("paid"))


def plan(episode_dir: str | Path, **options) -> dict:
    """What a run with these options would generate — no model is called, nothing is written.
    Returns per-stage counts ("planned" = paid calls it would make) and "paid_calls" in total."""
    return run(episode_dir, plan=True, **options)


def run(episode_dir: str | Path, *, stages: list[str] | str | None = None, from_stage: str | None = None,
        to_stage: str | None = None, force: bool = False, scenes=None, outputs=None, skip_review: bool = False,
        draft: bool = False, provider: str | None = None, workers: int | None = None, plan: bool = False,
        max_paid_calls: int | None = None) -> dict:
    """Run pipeline stages. Status is "done", "paused" (script not approved yet — the review gate), "failed",
    "planned" (plan=True) or "refused" (the plan needs more than max_paid_calls paid model calls)."""
    episode = EpisodeDir(Path(episode_dir))
    names = _select(stages, from_stage, to_stage)
    if max_paid_calls is not None and not plan:
        preview = run(episode.root, stages=names, force=force, scenes=scenes, outputs=outputs, skip_review=skip_review,
                      draft=draft, provider=provider, plan=True)
        if preview["paid_calls"] > max_paid_calls:
            preview.update(status="refused", message=f"this run needs {preview['paid_calls']} paid model calls, "
                           f"more than max_paid_calls={max_paid_calls}; raise the limit to proceed")
            return preview

    script_approved = episode.script_path.exists() and episode.load_script().approved
    # A plan looks past the review gate so it can count everything; it reports the gate separately.
    ctx = make_context(episode, provider=provider, draft=draft, workers=workers, force=force, scenes=scenes,
                       outputs=outputs, skip_review=skip_review or plan, dry_run=plan)
    result: dict[str, Any] = {"episode": str(episode.root), "status": "planned" if plan else "done", "stages": []}
    if plan and not script_approved and not skip_review and any(n not in ("research", "outline", "script") for n in names):
        result["review_gate"] = "script.json is not approved: production stages will pause until `approve`"
    for name in names:
        paid = STAGE_INFO[name][0]
        entry: dict[str, Any] = {"name": name, "paid": paid}
        result["stages"].append(entry)
        if plan and not paid:
            entry.update(status="local", note="runs locally, no paid calls")
            continue
        if plan and name not in ("research", "outline", "script") and not episode.script_path.exists():
            entry.update(status="blocked", note="needs script.json first (counts unknown until the script exists)")
            continue
        log.info("▶ %s%s", name, " (plan)" if plan else "")
        t0 = time.monotonic()
        try:
            STAGES[name](ctx)
        except ReviewRequired as exc:
            entry.update(status="paused", message=str(exc))
            result.update(status="paused", message=str(exc))
            break
        except Exception as exc:  # report, stop: later stages depend on this one
            entry.update(status="failed", error=str(exc))
            result.update(status="failed", message=f"{name}: {exc}")
            break
        finally:
            entry["seconds"] = round(time.monotonic() - t0, 1)
            if name in ctx.report:
                entry["counts"] = ctx.report[name]
        entry["status"] = "planned" if plan else "done"
        log.info("  (%s took %.1fs)", name, entry["seconds"])
    result["paid_calls" if plan else "generated"] = (
        _paid_total(result) if plan else sum((s.get("counts") or {}).get("made", 0) for s in result["stages"]))
    if not plan and result["status"] == "done":
        result["outputs"] = list_outputs(episode.root)["files"]
    return result


def adopt(episode_dir: str | Path, stages: list[str] | str | None = None, scenes=None,
          provider: str | None = None) -> dict:
    """Accept assets already on disk — drawn, animated or voiced by hand or by another agent — as up to date, so
    later runs keep them instead of regenerating. Missing files are left for a normal run. Calls no model, but
    uses the same provider settings as later runs (their names are part of each asset's fingerprint)."""
    episode = EpisodeDir(Path(episode_dir))
    names = _select(stages or ["cast", "art", "motion", "voice"], None, None)
    ctx = make_context(episode, scenes=scenes, skip_review=True, provider=provider)
    ctx.adopt = ctx.dry_run = True  # adopt what exists, only count what is missing
    for name in names:
        if name not in ("cast", "art", "motion", "voice"):
            raise ValueError(f"adopt works on cast, art, motion and voice, not {name}")
        STAGES[name](ctx)
    return {"episode": str(episode.root),
            "stages": {n: {k: v for k, v in ctx.report.get(n, {}).items() if k in ("adopted", "cached", "planned")}
                       for n in names}}


# ----- the script (the main hand-off point between agents) -------------------------

def get_script(episode_dir: str | Path) -> dict:
    return EpisodeDir(Path(episode_dir)).load_script().to_dict()


def put_script(episode_dir: str | Path, script: dict, keep_approval: bool = False) -> dict:
    """Write a whole script.json (e.g. after another model fact-checked or rewrote it). It is validated; a changed
    script loses its approval unless keep_approval=True. script.md is regenerated."""
    ep = EpisodeDir(Path(episode_dir))
    new = Episode.from_dict(script)
    problems = new.validate()
    if problems:
        return {"ok": False, "problems": problems}
    old = ep.load_script() if ep.script_path.exists() else None
    changed = old is None or {**old.to_dict(), "approved": None} != {**new.to_dict(), "approved": None}
    if old is not None and changed:
        new.revision = max(new.revision, old.revision + 1)
        revisions = ep.root / "revisions"
        revisions.mkdir(exist_ok=True)
        old.save(revisions / f"script.r{old.revision}.json")
    if changed and not keep_approval:
        new.approved = False
    new.save(ep.script_path)
    ep.script_md_path.write_text(render_script_md(new, ep.series.config), encoding="utf-8")
    return {"ok": True, "changed": changed, "revision": new.revision, "approved": new.approved}


def check(episode_dir: str | Path) -> dict:
    """Validate script.json and regenerate script.md (after hand edits or another model's rewrite)."""
    ep = EpisodeDir(Path(episode_dir))
    script = ep.load_script()
    ep.script_md_path.write_text(render_script_md(script, ep.series.config), encoding="utf-8")
    en = sum(len(l.text["en"].split()) for s in script.scenes for l in s.lines)
    zh = sum(len(l.text["zh"]) for s in script.scenes for l in s.lines)
    return {"scenes": len(script.scenes), "claims": len(script.claims), "words_en": en, "chars_zh": zh,
            "minutes_en": round(en / 150, 1), "minutes_zh": round(zh / 260, 1), "revision": script.revision,
            "approved": script.approved, "problems": script.validate(), "script_md": str(ep.script_md_path)}


def revise(episode_dir: str | Path, notes: str, provider: str | None = None) -> dict:
    """Apply review / fact-check notes with the configured LLM (old revision kept under revisions/)."""
    from .stages.writing import revise as revise_script
    ep = EpisodeDir(Path(episode_dir))
    script = revise_script(make_context(ep, provider=provider), notes)
    return {"revision": script.revision, "approved": script.approved, "script_md": str(ep.script_md_path)}


def approve(episode_dir: str | Path, force: bool = False) -> dict:
    ep = EpisodeDir(Path(episode_dir))
    script = ep.load_script()
    problems = script.validate()
    if problems and not force:
        return {"ok": False, "problems": problems}
    script.approved = True
    script.save(ep.script_path)
    ep.script_md_path.write_text(render_script_md(script, ep.series.config), encoding="utf-8")
    return {"ok": True, "revision": script.revision}


# ----- results ------------------------------------------------------------------------

def list_outputs(episode_dir: str | Path) -> dict:
    """Finished files with their companions (subtitles, publish copy, contact sheet) and the QC verdict."""
    ep = EpisodeDir(Path(episode_dir))
    out = ep.root / "out"
    files = []
    for mp4 in sorted(out.glob("*.mp4")) if out.exists() else []:
        stem = mp4.with_suffix("")
        item: dict[str, Any] = {"video": str(mp4), "bytes": mp4.stat().st_size}
        for key, suffix in (("srt", ".srt"), ("publish", ".publish.md"), ("sheet", ".sheet.jpg"), ("timeline", ".timeline.json")):
            p = Path(f"{stem}{suffix}")
            if p.exists():
                item[key] = str(p)
        if "timeline" in item:
            item["duration"] = json.loads(Path(item["timeline"]).read_text(encoding="utf-8"))["duration"]
        files.append(item)
    qc_md = out / "qc.md"
    verdict = None
    if qc_md.exists():
        text = qc_md.read_text(encoding="utf-8")
        verdict = "fail" if "❌" in text else "pass"
    return {"files": files, "covers": [str(p) for p in sorted(out.glob("cover_*.jpg"))] if out.exists() else [],
            "qc": verdict, "qc_report": str(qc_md) if qc_md.exists() else None}


# ----- environment ------------------------------------------------------------------

def load_env(path: str | Path = ".env") -> list[str]:
    """Read KEY=VALUE lines from a .env file into os.environ. Variables already set win; empty values and
    comments (including trailing " # ...") are ignored. Returns the names that were set (never the values)."""
    p = Path(path)
    if not p.is_file():
        return []
    loaded = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def doctor(series_dir: str | Path | None = None) -> dict:
    """Check the local machine: ffmpeg, Chromium, a CJK font, and which API keys the configured providers need."""
    from .diagrams import CHROMIUM_GLOBS
    from .overlays import FONT_CANDIDATES
    import glob

    checks: list[dict] = []
    for tool in ("ffmpeg", "ffprobe"):
        checks.append({"check": tool, "ok": bool(shutil.which(tool)), "fix": "install ffmpeg (https://ffmpeg.org)"})
    chrome = os.environ.get("AVP_CHROMIUM") or next((p for g in CHROMIUM_GLOBS for p in glob.glob(g)), "")
    checks.append({"check": "chromium", "ok": bool(chrome) or _playwright_has_browser(), "detail": chrome,
                   "fix": "playwright install chromium (or set AVP_CHROMIUM)"})
    font = next((f for f in FONT_CANDIDATES if Path(f).exists()), "")
    checks.append({"check": "cjk font", "ok": bool(font), "detail": font,
                   "fix": "install Noto Sans CJK / Source Han Sans, or set render.font"})
    if series_dir:
        cfg = load_series_config(Path(series_dir) / "series.toml")
        for job in ("research", "llm", "image", "video", "tts", "tts_en"):
            name = getattr(cfg.providers, job) or ("" if job != "research" else cfg.providers.llm)
            env, proxy = _key_for(cfg, job, name)
            if env:
                has_key = bool(os.environ.get(env))
                detail = f"${env} " + ("set" if has_key else "not set")
                if proxy and not has_key:
                    detail += " (auth_via_proxy is on: fine only where a proxy adds the key, e.g. the cloud session)"
                checks.append({"check": f"{job}: {name}", "ok": has_key, "detail": detail,
                               "fix": f"put {env}=... in .env (see .env.example)"})
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def _playwright_has_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


def _key_for(cfg, job: str, name: str) -> tuple[str, bool]:
    if not name or name == "mock" or ":" in name:
        return "", False
    if name == "anthropic":
        return "ANTHROPIC_API_KEY", False
    if name == "gemini":
        return cfg.gemini.api_key_env, False
    if name in ("cosyvoice", "qwen", "wan"):
        section = {"cosyvoice": cfg.cosyvoice, "qwen": cfg.qwen_image, "wan": cfg.wan_video}[name]
        return section.api_key_env, section.auth_via_proxy
    if name == "openai":
        section = {"research": cfg.openai.llm, "llm": cfg.openai.llm, "image": cfg.openai.image}.get(job, cfg.openai.tts)
        return section.api_key_env, False
    return "", False
