"""Edit decision list: when each shot starts, how long it lasts, where its lines are spoken.

Shots are joined with crossfades of `transition` seconds (shot i+1 starts `transition` seconds
before shot i ends). Padding around each scene's speech keeps words out of the crossfades.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Scene
from .subtitles import Cue, cues_for

FIRST_LEAD = 0.5      # silence before the very first line
GAP = 0.2             # extra silence on each side of a crossfade
LAST_TAIL = 1.2       # hold on the final shot
MIN_SHOT = 2.5
SILENT_SHOT = 3.0     # shots without any speech


@dataclass
class SpokenLine:
    text: str
    label: str            # prefix for the first subtitle of the line, e.g. "管仲：" ("" for narrator)
    duration: float
    audio: object = None  # path to the WAV, carried through for the mixer


@dataclass
class Shot:
    scene: Scene
    start: float          # global start time
    duration: float
    voice_offset: float   # first line starts here, relative to the shot
    lines: list[tuple[float, SpokenLine]] = field(default_factory=list)  # (offset in shot, line)
    cues: list[Cue] = field(default_factory=list)                         # relative to the shot

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class Timeline:
    shots: list[Shot]
    transition: float

    @property
    def duration(self) -> float:
        return self.shots[-1].end if self.shots else 0.0

    def global_cues(self) -> list[Cue]:
        return [Cue(round(s.start + c.start, 3), round(s.start + c.end, 3), c.text) for s in self.shots for c in s.cues]


def build_timeline(scenes: list[tuple[Scene, list[SpokenLine]]], transition: float, line_gap: float) -> Timeline:
    shots: list[Shot] = []
    t = 0.0
    n = len(scenes)
    for i, (scene, spoken) in enumerate(scenes):
        lead = FIRST_LEAD if i == 0 else transition + GAP
        tail = LAST_TAIL if i == n - 1 else transition + GAP
        placed: list[tuple[float, SpokenLine]] = []
        cues: list[Cue] = []
        cursor = lead
        for line in spoken:
            if line.duration <= 0:
                continue
            placed.append((round(cursor, 3), line))
            line_cues = cues_for(line.text, cursor, line.duration)
            if line.label and line_cues:
                line_cues[0].text = f"{line.label}{line_cues[0].text}"
            cues.extend(line_cues)
            cursor += line.duration + line_gap
        if placed:
            speech_end = cursor - line_gap
            duration = max(MIN_SHOT, speech_end + tail)
        else:
            duration = max(SILENT_SHOT, transition * 2 + 0.5)
        shots.append(Shot(scene, round(t, 3), round(duration, 3), lead, placed, cues))
        t += duration - transition
    return Timeline(shots, transition)
