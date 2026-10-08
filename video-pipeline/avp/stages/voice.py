"""Stage 4 — voice-over per shot via TTS."""

from __future__ import annotations

from ..models import Scene
from ..project import fingerprint
from .common import Context, for_each_scene, log


def run(ctx: Context) -> None:
    sb = ctx.project.load_storyboard()
    provider = ctx.providers.tts
    voice = ctx.config.gemini.voice if provider.name == "gemini" else ""
    style = ctx.config.gemini.tts_style if provider.name == "gemini" else ""
    scenes = [s for s in sb.scenes if s.narration.strip()]
    log.info("  [voice] %d lines with %s/%s %s", len(scenes), provider.name, provider.model, voice)

    def work(scene: Scene) -> str:
        path = ctx.project.voice_path(scene)
        fp = fingerprint(provider.name, provider.model, voice, style, scene.narration, sb.language)
        key = f"voice:{scene.key}"
        if not ctx.forced(scene) and ctx.project.is_fresh(key, fp, path):
            return "cached"
        tmp = path.with_suffix(".tmp.wav")
        provider.synthesize(scene.narration, sb.language, tmp)
        tmp.replace(path)
        ctx.project.mark(key, fp)
        return "made"

    for_each_scene(ctx, scenes, "voice", work)
