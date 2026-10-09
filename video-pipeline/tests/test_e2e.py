"""End-to-end run with offline providers: real ffmpeg + Chromium rendering, no API calls."""

import json
import re
import shutil
from pathlib import Path

import pytest

from avp.cli import main

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def test_mock_episode_end_to_end(tmp_path: Path):
    series = tmp_path / "show"
    main(["init", str(series)])
    toml = (series / "series.toml").read_text(encoding="utf-8")
    toml = re.sub(r"^minutes = .*$", "minutes = 1", toml, flags=re.M)
    toml = re.sub(r"^height = .*$", "height = 360", toml, flags=re.M)
    (series / "series.toml").write_text(toml, encoding="utf-8")
    main(["new", str(series), "管仲与盐铁专营", "--slug", "salt"])
    ep = series / "episodes" / "01-salt"

    main(["run", str(ep), "--provider", "mock"])  # stops at the review gate
    assert (ep / "script.md").exists() and not (ep / "out").exists()

    main(["approve", str(ep)])
    main(["run", str(ep), "--provider", "mock"])

    out = ep / "out"
    assert (out / "youtube_en.mp4").exists() and (out / "youtube_en.srt").read_text().strip()
    parts = sorted(out.glob("douyin_zh_part*.mp4"))
    assert len(parts) == 3
    assert "✅" in (out / "qc.md").read_text() and "❌" not in (out / "qc.md").read_text()
    assert (out / "youtube_en.publish.md").exists() and (out / "cover_en.jpg").exists()
    assert json.loads((out / "youtube_en.timeline.json").read_text())["duration"] > 5
    assert list(ep.glob("assets/*/motion.mp4")), "mock video provider should animate illustrations"

    # Second run is fully incremental: nothing regenerated.
    state_before = (ep / "state.json").read_text()
    main(["run", str(ep), "--provider", "mock", "--from", "cast", "--to", "voice"])
    assert (ep / "state.json").read_text() == state_before
