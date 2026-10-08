"""Stage 1 — brief -> storyboard.json (title, art direction, shot list with narration + prompts)."""

from __future__ import annotations

from ..models import CAMERA_MOVES, Storyboard
from ..providers import ScriptRequest
from .common import Context, log

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "logline": {"type": "string"},
        "visual_style": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narration": {"type": "string"},
                    "visual_prompt": {"type": "string"},
                    "on_screen_text": {"type": "string"},
                    "camera": {"type": "string", "enum": list(CAMERA_MOVES)},
                    "motion_prompt": {"type": "string"},
                },
                "required": ["narration", "visual_prompt", "on_screen_text", "camera", "motion_prompt"],
            },
        },
    },
    "required": ["title", "logline", "visual_style", "scenes"],
}


def build_prompt(brief: str, *, scenes: int, duration: int, language: str, aspect: str, style: str) -> str:
    lang = "Simplified Chinese" if language == "zh" else "English"
    # Speaking pace: ~4.5 Chinese characters or ~2.6 English words per second.
    budget = (f"about {round(duration * 4.5)} Chinese characters in total" if language == "zh"
              else f"about {round(duration * 2.6)} words in total")
    orientation = {"9:16": "vertical, mobile-first", "1:1": "square"}.get(aspect, "horizontal widescreen")
    return f"""You are an award-winning short-form video director and scriptwriter.
Turn the brief below into a storyboard for a ~{duration}-second narrated video with exactly {scenes} shots.
Frame: {aspect} ({orientation}).

Write each field as follows:
- narration: the voice-over for that shot in {lang}. Spoken, vivid, concrete. Shot 1 must hook the viewer
  within 3 seconds; the last shot lands the takeaway or a call to action. All narration together: {budget}.
  No stage directions, no speaker labels, no emoji.
- visual_prompt: a detailed ENGLISH prompt for an image model describing ONE frame — subject, action, setting,
  composition, lighting, lens. Describe recurring characters/places with identical wording in every shot so
  they stay consistent. Never request text, letters, captions, logos or watermarks in the image.
- on_screen_text: a punchy headline in {lang} (max 10 Chinese characters / 5 English words), or "" for none.
- camera: the Ken Burns move that best fits the shot.
- motion_prompt: one ENGLISH sentence describing subtle motion for an image-to-video model (what moves, how).
- visual_style: one ENGLISH sentence of global art direction shared by every frame, based on: {style}
- title: a short, catchy title in {lang}. logline: one-sentence summary in {lang}.

Brief:
{brief}"""


def run(ctx: Context) -> None:
    project, cfg = ctx.project, ctx.config
    if project.storyboard_path.exists() and not ctx.force:
        log.info("  [script] storyboard.json exists — keeping it (use --force to rewrite)")
        return
    p = cfg.project
    request = ScriptRequest(
        brief=project.brief,
        prompt=build_prompt(project.brief, scenes=p.scenes, duration=p.duration, language=p.language,
                            aspect=p.aspect, style=p.style_prompt),
        schema=SCHEMA,
        scenes=p.scenes,
        language=p.language,
        style_prompt=p.style_prompt,
    )
    log.info("  [script] writing storyboard with %s/%s", ctx.providers.llm.name, getattr(ctx.providers.llm, "model", ""))
    raw = ctx.providers.llm.write_storyboard(request)
    raw.update(language=p.language, aspect=p.aspect)
    if not raw.get("visual_style"):
        raw["visual_style"] = p.style_prompt
    storyboard = Storyboard.from_dict(raw)
    storyboard.save(project.storyboard_path)
    log.info("  [script] '%s' — %d shots -> %s", storyboard.title, len(storyboard.scenes), project.storyboard_path)
