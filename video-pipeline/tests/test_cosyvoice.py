import sys
import types
from pathlib import Path

import pytest

from avp.config import CosyVoiceConfig, SeriesConfig
from avp.providers import build_providers


@pytest.fixture
def fake_dashscope(monkeypatch):
    calls = []

    class SpeechSynthesizer:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def call(self, text):
            calls.append((self.kwargs, text))
            return b"RIFF-fake-wav"

    tts_v2 = types.SimpleNamespace(SpeechSynthesizer=SpeechSynthesizer,
                                   AudioFormat=types.SimpleNamespace(WAV_24000HZ_MONO_16BIT="wav24k"))
    mod = types.ModuleType("dashscope")
    audio = types.ModuleType("dashscope.audio")
    monkeypatch.setitem(sys.modules, "dashscope", mod)
    monkeypatch.setitem(sys.modules, "dashscope.audio", audio)
    monkeypatch.setitem(sys.modules, "dashscope.audio.tts_v2", tts_v2)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    return mod, calls


def test_cosyvoice_synthesizes_with_voice_and_endpoint(fake_dashscope, tmp_path: Path):
    from avp.providers.cosyvoice import CosyVoiceTTS

    mod, calls = fake_dashscope
    tts = CosyVoiceTTS(CosyVoiceConfig(region="intl", use_instruction=True), retries=0)
    tts.synthesize("管仲说，盐你们照样买。", "longcheng_v2", "smug " * 40, "zh", tmp_path / "a.wav")
    assert (tmp_path / "a.wav").read_bytes() == b"RIFF-fake-wav"
    kwargs, text = calls[-1]
    assert kwargs["voice"] == "longcheng_v2" and kwargs["model"] == "cosyvoice-v2" and kwargs["format"] == "wav24k"
    assert len(kwargs["instruction"]) == 128
    assert mod.api_key == "sk-test" and "dashscope-intl" in mod.base_websocket_api_url


def test_cosyvoice_requires_key(fake_dashscope, monkeypatch):
    from avp.providers.cosyvoice import CosyVoiceTTS

    monkeypatch.delenv("DASHSCOPE_API_KEY")
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        CosyVoiceTTS(CosyVoiceConfig(), retries=0)


def test_per_language_tts_routing(fake_dashscope):
    cfg = SeriesConfig()
    cfg.providers.research = cfg.providers.llm = cfg.providers.image = "mock"
    cfg.providers.tts, cfg.providers.tts_en = "cosyvoice", "mock"
    providers = build_providers(cfg)
    assert providers.tts_for("zh").name == "cosyvoice"
    assert providers.tts_for("en").name == "mock"


def test_cosyvoice_auth_via_proxy(fake_dashscope, monkeypatch):
    from avp.providers.cosyvoice import CosyVoiceTTS

    mod, _ = fake_dashscope
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    CosyVoiceTTS(CosyVoiceConfig(auth_via_proxy=True), retries=0)
    assert mod.api_key == "injected-by-proxy"
