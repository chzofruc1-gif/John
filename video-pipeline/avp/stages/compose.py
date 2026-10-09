"""Stage 7 — render every output (language × aspect × part) to MP4 with subtitles.

Per output file:
  1. lay out a timeline from the real voice-line durations of that language
  2. render one video segment per shot (Ken Burns illustration or animated diagram + text overlays)
  3. build the voice track, join segments with crossfades, mix music, normalise loudness
"""

from __future__ import annotations

import json
import os
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ..config import OutputSpec, frame_size
from ..diagrams import TEMPLATE, DiagramRenderer, diagram_data
from ..media import audio_duration, require_ffmpeg, run_ffmpeg
from ..models import NARRATOR, Episode, Scene
from ..overlays import OverlayRenderer, cropped, find_font_path, make_cover
from ..project import fingerprint
from ..subtitles import to_srt
from ..timeline import Shot, SpokenLine, Timeline, build_timeline
from .common import Context, log

VOICE_RATE = 24000
KB_HEADROOM = 1.2   # source is pre-scaled this much larger than the frame so moves never show edges
BANNER_SECONDS = 3.5


@dataclass
class OutputFile:
    spec: OutputSpec
    name: str              # file stem, e.g. "douyin_zh_part2"
    part: int | None       # None for a full-length output
    scenes: list[Scene]


def plan_outputs(ep: Episode, specs: list[OutputSpec]) -> list[OutputFile]:
    files = []
    for spec in specs:
        if spec.split_parts and len(ep.parts()) > 1:
            for p in ep.parts():
                files.append(OutputFile(spec, f"{spec.id}_part{p}", p, [s for s in ep.scenes if s.part == p]))
        else:
            files.append(OutputFile(spec, spec.id, None, list(ep.scenes)))
    return files


# ----- filters ----------------------------------------------------------------

def ken_burns(camera: str, bw: int, bh: int, duration: float) -> str:
    """Filter chain: cover-crop the still to the box, then move. Expects a looped image input."""
    big_w, big_h = round(bw * KB_HEADROOM / 2) * 2, round(bh * KB_HEADROOM / 2) * 2
    s = f"min(t/{duration:.3f},1)"
    zoom, x = {
        "zoom-in": (f"(1+0.15*{s})", "(iw-ow)/2"),
        "zoom-out": (f"(1.15-0.15*{s})", "(iw-ow)/2"),
        "pan-left": ("1.15", f"(iw-ow)*(1-{s})"),
        "pan-right": ("1.15", f"(iw-ow)*{s}"),
    }.get(camera, (f"(1.04+0.03*{s})", "(iw-ow)/2"))
    return (f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h},"
            f"scale=w='trunc({bw}*{zoom}/2)*2':h='trunc({bh}*{zoom}/2)*2':eval=frame,"
            f"crop={bw}:{bh}:x='{x}':y='(ih-oh)/2',setsar=1")


def illustration_base(camera: str, w: int, h: int, duration: float, clip_fps: int = 0) -> str:
    """[0:v] -> [base]. Portrait frames show a square crop of the art over a blurred fill.

    With `clip_fps`, input 0 is an animated clip of the art: it plays once, then its last frame holds
    for the rest of the shot while the camera keeps moving."""
    src = "[0:v]"
    if clip_fps:
        src = f"[0:v]fps={clip_fps},tpad=stop_mode=clone:stop_duration={duration:.3f},trim=duration={duration:.3f},setpts=PTS-STARTPTS,"
    if h <= w:
        return f"{src}{ken_burns(camera, w, h, duration)},format=yuv420p[base]"
    box = w  # square foreground, art is composed with the subject in the centre
    top = round(h * 0.2)
    return (f"{src}split[a][b];"
            f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=24:2,"
            f"eq=brightness=-0.12:saturation=0.8,setsar=1[bg];"
            f"[b]{ken_burns(camera, box, box, duration)}[fg];"
            f"[bg][fg]overlay=0:{top},format=yuv420p[base]")


@dataclass
class Layer:
    png: Path
    start: float
    end: float


def render_segment(shot: Shot, base_input: list[str], base_filter: str, layers: list[Layer],
                   fps: int, crf: int, out: Path) -> None:
    d = shot.duration
    args = list(base_input)
    chains = [base_filter]
    label = "base"
    for i, layer in enumerate(layers, start=1):
        png, x, y = cropped(layer.png)  # small single-frame overlays keep compositing cheap
        args += ["-i", str(png)]
        chains.append(f"[{label}][{i}:v]overlay={x}:{y}:eof_action=repeat:"
                      f"enable='between(t,{layer.start:.3f},{layer.end:.3f})'[v{i}]")
        label = f"v{i}"
    run_ffmpeg([*args, "-filter_complex", ";".join(chains), "-map", f"[{label}]",
                "-t", f"{d:.3f}", "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
                "-pix_fmt", "yuv420p", "-an", str(out)])


# ----- audio ------------------------------------------------------------------

def read_pcm(path: Path, tmp_dir: Path) -> bytes:
    """16-bit mono 24 kHz PCM from a WAV (converting with ffmpeg if it is in another format)."""
    try:
        with wave.open(str(path), "rb") as wf:
            if (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (1, 2, VOICE_RATE):
                return wf.readframes(wf.getnframes())
    except wave.Error:
        pass
    conv = tmp_dir / f"conv_{path.parent.name}_{path.name}"
    run_ffmpeg(["-i", str(path), "-ac", "1", "-ar", str(VOICE_RATE), "-sample_fmt", "s16", str(conv)])
    with wave.open(str(conv), "rb") as wf:
        return wf.readframes(wf.getnframes())


def build_voice_track(timeline: Timeline, out: Path, tmp_dir: Path) -> Path:
    total = int(timeline.duration * VOICE_RATE) * 2
    buf = bytearray(total)
    for shot in timeline.shots:
        for offset, line in shot.lines:
            pcm = read_pcm(Path(line.audio), tmp_dir)
            start = int((shot.start + offset) * VOICE_RATE) * 2
            pcm = pcm[: max(0, total - start)]
            buf[start:start + len(pcm)] = pcm
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(VOICE_RATE)
        wf.writeframes(bytes(buf))
    return out


# ----- per output file ----------------------------------------------------------

def spoken_lines(ctx: Context, ep: Episode, scene: Scene, lang: str) -> list[SpokenLine]:
    lines = []
    for i, line in enumerate(scene.lines):
        text = line.text.get(lang, "")
        if not text:
            continue
        path = ctx.episode.voice_path(scene, lang, i)
        if not path.exists():
            raise FileNotFoundError(f"missing voice line {path.relative_to(ctx.episode.root)} — run the voice stage")
        label = ""
        if line.speaker != NARRATOR:
            name = ep.speaker_name(line.speaker, lang)
            label = f"{name}：" if lang == "zh" else f"{name}: "
        lines.append(SpokenLine(text, label, audio_duration(path), path))
    return lines


def render_file(ctx: Context, ep: Episode, of: OutputFile, font: str | None) -> Path:
    cfg, r = ctx.config, ctx.config.render
    lang = of.spec.language
    w, h = frame_size(of.spec.aspect, r.height)
    timeline = build_timeline([(s, spoken_lines(ctx, ep, s, lang)) for s in of.scenes], r.transition, r.line_gap)
    work = ctx.episode.build_dir / of.name
    work.mkdir(parents=True, exist_ok=True)
    overlays = OverlayRenderer(w, h, font)
    burn = of.spec.subtitles in ("burn", "both")
    series_name = cfg.series.name_zh if lang == "zh" else cfg.series.name_en
    banner = f"{series_name} · EP{ep.number:02d}"
    if of.part:
        banner += f" ({of.part}/{len(ep.parts())})"

    def layers_for(index: int, shot: Shot) -> list[Layer]:
        layers: list[Layer] = []
        d = shot.duration
        hold_end = d - (r.transition if index < len(timeline.shots) - 1 else 0.3)
        caption = banner if index == 0 else (shot.scene.on_screen.get(lang, "") if shot.scene.kind == "illustration" else "")
        if caption:
            png = overlays.caption(caption, work / f"{shot.scene.id}_caption.png")
            end = min(hold_end, BANNER_SECONDS) if index == 0 else hold_end
            layers.append(Layer(png, 0.25 if index == 0 else r.transition, end))
        if burn:
            for k, cue in enumerate(shot.cues):
                png = overlays.subtitle(cue.text, work / f"{shot.scene.id}_sub{k:02d}.png")
                layers.append(Layer(png, cue.start, min(cue.end, d)))
        return layers

    def clip_for(scene: Scene) -> Path | None:
        """The animated clip for an illustration, when motion is on and the clip exists."""
        clip = ctx.episode.motion_path(scene)
        return clip if ctx.providers.video is not None and clip.exists() else None

    def segment_key(index: int, shot: Shot) -> tuple[Path, str]:
        s = shot.scene
        if s.kind == "diagram":  # template edits must re-render diagrams too
            content = [diagram_data(s.diagram, lang), fingerprint(TEMPLATE.read_text(encoding="utf-8"))]
        else:
            content = ctx.episode.recorded(f"image:{s.id}") or str(ctx.episode.image_path(s).stat().st_mtime)
            if clip_for(s):
                content = ["motion", ctx.episode.recorded(f"motion:{s.id}") or str(clip_for(s).stat().st_mtime)]
        fp = fingerprint("seg-v2", s.kind, s.camera, content, shot.duration, [(c.start, c.end, c.text) for c in shot.cues],
                         w, h, r.fps, r.crf, burn, banner if index == 0 else "", s.on_screen.get(lang), font, r.transition,
                         index == len(timeline.shots) - 1)
        return work / f"{index:03d}_{s.id}.mp4", fp

    jobs = []
    for index, shot in enumerate(timeline.shots):
        s = shot.scene
        out, fp = segment_key(index, shot)
        key = f"seg:{of.name}:{index}"
        if not ctx.forced(s.id) and ctx.episode.is_fresh(key, fp, out):
            continue
        if s.kind == "illustration" and not ctx.episode.image_path(s).exists():
            raise FileNotFoundError(f"missing illustration for {s.id} — run the art stage")
        jobs.append((index, shot, out, key, fp))

    def run(job):
        index, shot, out, key, fp = job
        s = shot.scene
        if s.kind == "diagram":
            clip = work / f"{s.id}_diagram.mp4"
            with DiagramRenderer(r.chromium) as renderer:  # one browser per job: Playwright is thread-bound
                renderer.render(s.diagram, lang, w, h, shot.duration, r.fps, clip, r.crf)
            base_input, base_filter = ["-i", str(clip)], "[0:v]setsar=1,format=yuv420p[base]"
        elif clip_for(s):
            base_input = ["-i", str(clip_for(s))]
            base_filter = illustration_base(s.camera, w, h, shot.duration, clip_fps=r.fps)
        else:
            image = ctx.episode.image_path(s)
            base_input = ["-loop", "1", "-framerate", str(r.fps), "-t", f"{shot.duration:.3f}", "-i", str(image)]
            base_filter = illustration_base(s.camera, w, h, shot.duration)
        render_segment(shot, base_input, base_filter, layers_for(index, shot), r.fps, r.crf, out)
        ctx.episode.mark(key, fp)

    workers = r.workers or max(1, (os.cpu_count() or 2) // 2)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(run, jobs))
    log.info("  [compose] %s: %d shots (%d rendered), %.1fs", of.name, len(timeline.shots), len(jobs), timeline.duration)

    # Join, mix, encode.
    segments = [segment_key(i, s)[0] for i, s in enumerate(timeline.shots)]
    voice = build_voice_track(timeline, work / "voice.wav", work)
    out_dir = ctx.episode.out_dir
    srt = out_dir / f"{of.name}.srt"
    srt.write_text(to_srt(timeline.global_cues()), encoding="utf-8")
    final = out_dir / f"{of.name}.mp4"
    mix(segments, timeline, voice, srt if of.spec.subtitles in ("soft", "both") else None, lang, ctx, final)
    write_timeline_json(timeline, lang, out_dir / f"{of.name}.timeline.json")
    return final


def mix(segments: list[Path], timeline: Timeline, voice: Path, soft_srt: Path | None, lang: str,
        ctx: Context, out: Path) -> None:
    r = ctx.config.render
    total = timeline.duration
    args: list[str] = []
    for seg in segments:
        args += ["-i", str(seg)]
    n = len(segments)
    filters = []
    for i in range(n):
        filters.append(f"[{i}:v]settb=AVTB,fps={r.fps},format=yuv420p[s{i}]")
    if n == 1:
        filters.append("[s0]null[vout]")
    elif r.transition > 0:
        prev = "s0"
        for i in range(1, n):
            label = "vout" if i == n - 1 else f"x{i}"
            filters.append(f"[{prev}][s{i}]xfade=transition={r.transition_style}:duration={r.transition}:"
                           f"offset={timeline.shots[i].start:.3f}[{label}]")
            prev = label
    else:
        filters.append("".join(f"[s{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vout]")

    args += ["-i", str(voice)]
    vi = n
    filters.append(f"[{vi}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                   f"apad=whole_dur={total:.3f},atrim=0:{total:.3f}[voice]")
    audio = "voice"
    bgm = Path(r.bgm).expanduser() if r.bgm else None
    if bgm and not bgm.is_absolute():
        bgm = ctx.series.root / bgm
    if bgm and bgm.exists():
        args += ["-stream_loop", "-1", "-i", str(bgm)]
        bi = n + 1
        fade_st = max(0.0, total - 3)
        filters.append(f"[{bi}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                       f"volume={r.bgm_volume},atrim=0:{total:.3f},afade=t=in:d=1.5,afade=t=out:st={fade_st:.3f}:d=3[bgm]")
        filters.append("[voice]asplit=2[vmain][vkey]")
        filters.append("[bgm][vkey]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[duck]")
        filters.append("[vmain][duck]amix=inputs=2:normalize=0:duration=first[mixed]")
        audio = "mixed"
    elif r.bgm:
        log.warning("  [compose] bgm file not found: %s", r.bgm)
    if r.loudnorm:
        filters.append(f"[{audio}]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[aout]")
    else:
        filters.append(f"[{audio}]anull[aout]")

    maps = ["-map", "[vout]", "-map", "[aout]"]
    sub_args: list[str] = []
    if soft_srt:
        args += ["-i", str(soft_srt)]
        maps += ["-map", f"{n + (2 if bgm and bgm.exists() else 1)}:s"]
        sub_args = ["-c:s", "mov_text", "-metadata:s:s:0", f"language={'chi' if lang == 'zh' else 'eng'}"]
    run_ffmpeg([*args, "-filter_complex", ";".join(filters), *maps,
                "-c:v", "libx264", "-preset", "medium", "-crf", str(r.crf), "-pix_fmt", "yuv420p", "-r", str(r.fps),
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", *sub_args,
                "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)])


def write_timeline_json(timeline: Timeline, lang: str, path: Path) -> None:
    chapters = [{"start": s.start, "title": s.scene.chapter.get(lang, "")} for s in timeline.shots if s.scene.chapter.get(lang)]
    path.write_text(json.dumps({
        "duration": round(timeline.duration, 3),
        "shots": [{"scene": s.scene.id, "start": s.start, "duration": s.duration} for s in timeline.shots],
        "chapters": chapters,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compose(ctx: Context) -> None:
    ctx.require_approval("compose")
    require_ffmpeg()
    ep = ctx.episode.load_script()
    specs = [o for o in ctx.config.outputs if not ctx.only_outputs or o.id in ctx.only_outputs]
    font = find_font_path(ctx.config.render.font)
    for of in plan_outputs(ep, specs):
        path = render_file(ctx, ep, of, font)
        log.info("  [compose] -> %s", path.relative_to(ctx.episode.root))
    first_art = next((ctx.episode.image_path(s) for s in ep.scenes
                      if s.kind == "illustration" and ctx.episode.image_path(s).exists()), None)
    if first_art:
        for lang in ctx.config.languages:
            make_cover(first_art, ep.title[lang], 1280, 720, font, ctx.episode.out_dir / f"cover_{lang}.jpg")
