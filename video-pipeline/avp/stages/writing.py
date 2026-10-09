"""Stages 1-3 — research dossier, beat-sheet outline, bilingual master script (+ revise)."""

from __future__ import annotations

import json

from ..config import SeriesConfig
from ..models import CAMERA_MOVES, CONFIDENCE, DIAGRAM_TEMPLATES, Episode
from ..scriptdoc import render_script_md
from .common import Context, log

# Narration pace used to size the script: words/characters per minute of finished video.
EN_WPM = 150
ZH_CPM = 260


def series_brief(ctx: Context) -> str:
    cfg = ctx.config
    s = cfg.series
    rules = "\n".join(f"- {r}" for r in s.rules) or "- (none)"
    cast = "\n".join(f"- id `{c.id}`: {c.name_zh} / {c.name_en} — look: {c.look}"
                     for c in ctx.series.cast_library().values()) or "- (none yet)"
    return f"""SERIES: {s.name_zh} / {s.name_en}
Premise: {s.premise}
Chinese audience: {s.audience_zh}
English audience: {s.audience_en}
Tone: {s.tone}
House rules:
{rules}
Recurring characters already designed (reuse these ids when they appear):
{cast}"""


# ----- research ---------------------------------------------------------------

def research(ctx: Context) -> None:
    ep = ctx.episode
    if ep.research_path.exists() and not ctx.force:
        log.info("  [research] research.md exists — keeping it (use --force to redo)")
        return
    prompt = f"""{series_brief(ctx)}

You are the show's lead researcher. Build a research dossier for the episode brief below. Use web search.
Be rigorous: this channel's credibility depends on getting history right.

Cover, with a source for every fact (primary text with chapter, or a named scholar / book / paper):
1. Historical context: who, when, where, what problem they faced. Exact dates (BC/AD) and places.
2. The core economic idea or policy and HOW it worked mechanically (money, goods, prices, incentives, who paid).
3. Key primary-source passages: quote the original classical Chinese, cite the chapter, give a faithful
   modern Chinese paraphrase and an English translation.
4. Authorship and dating problems (e.g. texts attributed to a figure but written later), and where scholars
   disagree. Label every claim as ESTABLISHED, DEBATED or TRADITIONAL (story told by tradition, weakly attested).
5. Numbers that can be visualised (revenues, prices, population, dates) with their sources and reliability.
6. Consequences and legacy: what happened next, and long-run echoes in later dynasties.
7. Comparisons a Western viewer would recognise (similar ideas or policies elsewhere) — clearly marked as analogies.
8. Common myths or misconceptions about this topic, and the correction.
9. Natural comedic material: ironies, absurd episodes, characters' personalities as recorded in sources.

Write in English, as structured markdown. Do not invent quotes or numbers; if unsure, say so.

EPISODE BRIEF:
{ep.brief}"""
    log.info("  [research] researching with %s/%s", ctx.providers.research.name, ctx.providers.research.model)
    result = ctx.providers.research.research(prompt)
    sources = "\n".join(f"- [{s.title}]({s.url})" for s in result.sources)
    ep.research_path.write_text(
        result.markdown.strip() + ("\n\n## Sources consulted (search grounding)\n\n" + sources if sources else "") + "\n",
        encoding="utf-8",
    )
    log.info("  [research] %d sources -> %s", len(result.sources), ep.research_path.name)


# ----- outline ----------------------------------------------------------------

def outline(ctx: Context) -> None:
    ep, cfg = ctx.episode, ctx.config
    if ep.outline_path.exists() and not ctx.force:
        log.info("  [outline] outline.md exists — keeping it (use --force to redo)")
        return
    d = cfg.episode
    prompt = f"""{series_brief(ctx)}

You are the head writer. Using the research dossier, write a beat-sheet outline for a {d.minutes:g}-minute episode.

Structure:
- COLD OPEN (≤30s): a surprising, concrete hook — a question, a paradox, or a vivid moment. No "In this video...".
- The spine is ECONOMICS. Identify the concrete measures/policies/ideas of the topic. For each one plan:
  what was done (primary source) → how it works mechanically → the modern economic concept that explains it
  (name, originator, year) → its historical significance in world economic thought (with careful "earliest" claims).
- {d.zh_parts} ACTS of roughly equal length. Each act must end on a mini-cliffhanger, because the Chinese
  version is cut into {d.zh_parts} separate short videos at these act breaks.
- Inside the acts: story beats (people, conflict, decisions) alternate with explainer beats (the economic
  mechanism, made concrete). Every explainer beat names the diagram that will show it
  (one of: {", ".join(DIAGRAM_TEMPLATES)}).
- Mark 3-5 comedic beats: narrator asides, modern analogies, or a character speaking in a short skit.
- CLOSE: the big idea in one sentence, why it still matters today, and a tease for the next episode.

For each beat give: act, beat title, what happens, which facts/claims from the dossier it uses (flag
DEBATED/TRADITIONAL ones), visual idea, comedy idea (if any). Write in English markdown.

EPISODE BRIEF:
{ep.brief}

RESEARCH DOSSIER:
{ep.read(ep.research_path) or "(no research yet — rely on well-established facts only)"}"""
    log.info("  [outline] writing beat sheet")
    ep.outline_path.write_text(ctx.providers.llm.text("outline", prompt).strip() + "\n", encoding="utf-8")
    log.info("  [outline] -> %s", ep.outline_path.name)


# ----- script -----------------------------------------------------------------

BI = {"type": "object", "properties": {"zh": {"type": "string"}, "en": {"type": "string"}}, "required": ["zh", "en"]}

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": BI,
        "logline": BI,
        "cast": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "name": BI, "gender": {"type": "string", "enum": ["male", "female"]},
            "look": {"type": "string"}, "role": BI,
        }, "required": ["id", "name", "gender", "look", "role"]}},
        "scenes": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"},
            "part": {"type": "integer"},
            "kind": {"type": "string", "enum": ["illustration", "diagram"]},
            "chapter": BI,
            "lines": {"type": "array", "items": {"type": "object", "properties": {
                "speaker": {"type": "string"}, "delivery": {"type": "string"}, "text": BI,
            }, "required": ["speaker", "delivery", "text"]}},
            "visual_prompt": {"type": "string"},
            "motion": {"type": "string"},
            "characters": {"type": "array", "items": {"type": "string"}},
            "camera": {"type": "string", "enum": list(CAMERA_MOVES)},
            "diagram": {"type": "object", "properties": {
                "template": {"type": "string", "enum": list(DIAGRAM_TEMPLATES)},
                "title": BI,
                "items": {"type": "array", "items": {"type": "object", "properties": {
                    "primary": BI, "secondary": BI}, "required": ["primary", "secondary"]}},
                "original": {"type": "string"},
                "source": {"type": "string"},
            }, "required": ["template", "title", "items", "original", "source"]},
            "on_screen": BI,
            "claims": {"type": "array", "items": {"type": "string"}},
        }, "required": ["id", "part", "kind", "chapter", "lines", "visual_prompt", "motion", "characters", "camera",
                        "on_screen", "claims"]}},
        "claims": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "statement": BI, "source": {"type": "string"}, "quote": {"type": "string"},
            "confidence": {"type": "string", "enum": list(CONFIDENCE)}, "note": BI,
        }, "required": ["id", "statement", "source", "quote", "confidence", "note"]}},
        "tags": {"type": "object", "properties": {
            "zh": {"type": "array", "items": {"type": "string"}},
            "en": {"type": "array", "items": {"type": "string"}},
        }, "required": ["zh", "en"]},
    },
    "required": ["title", "logline", "cast", "scenes", "claims", "tags"],
}

SCRIPT_RULES = """FORMAT RULES (the output is parsed by a production pipeline):
- scenes: about {scenes} scenes of ~{secs:g}s each. ids "s01", "s02", ... in order. "part" = act number 1..{parts}.
- Every scene has 1-4 lines. speaker is "narrator" or a cast id. Character lines are short (≤20 words / ≤30 字),
  in character, and used for comedy or drama — the narrator carries the explanation.
- Each line has BOTH languages. They are two native scripts, not translations of each other:
  * zh: 口语化、有梗、节奏快，像一位懂行又爱吐槽的朋友在讲；可以用网络流行语但别过时；
    古文原句可以引用，但紧接着用大白话解释。
  * en: conversational, witty, British-documentary-meets-YouTube energy. Assume the viewer knows NOTHING about
    China: give context (dates as "around 650 BC", what a "state" was), explain names once, use Western analogies.
- "delivery": a short acting note for the voice actor in English (e.g. "deadpan", "conspiratorial whisper").
- Total narration ≈ {en_words} English words and ≈ {zh_chars} Chinese characters.
- kind "illustration": visual_prompt is ONE frame in English: subject, action, setting, composition, lighting.
  Put cast ids in "characters" and describe them by name in the prompt (their look is added automatically).
  Never ask for text, letters, captions or calligraphy inside illustrations. Omit "diagram".
  motion is ONE short English sentence: what moves when this frame is animated for ~5 seconds (gestures,
  expressions, a prop, falling coins) — small, readable, comic actions; "" for diagrams.
- kind "diagram": explains a mechanism. Use the templates:
  * chapter — act title card. title = act title; items[0].primary = kicker like "Act 1" / "第一幕".
  * quote — a classical passage. original = exact classical Chinese text; source = book·chapter;
    title = faithful translation (zh: 白话译文, en: English translation). items may be empty.
  * flow — a chain of 3-5 steps/nodes. items = nodes (primary = node label ≤4 words / ≤8 字,
    secondary = what flows to the next node, e.g. "taxes", "salt").
  * compare — exactly 2 items: two sides of a debate/system. primary = side name, secondary = 2-4 points
    separated by "；" (zh) or "; " (en).
  * timeline — 3-6 items in order. primary = date (e.g. "685 BC"/"前685年"), secondary = event (short).
  * stat — 1 item. primary = the number (e.g. "~50%"), secondary = what it measures. Only sourced numbers.
  Keep diagram text SHORT. visual_prompt "" and characters [] for diagrams.
- Alternate illustration and diagram scenes so no more than 3 of the same kind run in a row. Start each act
  (part) with a "chapter" diagram scene, except the cold open which is part 1 and comes before it.
- on_screen: optional punchy headline (≤6 words / ≤10 字) or empty strings.
- chapter: non-empty only on the first scene of each YouTube chapter (cold open, each act, close), e.g.
  {{"zh": "盐的生意", "en": "The Salt Business"}}.
- claims: every factual statement in the narration (dates, numbers, quotes, attributions, causal claims) gets a
  claim with a precise source (e.g. "《管子·海王》", "《史记·平准书》", "Loewe, Crisis and Conflict in Han China
  (1974), ch. 3"). confidence: established | debated | traditional. note explains caveats in both languages.
  Reference claim ids from the scenes that state them. Never invent sources; if unsure, mark "debated" and say why.
- cast: every non-narrator speaker and every character drawn in illustrations. Reuse ids of recurring
  characters listed above. "look" = consistent English visual description in the series art style
  (age, build, face, hair/beard, hat, clothing colours of the correct era).
- tags: 8-15 search tags per language."""


def _script_rules(cfg: SeriesConfig) -> str:
    d = cfg.episode
    return SCRIPT_RULES.format(
        scenes=max(4, round(d.minutes * 60 / d.seconds_per_scene)), secs=d.seconds_per_scene, parts=d.zh_parts,
        en_words=round(d.minutes * EN_WPM), zh_chars=round(d.minutes * ZH_CPM),
    )


def _finish_script(ctx: Context, raw: dict, revision: int) -> Episode:
    ep_dir = ctx.episode
    raw["number"] = ep_dir.number
    raw["revision"] = revision
    raw["approved"] = False
    for scene in raw.get("scenes", []):
        if scene.get("kind") != "diagram":
            scene["diagram"] = None
    episode = Episode.from_dict(raw)
    problems = episode.validate()
    episode.save(ep_dir.script_path)
    ep_dir.script_md_path.write_text(render_script_md(episode, ctx.config), encoding="utf-8")
    if problems:
        log.warning("  [script] %d consistency problem(s) — fix in script.json or `avp revise`:\n    %s",
                    len(problems), "\n    ".join(problems))
    log.info("  [script] '%s' / '%s' — %d scenes, %d claims, revision %d -> script.json, script.md",
             episode.title["zh"], episode.title["en"], len(episode.scenes), len(episode.claims), revision)
    return episode


def script(ctx: Context) -> None:
    ep, cfg = ctx.episode, ctx.config
    if ep.script_path.exists() and not ctx.force:
        log.info("  [script] script.json exists — keeping it (use --force to rewrite, or `avp revise`)")
        return
    prompt = f"""{series_brief(ctx)}

You are the head writer. Turn the outline into the final bilingual shooting script.
The story serves the economics: characters and comedy carry the viewer, but each act must leave them understanding
specific measures, how they work, their modern economic meaning, and their place in world history.
Make it genuinely entertaining AND genuinely illuminating: every comedic beat must also teach something,
and every mechanism must be explained so concretely that a 15-year-old could re-explain it.

{_script_rules(cfg)}

EPISODE BRIEF:
{ep.brief}

OUTLINE:
{ep.read(ep.outline_path)}

RESEARCH DOSSIER:
{ep.read(ep.research_path)}"""
    log.info("  [script] writing bilingual script with %s/%s", ctx.providers.llm.name, ctx.providers.llm.model)
    d = cfg.episode
    raw = ctx.providers.llm.json("script", prompt, SCRIPT_SCHEMA, hints={
        "brief": ep.brief, "scenes": max(4, round(d.minutes * 60 / d.seconds_per_scene)), "parts": d.zh_parts,
        "number": ep.number,
    })
    _finish_script(ctx, raw, revision=1)


def revise(ctx: Context, notes: str) -> Episode:
    """Apply reviewer / fact-check notes to the current script, keeping everything else intact."""
    current = ctx.episode.load_script()
    prompt = f"""{series_brief(ctx)}

You are the head writer. Below is the current script (JSON) and review notes from fact-checkers and the
producer. Revise the script to address EVERY note. Fix factual errors and their claims/sources; keep scene ids
stable where scenes survive; keep everything that the notes do not ask to change. Return the full script.

{_script_rules(ctx.config)}

REVIEW NOTES:
{notes}

CURRENT SCRIPT:
{json.dumps(current.to_dict(), ensure_ascii=False)}"""
    log.info("  [revise] applying review notes (revision %d -> %d)", current.revision, current.revision + 1)
    raw = ctx.providers.llm.json("revise", prompt, SCRIPT_SCHEMA, hints={"current": current.to_dict()})
    history = ctx.episode.root / "revisions"
    history.mkdir(exist_ok=True)
    current.save(history / f"script.r{current.revision}.json")
    (history / f"notes.r{current.revision}.md").write_text(notes.strip() + "\n", encoding="utf-8")
    return _finish_script(ctx, raw, revision=current.revision + 1)

