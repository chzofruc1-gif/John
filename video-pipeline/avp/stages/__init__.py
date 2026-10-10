"""Pipeline stages, in execution order. Everything after `script` waits for the script to be approved."""

from .assets import art, cast, motion, voice
from .compose import compose
from .finish import publish, qc
from .writing import outline, research, script

STAGES = {
    "research": research,   # brief -> research.md (web-grounded dossier with sources)
    "outline": outline,     # research -> outline.md (acts, beats, diagrams, comedy beats)
    "script": script,       # outline -> script.json + script.md (bilingual, footnoted)
    # ---- review gate: fact-check script.md, `avp revise`, then `avp approve` ----
    "cast": cast,           # character reference sheets (series-level, reused across episodes)
    "art": art,             # one illustration per illustration scene
    "motion": motion,       # illustrations animated into short clips (if a video provider is set)
    "voice": voice,         # one WAV per line per language
    "compose": compose,     # every output file: segments, diagrams, subtitles, mix
    "publish": publish,     # titles, descriptions, chapters, sources, hashtags
    "qc": qc,               # duration / audio / loudness checks + contact sheets
}
ORDER = list(STAGES)
WRITING_STAGES = ORDER[:3]
