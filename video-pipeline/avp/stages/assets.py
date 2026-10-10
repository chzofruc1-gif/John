"""Stages 4-6 — character sheets, illustrations, voice lines. All incremental via fingerprints."""

from __future__ import annotations

import hashlib

from ..config import CharacterSpec
from ..models import NARRATOR, Episode, Line, Scene
from ..project import fingerprint
from .common import Context, log, parallel

MASTER_ASPECT = "16:9"  # art is drawn once in 16:9; vertical outputs reframe it
NO_TEXT = "Absolutely no text, letters, numbers, captions, signatures, calligraphy, watermarks or logos in the image."


def _cast_specs(ctx: Context, ep: Episode) -> dict[str, CharacterSpec]:
    """Episode cast resolved against the series library (library looks win, for consistency)."""
    specs = {}
    for member in ep.cast:
        specs[member.id] = ctx.series.register_cast(member)
    return specs


# ----- character reference sheets ---------------------------------------------

def sheet_prompt(spec: CharacterSpec, art_style: str) -> str:
    return (f"Character model sheet for an animated history series. Character: {spec.name_en}. {spec.look}. "
            "Show the same character three times side by side: full body front view, full body three-quarter view, "
            "and a close-up of the face with a mischievous expression. Plain warm off-white background, even lighting, "
            f"clean readable silhouette.\nArt direction: {art_style}\n{NO_TEXT}")


def cast(ctx: Context) -> None:
    ctx.require_approval("cast")
    ep = ctx.episode.load_script()
    provider = ctx.providers.image
    specs = _cast_specs(ctx, ep)
    drawn = {cid for s in ep.scenes if s.kind == "illustration" for cid in s.characters}
    todo = [specs[cid] for cid in sorted(drawn) if cid in specs]
    log.info("  [cast] %d character sheet(s) with %s/%s", len(todo), provider.name, provider.model)

    def work(spec: CharacterSpec) -> str:
        path = ctx.series.cast_sheet(spec.id)
        prompt = sheet_prompt(spec, ctx.config.series.art_style)
        key = f"cast:{spec.id}"
        # Sheets live at series level and are kept once drawn, so a character looks the same in every episode
        # even if the look text is tweaked later (use --force --scenes <id> to redraw one). Placeholders drawn
        # by the offline mock, or sheets of unknown origin, are always replaced by a real provider.
        record = ctx.series.sheet_record(spec.id)
        placeholder = not record or (record.get("provider") == "mock" and provider.name != "mock")
        if path.exists() and not placeholder and not ctx.forced(spec.id):
            if record.get("look") != spec.look:
                log.warning("  [cast] %s: look text changed since the sheet was drawn; keeping the sheet "
                            "(redraw with --force --scenes %s)", spec.id, spec.id)
            return "cached"
        fp = fingerprint(provider.name, provider.model, prompt)
        if ctx.adopt and path.exists():  # a sheet drawn elsewhere: record it so it is kept from now on
            ctx.series.record_sheet(spec.id, "external", "", spec.look)
            ctx.episode.mark(key, fp)
            return "adopted"
        if ctx.dry_run:
            ctx.planned.add(key)
            return "planned"
        tmp = path.with_suffix(".tmp.png")
        provider.generate(prompt, MASTER_ASPECT, tmp, [])
        tmp.replace(path)
        ctx.series.record_sheet(spec.id, provider.name, provider.model, spec.look)
        ctx.episode.mark(key, fp)
        return "made"

    parallel(ctx, todo, "cast", work, lambda s: s.id)


# ----- illustrations -----------------------------------------------------------

def illustration_prompt(scene: Scene, specs: dict[str, CharacterSpec], art_style: str) -> str:
    people = [specs[c] for c in scene.characters if c in specs]
    cast_text = ""
    if people:
        cast_text = ("\n\nCharacters in this frame — match the attached reference sheets exactly (same face, "
                     "proportions, clothing, colours):\n" + "\n".join(f"- {p.name_en}: {p.look}" for p in people))
    return (f"{scene.visual_prompt}{cast_text}\n\nArt direction: {art_style}\n"
            "Composition: keep the main subject large and inside the central 60% of the frame, "
            "leave the bottom 20% calm (subtitles go there).\n" + NO_TEXT)


def art(ctx: Context) -> None:
    ctx.require_approval("art")
    ep = ctx.episode.load_script()
    provider = ctx.providers.image
    specs = _cast_specs(ctx, ep)
    scenes = [s for s in ep.scenes if s.kind == "illustration" and (not ctx.only_scenes or s.id in ctx.only_scenes)]
    log.info("  [art] %d illustration(s) with %s/%s", len(scenes), provider.name, provider.model)

    def work(scene: Scene) -> str:
        path = ctx.episode.image_path(scene)
        prompt = illustration_prompt(scene, specs, ctx.config.series.art_style)
        refs = [ctx.series.cast_sheet(c) for c in scene.characters if ctx.series.cast_sheet(c).exists()]
        fp = fingerprint(provider.name, provider.model, prompt, [ctx.episode.recorded(f"cast:{c}") for c in scene.characters])
        key = f"image:{scene.id}"
        upstream = ctx.dry_run and any(f"cast:{c}" in ctx.planned for c in scene.characters)
        if not upstream and not ctx.forced(scene.id) and ctx.episode.is_fresh(key, fp, path):
            return "cached"
        if ctx.adopt and path.exists():
            ctx.episode.mark(key, fp)
            return "adopted"
        if ctx.dry_run:
            ctx.planned.add(key)
            return "planned"
        tmp = path.with_suffix(".tmp.png")
        provider.generate(prompt, MASTER_ASPECT, tmp, refs)
        tmp.replace(path)
        ctx.episode.mark(key, fp)
        return "made"

    parallel(ctx, scenes, "art", work, lambda s: s.id)


# ----- motion (image-to-video) --------------------------------------------------

def motion_prompt(scene: Scene, specs: dict[str, CharacterSpec]) -> str:
    """What moves in the clip; falls back to the frame description when the script gives no motion."""
    names = {c: specs[c].name_en for c in scene.characters if c in specs}
    action = scene.motion or f"Gentle lively animation of this scene: {scene.visual_prompt}"
    who = f" Characters: {', '.join(names.values())}." if names else ""
    return f"{action}{who} Keep every face, costume and the background exactly as in the image; no camera cuts."


def motion(ctx: Context) -> None:
    ctx.require_approval("motion")
    provider = ctx.providers.video
    if provider is None:
        log.info("  [motion] no video provider set — illustrations stay stills")
        return
    ep = ctx.episode.load_script()
    specs = _cast_specs(ctx, ep)
    marked_only = ctx.config.render.animate == "marked"
    scenes = [s for s in ep.scenes if s.kind == "illustration" and (not ctx.only_scenes or s.id in ctx.only_scenes)
              and (s.motion or not marked_only)]
    log.info("  [motion] %d clip(s) with %s/%s", len(scenes), provider.name, provider.model)

    def work(scene: Scene) -> str:
        image = ctx.episode.image_path(scene)
        if not image.exists() and not ctx.dry_run:
            raise FileNotFoundError(f"missing illustration for {scene.id} — run the art stage")
        path = ctx.episode.motion_path(scene)
        prompt = motion_prompt(scene, specs)
        fp = fingerprint(provider.name, provider.model, prompt, ctx.episode.recorded(f"image:{scene.id}"))
        key = f"motion:{scene.id}"
        upstream = ctx.dry_run and f"image:{scene.id}" in ctx.planned
        if not upstream and not ctx.forced(scene.id) and ctx.episode.is_fresh(key, fp, path):
            return "cached"
        if ctx.adopt and path.exists():
            ctx.episode.mark(key, fp)
            return "adopted"
        if ctx.dry_run:
            return "planned"
        tmp = path.with_suffix(".tmp.mp4")
        try:
            provider.animate(image, prompt, tmp)
        except Exception as exc:  # motion is an enhancement: a failed clip leaves the still, never blocks the episode
            tmp.unlink(missing_ok=True)
            log.warning("  [motion] %s: %s — keeping the still image", scene.id, str(exc).splitlines()[0][:300])
            return "skipped"
        tmp.replace(path)
        ctx.episode.mark(key, fp)
        return "made"

    parallel(ctx, scenes, "motion", work, lambda s: s.id)


# ----- voice -------------------------------------------------------------------

def voice_for(ctx: Context, speaker: str, lang: str, specs: dict[str, CharacterSpec]) -> tuple[str, str]:
    """(voice name, base style) for a speaker in a language."""
    cfg = ctx.config
    if speaker == NARRATOR:
        v = cfg.narrator
        return (v.zh if lang == "zh" else v.en), (v.style_zh if lang == "zh" else v.style_en)
    spec = specs.get(speaker)
    if spec:
        chosen = spec.voice.zh if lang == "zh" else spec.voice.en
        style = spec.voice.style_zh if lang == "zh" else spec.voice.style_en
        if chosen:
            return chosen, style
    female = bool(spec and spec.gender == "female")
    by_lang = {("zh", True): cfg.voices.female_zh, ("zh", False): cfg.voices.male_zh,
               ("en", True): cfg.voices.female_en, ("en", False): cfg.voices.male_en}[(lang, female)]
    pool = by_lang or (cfg.voices.female if female else cfg.voices.male)
    narrator_voice = cfg.narrator.zh if lang == "zh" else cfg.narrator.en
    pool = [v for v in pool if v != narrator_voice] or pool  # never share the narrator's voice
    index = int(hashlib.md5(speaker.encode()).hexdigest(), 16) % len(pool)
    return pool[index], ""


def voice(ctx: Context) -> None:
    ctx.require_approval("voice")
    ep = ctx.episode.load_script()
    tts_for = ctx.providers.tts_for
    specs = _cast_specs(ctx, ep)
    jobs: list[tuple[Scene, int, Line, str]] = []
    for lang in ctx.config.languages:
        for scene in ep.scenes:
            if ctx.only_scenes and scene.id not in ctx.only_scenes:
                continue
            for i, line in enumerate(scene.lines):
                if line.text.get(lang):
                    jobs.append((scene, i, line, lang))
    log.info("  [voice] %d line(s) in %s with %s%s", len(jobs), "+".join(ctx.config.languages),
             "/".join(sorted({f"{tts_for(l).name}:{tts_for(l).model}" for l in ctx.config.languages})), "")

    def work(job: tuple[Scene, int, Line, str]) -> str:
        scene, i, line, lang = job
        provider = tts_for(lang)
        voice_name, base_style = voice_for(ctx, line.speaker, lang, specs)
        style = ", ".join(x for x in (base_style, line.delivery and f"delivery: {line.delivery}") if x)
        path = ctx.episode.voice_path(scene, lang, i)
        fp = fingerprint(provider.name, provider.model, voice_name, style, line.text[lang])
        key = f"voice:{scene.id}:{lang}:{i}"
        if not ctx.forced(scene.id) and ctx.episode.is_fresh(key, fp, path):
            return "cached"
        if ctx.adopt and path.exists():
            ctx.episode.mark(key, fp)
            return "adopted"
        if ctx.dry_run:
            return "planned"
        tmp = path.with_suffix(".tmp.wav")
        provider.synthesize(line.text[lang], voice_name, style, lang, tmp)
        tmp.replace(path)
        ctx.episode.mark(key, fp)
        return "made"

    parallel(ctx, jobs, "voice", work, lambda j: f"{j[0].id} {j[3]} line {j[1] + 1}")
