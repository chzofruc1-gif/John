"""Stages 8-9 — publish metadata per platform, and automated quality checks on the rendered files."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..media import probe_duration, run_ffmpeg
from .common import Context, log
from .compose import plan_outputs


def _ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def youtube_chapters(chapters: list[dict], intro: str) -> list[str]:
    """YouTube needs ≥3 chapters, the first at 0:00, each ≥10 s long."""
    kept: list[dict] = []
    for ch in chapters:
        if kept and ch["start"] - kept[-1]["start"] < 10:
            continue
        kept.append(ch)
    if not kept or kept[0]["start"] > 0.5:
        kept.insert(0, {"start": 0.0, "title": intro})
    kept[0]["start"] = 0.0
    return [f"{_ts(c['start'])} {c['title']}" for c in kept] if len(kept) >= 3 else []


def publish(ctx: Context) -> None:
    ep = ctx.episode.load_script()
    out_dir = ctx.episode.out_dir
    cfg = ctx.config
    specs = [o for o in cfg.outputs if not ctx.only_outputs or o.id in ctx.only_outputs]
    sources = sorted({c.source for c in ep.claims if c.source})
    for of in plan_outputs(ep, specs):
        lang = of.spec.language
        tl_path = out_dir / f"{of.name}.timeline.json"
        if not tl_path.exists():
            log.warning("  [publish] %s not rendered yet — skipping", of.name)
            continue
        tl = json.loads(tl_path.read_text(encoding="utf-8"))
        series = cfg.series.name_zh if lang == "zh" else cfg.series.name_en
        tags = ep.tags.get(lang, [])
        lines: list[str] = []
        if of.spec.platform == "youtube":
            title = f"{ep.title[lang]} | {series} EP{ep.number:02d}"
            chapters = youtube_chapters(tl["chapters"], "Intro" if lang == "en" else "开场")
            lines += [f"# {title}", "", "## Description", "", ep.logline[lang], ""]
            if chapters:
                lines += ["Chapters" if lang == "en" else "章节", *chapters, ""]
            lines += ["Sources & further reading" if lang == "en" else "参考资料", *[f"- {s}" for s in sources], ""]
            lines += [("Made with AI-assisted production: researched and fact-checked against the sources above; "
                       "narration voices are synthetic.") if lang == "en" else
                      "本视频使用 AI 辅助制作：内容依据上列资料查证，配音为合成语音。", ""]
            lines += ["## Tags", "", ", ".join(tags)]
        else:
            part = f"（{of.part}/{len(ep.parts())}）" if of.part else ""
            title = f"EP{ep.number:02d}｜{ep.title[lang]}{part}"
            hashtags = " ".join("#" + re.sub(r"\s+", "", t) for t in tags[:8])
            lines += [f"# {title}", "", "## 文案" if lang == "zh" else "## Caption", "",
                      ep.logline[lang], "", hashtags, "",
                      "## 参考资料" if lang == "zh" else "## Sources", "", *[f"- {s}" for s in sources]]
        path = out_dir / f"{of.name}.publish.md"
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        log.info("  [publish] -> %s", path.relative_to(ctx.episode.root))


# ----- QC ---------------------------------------------------------------------

def _streams(path: Path) -> list[str]:
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True)
    return [s.strip() for s in proc.stdout.split() if s.strip()]


def _loudness(path: Path) -> tuple[float | None, float | None]:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
                           "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    text = proc.stderr[proc.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", text)
    p = re.search(r"Peak:\s+(-?[\d.]+) dBFS", text)
    return (float(i.group(1)) if i else None, float(p.group(1)) if p else None)


def _contact_sheet(path: Path, duration: float, out: Path, tiles: int = 12) -> None:
    cols = 4 if tiles >= 8 else tiles
    rows = (tiles + cols - 1) // cols
    every = max(duration / tiles, 0.5)
    run_ffmpeg(["-i", str(path), "-vf", f"fps=1/{every:.3f},scale=480:-2,tile={cols}x{rows}:padding=6:color=white",
                "-frames:v", "1", "-q:v", "3", str(out)])


def qc(ctx: Context) -> None:
    ep = ctx.episode.load_script()
    out_dir = ctx.episode.out_dir
    report = ["# QC report", "", "| file | duration | expected | audio | loudness (LUFS) | peak (dBFS) | size | verdict |",
              "|---|---|---|---|---|---|---|---|"]
    failures = 0
    for of in plan_outputs(ep, ctx.config.outputs):
        mp4 = out_dir / f"{of.name}.mp4"
        tl_path = out_dir / f"{of.name}.timeline.json"
        if not mp4.exists() or not tl_path.exists():
            report.append(f"| {of.name} | — | — | — | — | — | — | ❌ not rendered |")
            failures += 1
            continue
        expected = json.loads(tl_path.read_text(encoding="utf-8"))["duration"]
        duration = probe_duration(mp4)
        streams = _streams(mp4)
        lufs, peak = _loudness(mp4)
        problems = []
        if abs(duration - expected) > 0.5:
            problems.append("duration off")
        if "audio" not in streams:
            problems.append("no audio")
        if lufs is not None and not -19 <= lufs <= -13:
            problems.append("loudness")
        if peak is not None and peak > -0.5:
            problems.append("clipping")
        if lufs is not None and lufs < -60:
            problems.append("silent")
        _contact_sheet(mp4, duration, out_dir / f"{of.name}.sheet.jpg")
        size = mp4.stat().st_size / 1e6
        verdict = "✅ ok" if not problems else "❌ " + ", ".join(problems)
        failures += bool(problems)
        report.append(f"| {of.name} | {duration:.1f}s | {expected:.1f}s | {'yes' if 'audio' in streams else 'NO'} | "
                      f"{lufs if lufs is not None else '?'} | {peak if peak is not None else '?'} | {size:.1f} MB | {verdict} |")
    report += ["", "Contact sheets: `out/*.sheet.jpg` — check framing, subtitle overlap and pacing by eye."]
    (out_dir / "qc.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    log.info("  [qc] %s -> out/qc.md", "all checks passed" if not failures else f"{failures} file(s) need attention")
    if failures:
        raise RuntimeError(f"QC found problems in {failures} file(s) — see {out_dir / 'qc.md'}")
