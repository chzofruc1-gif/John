"""Edit decision list: when each shot starts, how long it lasts, where its voice-over sits.

Shots are joined with crossfades of `transition` seconds, so shot i+1 starts `transition`
seconds before shot i ends. Padding around each voice-over keeps speech out of the fades.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Scene
from .subtitles import Cue, cues_for

FIRST_LEAD = 0.4      # silence before the very first line
GAP = 0.15            # extra silence on each side of a crossfade
LAST_TAIL = 1.0       # hold on the final shot
MIN_SHOT = 2.5
SILENT_SHOT = 3.0     # shots without narration


@dataclass
class Shot:
    scene: Scene
    start: float          # global start time
    duration: float
    voice_offset: float   # voice start, relative to the shot
    voice_duration: float
    cues: list[Cue] = field(default_factory=list)  # relative to the shot

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def voice_start(self) -> float:
        return self.start + self.voice_offset


@dataclass
class Timeline:
    shots: list[Shot]
    transition: float

    @property
    def duration(self) -> float:
        return self.shots[-1].end if self.shots else 0.0

    def global_cues(self) -> list[Cue]:
        return [Cue(round(s.start + c.start, 3), round(s.start + c.end, 3), c.text) for s in self.shots for c in s.cues]


def build_timeline(scenes: list[Scene], voice_durations: list[float], transition: float) -> Timeline:
    shots: list[Shot] = []
    t = 0.0
    n = len(scenes)
    for i, (scene, voice) in enumerate(zip(scenes, voice_durations)):
        lead = FIRST_LEAD if i == 0 else transition + GAP
        tail = LAST_TAIL if i == n - 1 else transition + GAP
        if voice > 0:
            duration = max(MIN_SHOT, lead + voice + tail)
        else:
            duration = max(SILENT_SHOT, transition * 2 + 0.5)
        cues = cues_for(scene.narration, lead, voice) if voice > 0 and scene.narration else []
        shots.append(Shot(scene, round(t, 3), round(duration, 3), lead, voice, cues))
        t += duration - transition
    return Timeline(shots, transition)
