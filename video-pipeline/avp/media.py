"""Thin wrappers around the ffmpeg / ffprobe command-line tools."""

from __future__ import annotations

import logging
import shutil
import subprocess
import wave
from pathlib import Path

log = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise FFmpegError(f"{tool} not found on PATH — install ffmpeg (https://ffmpeg.org/download.html)")


def run_ffmpeg(args: list[str]) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args]
    log.debug("ffmpeg %s", " ".join(args))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}):\n{proc.stderr.strip()[-3000:]}")


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError as exc:
        raise FFmpegError(f"could not read duration of {path}: {proc.stderr.strip()}") from exc


def audio_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except (wave.Error, EOFError):
        return probe_duration(path)
