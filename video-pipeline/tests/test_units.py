from pathlib import Path

import pytest

from avp.config import SeriesConfig, frame_size, load_series_config, merge_into
from avp.models import Episode
from avp.providers.mock import MockLLM
from avp.stages.finish import youtube_chapters
from avp.subtitles import split_narration, to_srt, cues_for
from avp.timeline import SpokenLine, build_timeline

TEMPLATE = Path(__file__).parents[1] / "avp" / "templates" / "econ-history.toml"


def test_split_cjk_breaks_on_punctuation_and_length():
    lines = split_narration("管仲当上齐国的国相之后，面对一个难题：国库空虚，百姓却不愿意多交税。他想出了一个绝妙的办法。")
    assert all(len(line) <= 16 for line in lines)
    assert "".join(lines).replace("，", "").replace("。", "") .startswith("管仲当上齐国的国相之后")
    assert not any(line.endswith("，") for line in lines)


def test_split_english_wraps_on_words():
    lines = split_narration("Guan Zhong had a problem: the treasury was empty, and nobody in the state of Qi wanted to pay more taxes.")
    assert all(len(line) <= 42 for line in lines)
    assert all(not line.startswith(" ") for line in lines)


def test_cues_cover_the_clip_and_srt_format():
    cues = cues_for("第一句。第二句话更长一些。", 1.0, 3.0)
    assert cues[0].start == 1.0 and abs(cues[-1].end - 4.0) < 1e-6
    srt = to_srt(cues)
    assert srt.startswith("1\n00:00:01,000 --> ")


def test_frame_size():
    assert frame_size("16:9", 1080) == (1920, 1080)
    assert frame_size("9:16", 1080) == (1080, 1920)
    assert frame_size("1:1", 721) == (720, 720)


def test_template_loads_and_validates():
    cfg = load_series_config(TEMPLATE)
    assert [o.id for o in cfg.outputs] == ["youtube_en", "douyin_zh"]
    assert cfg.languages == ["zh", "en"]


def test_unknown_config_key_is_rejected():
    with pytest.raises(ValueError, match="unknown config key: render.heigth"):
        merge_into(SeriesConfig(), {"render": {"heigth": 720}})


def test_timeline_keeps_speech_out_of_crossfades():
    script = Episode.from_dict(MockLLM().json("script", "", {}, {"brief": "盐铁", "scenes": 4, "parts": 2}))
    scenes = [(s, [SpokenLine("x", "", 2.0), SpokenLine("y", "管仲：", 1.0)]) for s in script.scenes]
    tl = build_timeline(scenes, transition=0.4, line_gap=0.25)
    for prev, nxt in zip(tl.shots, tl.shots[1:]):
        assert abs(nxt.start - (prev.end - 0.4)) < 1e-6  # crossfade overlap
        last_offset, last_line = prev.lines[-1]
        assert prev.start + last_offset + last_line.duration <= prev.end - 0.4  # speech ends before the fade
        assert nxt.start + nxt.lines[0][0] >= prev.end  # next speech starts after the fade
    assert tl.shots[0].cues[-1].text.startswith("管仲：")


def test_mock_script_is_consistent():
    raw = MockLLM().json("script", "", {}, {"brief": "测试", "scenes": 6, "parts": 3})
    ep = Episode.from_dict(raw)
    assert ep.validate() == []
    assert ep.parts() == [1, 2, 3]
    assert Episode.from_dict(ep.to_dict()).to_dict() == ep.to_dict()


def test_validate_flags_unknown_speaker_and_claim():
    raw = MockLLM().json("script", "", {}, {"brief": "测试", "scenes": 2, "parts": 1})
    raw["scenes"][0]["lines"].append({"speaker": "ghost", "text": {"zh": "嘿", "en": "hey"}})
    raw["scenes"][0]["claims"].append("c99")
    problems = Episode.from_dict(raw).validate()
    assert any("ghost" in p for p in problems) and any("c99" in p for p in problems)


def test_youtube_chapters_rules():
    chapters = [{"start": 5.0, "title": "Act 1"}, {"start": 9.0, "title": "too close"}, {"start": 60, "title": "Act 2"}]
    assert youtube_chapters(chapters, "Intro") == ["0:00 Intro", "0:05 Act 1", "1:00 Act 2"]
    assert youtube_chapters([{"start": 0, "title": "only"}], "Intro") == []
