"""CosyVoice HTTP/SSE provider against a fake DashScope server (no network, no key)."""

import base64
import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from avp.config import CosyVoiceConfig, SeriesConfig
from avp.providers import build_providers
from avp.providers.cosyvoice import CosyVoiceError, CosyVoiceTTS, parse_sse_audio

PCM = b"\x01\x00" * 2400  # 0.1 s at 24 kHz


class Handler(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.seen.append((self.path, payload, dict(self.headers)))
        voice = payload["input"]["voice"]
        if voice == "bad_voice":
            events = [{"code": "InvalidParameter", "message": "Engine return error code: 418"}]
        else:
            half = len(PCM) // 2
            events = [{"output": {"type": "sentence-begin", "audio": {"data": ""}}},
                      {"output": {"audio": {"data": base64.b64encode(PCM[:half]).decode()}}},
                      {"output": {"audio": {"data": base64.b64encode(PCM[half:]).decode()}}},
                      {"output": {"finish_reason": "stop", "audio": {"url": "http://oss.example/x.wav"}}}]
        body = "".join(f"id:{i}\nevent:result\ndata:{json.dumps(e)}\n\n" for i, e in enumerate(events)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_synthesize_writes_wav_from_sse_chunks(server, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    tts = CosyVoiceTTS(CosyVoiceConfig(base_url=server, use_instruction=True, speech_rate=1.1), retries=0)
    tts.synthesize("管仲说，盐你们照样买。", "longcheng_v2", "smug " * 40, "zh", tmp_path / "a.wav")
    with wave.open(str(tmp_path / "a.wav")) as wf:
        assert (wf.getframerate(), wf.getnchannels(), wf.readframes(wf.getnframes())) == (24000, 1, PCM)
    path, payload, headers = Handler.seen[-1]
    assert path == "/api/v1/services/audio/tts/SpeechSynthesizer"
    assert payload["model"] == "cosyvoice-v2" and payload["input"]["format"] == "pcm"
    assert payload["input"]["rate"] == 1.1 and len(payload["input"]["instruction"]) == 128
    assert headers["Authorization"] == "Bearer sk-test" and headers["X-DashScope-SSE"] == "enable"


def test_error_event_raises(server, tmp_path: Path):
    tts = CosyVoiceTTS(CosyVoiceConfig(base_url=server, auth_via_proxy=True), retries=2)
    with pytest.raises(CosyVoiceError, match="418"):
        tts.synthesize("hi", "bad_voice", "", "zh", tmp_path / "x.wav")
    assert "Authorization" not in Handler.seen[-1][2]  # proxy mode sends no key


def test_requires_key_unless_proxy(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        CosyVoiceTTS(CosyVoiceConfig(), retries=0)


def test_parse_ignores_non_data_lines():
    lines = ["id:1", "event:result", ":HTTP_STATUS/200", "data:", f"data:{json.dumps({'output': {'audio': {'data': base64.b64encode(b'ab').decode()}}})}"]
    assert parse_sse_audio(lines) == b"ab"


def test_per_language_tts_routing(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    cfg = SeriesConfig()
    cfg.providers.research = cfg.providers.llm = cfg.providers.image = "mock"
    cfg.providers.tts, cfg.providers.tts_en = "cosyvoice", "mock"
    providers = build_providers(cfg)
    assert providers.tts_for("zh").name == "cosyvoice"
    assert providers.tts_for("en").name == "mock"
