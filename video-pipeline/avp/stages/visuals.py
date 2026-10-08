"""Stage 2 — one key frame per shot from the image model."""

from __future__ import annotations

from ..models import Scene, Storyboard
from ..project import fingerprint
from .common import Context, for_each_scene, log

NO_TEXT = "No text, letters, captions, subtitles, watermarks or logos anywhere in the image."


def image_prompt(scene: Scene, storyboard: Storyboard) -> str:
    return f"{scene.visual_prompt}\n\nArt direction: {storyboard.visual_style}\nAspect ratio {storyboard.aspect}. {NO_TEXT}"


def run(ctx: Context) -> None:
    sb = ctx.project.load_storyboard()
    provider = ctx.providers.image
    log.info("  [visuals] %d frames with %s/%s", len(sb.scenes), provider.name, provider.model)

    def work(scene: Scene) -> str:
        path = ctx.project.image_path(scene)
        prompt = image_prompt(scene, sb)
        fp = fingerprint(provider.name, provider.model, prompt, sb.aspect)
        key = f"image:{scene.key}"
        if not ctx.forced(scene) and ctx.project.is_fresh(key, fp, path):
            return "cached"
        tmp = path.with_suffix(".tmp.png")
        provider.generate(prompt, sb.aspect, tmp)
        tmp.replace(path)
        ctx.project.mark(key, fp)
        return "made"

    for_each_scene(ctx, sb.scenes, "visuals", work)
