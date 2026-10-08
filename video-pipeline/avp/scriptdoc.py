"""Render script.json as a readable review document with footnoted claims (script.md)."""

from __future__ import annotations

from .config import SeriesConfig
from .models import NARRATOR, Episode

CONFIDENCE_LABEL = {
    "established": "✅ established / 学界共识",
    "debated": "⚠️ debated / 存在争议",
    "traditional": "📜 traditional / 传统说法，证据较弱",
}

FACT_CHECK_PROMPT = """You are a rigorous historian of Chinese economic history and a fact-checker.
Check the script below. For EVERY numbered claim [cN] and every factual statement in the dialogue:
1. Is it accurate? Is the date / number / attribution right?
2. Is the cited source correct and does it actually say this? Is the quoted classical text exact?
3. Is the confidence label (established / debated / traditional) appropriate?
4. Is anything misleading by omission, anachronistic, or an over-simplification that a specialist would object to?
5. Are the modern analogies fair?
Answer as a list of notes: [claim id or scene id] — problem — suggested correction — source.
Say explicitly when a claim checks out. Do not rewrite the jokes; only flag them if they distort the history."""


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _speaker(ep: Episode, speaker: str, lang: str) -> str:
    if speaker == NARRATOR:
        return "旁白" if lang == "zh" else "Narrator"
    return ep.speaker_name(speaker, lang) or speaker


def render_script_md(ep: Episode, cfg: SeriesConfig) -> str:
    out: list[str] = []
    w = out.append
    w(f"# EP{ep.number:02d} {ep.title['zh']} / {ep.title['en']}")
    w("")
    w(f"> {ep.logline['zh']}  \n> {ep.logline['en']}")
    w("")
    w(f"Revision {ep.revision} · {'APPROVED' if ep.approved else 'DRAFT — needs review'} · "
      f"{len(ep.scenes)} scenes · {len(ep.claims)} claims · {cfg.series.name_zh} / {cfg.series.name_en}")
    w("")
    w("<details><summary>Fact-check prompt (paste this plus the whole document into another model)</summary>")
    w("")
    w("```text")
    w(FACT_CHECK_PROMPT)
    w("```")
    w("</details>")
    w("")
    w("## Cast / 角色")
    w("")
    w("| id | 名字 | Name | Role | Look |")
    w("|---|---|---|---|---|")
    for c in ep.cast:
        w(f"| `{c.id}` | {c.name['zh']} | {c.name['en']} | {c.role['en']} | {c.look} |")
    w("")
    w("## Script / 脚本")
    current_part = None
    for s in ep.scenes:
        if s.part != current_part:
            current_part = s.part
            w("")
            w(f"### Part {s.part} / 第{s.part}集")
        w("")
        header = f"**{s.id}** · {s.kind}"
        if s.chapter["en"]:
            header += f" · 📑 {s.chapter['zh']} / {s.chapter['en']}"
        w(header)
        w("")
        if s.kind == "diagram" and s.diagram:
            d = s.diagram
            w(f"- 🖼 diagram `{d.template}`: {d.title['zh']} / {d.title['en']}")
            if d.original:
                w(f"  - 原文: {d.original} — {d.source}")
            for item in d.items:
                w(f"  - {item.primary['zh']} — {item.secondary['zh']} / {item.primary['en']} — {item.secondary['en']}")
        else:
            cast = f" (cast: {', '.join(s.characters)})" if s.characters else ""
            w(f"- 🎨 {s.camera}{cast}: {s.visual_prompt}")
        if s.on_screen["en"] or s.on_screen["zh"]:
            w(f"- 🏷 {s.on_screen['zh']} / {s.on_screen['en']}")
        refs = " ".join(f"[{c}]" for c in s.claims)
        w("")
        w("| | 中文 | English |")
        w("|---|---|---|")
        for line in s.lines:
            who = f"{_speaker(ep, line.speaker, 'zh')} / {_speaker(ep, line.speaker, 'en')}"
            if line.delivery:
                who += f" _({line.delivery})_"
            w(f"| {who} | {_cell(line.text['zh'])} | {_cell(line.text['en'])} |")
        if refs:
            w("")
            w(f"Claims: {refs}")
    w("")
    w("## Claims & sources / 史实与出处")
    w("")
    for c in ep.claims:
        w(f"**[{c.id}]** {CONFIDENCE_LABEL.get(c.confidence, c.confidence)}")
        w("")
        w(f"- 中: {c.statement['zh']}")
        w(f"- EN: {c.statement['en']}")
        w(f"- Source: {c.source}")
        if c.quote:
            w(f"- 原文: {c.quote}")
        if c.note["zh"] or c.note["en"]:
            w(f"- Note: {c.note['zh']} / {c.note['en']}")
        w("")
    problems = ep.validate()
    if problems:
        w("## ⚠️ Consistency problems")
        w("")
        w("\n".join(f"- {p}" for p in problems))
        w("")
    return "\n".join(out).rstrip() + "\n"
