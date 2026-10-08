"""Pipeline stages, in execution order."""

from . import compose, motion, script, visuals, voice

STAGES = {
    "script": script.run,     # brief -> storyboard.json
    "visuals": visuals.run,   # storyboard -> key frame per scene
    "motion": motion.run,     # key frames -> motion clips (only if a video provider is set)
    "voice": voice.run,       # narration -> voice.wav per scene
    "compose": compose.run,   # everything -> final.mp4, subtitles.srt, cover.jpg
}
ORDER = list(STAGES)
