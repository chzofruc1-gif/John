"""Stage 3 (optional) — animate key frames into motion clips with an image-to-video model."""

from __future__ import annotations

from ..models import Scene
from ..project import fingerprint
from .common import Context, for_each_scene, log


def run(ctx: Context) -> None:
    provider = ctx.providers.video
    if provider is None:
        log.info("  [motion] no video provider configured — shots will use Ken Burns moves")
        return
    sb = ctx.project.load_storyboard()
    log.info("  [motion] %d clips with %s/%s", len(sb.scenes), provider.name, provider.model)

    def work(scene: Scene) -> str:
        image = ctx.project.image_path(scene)
        if not image.exists():
            raise FileNotFoundError("key frame missing — run the visuals stage first")
        path = ctx.project.clip_path(scene)
        prompt = (f"{scene.motion_prompt or scene.visual_prompt} "
                  f"Camera: {scene.camera.replace('-', ' ')}, smooth and subtle. {sb.visual_style}")
        fp = fingerprint(provider.name, provider.model, prompt, sb.aspect, ctx.project.recorded(f"image:{scene.key}"))
        key = f"clip:{scene.key}"
        if not ctx.forced(scene) and ctx.project.is_fresh(key, fp, path):
            return "cached"
        tmp = path.with_suffix(".tmp.mp4")
        provider.animate(image, prompt, sb.aspect, tmp)
        tmp.replace(path)
        ctx.project.mark(key, fp)
        return "made"

    for_each_scene(ctx, sb.scenes, "motion", work)
