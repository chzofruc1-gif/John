"""Exercise the vendor-neutral providers against a fake local server (no network, no keys)."""

import base64
import io
import json
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from PIL import Image

from avp.config import CommandTTSConfig, EndpointConfig, SDWebUIConfig
from avp.providers.openai_compat import (CommandTTS, OpenAICompatImage, OpenAICompatLLM, OpenAICompatTTS,
                                         SDWebUIImage, extract_json)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 36), "red").save(buf, "PNG")
    return buf.getvalue()


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1), wf.setsampwidth(2), wf.setframerate(24000), wf.writeframes(b"\0\0" * 2400)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    requests: list = []

    def log_message(self, *args):
        pass

    def _send(self, body: bytes, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.requests.append((self.path, payload, self.headers.get("Authorization")))
        if self.path == "/v1/chat/completions":
            content = 'Sure!\n```json\n{"title": {"zh": "盐", "en": "Salt"}}\n```' if "JSON" in payload["messages"][0]["content"] \
                else "<think>hmm</think>Plain answer"
            self._send(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
        elif self.path == "/v1/images/generations":
            self._send(json.dumps({"data": [{"b64_json": base64.b64encode(_png()).decode()}]}).encode())
        elif self.path == "/v1/audio/speech":
            self._send(_wav(), "audio/wav")
        elif self.path == "/sdapi/v1/txt2img":
            self._send(json.dumps({"images": [base64.b64encode(_png()).decode()]}).encode())
        else:
            self.send_error(404)


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_llm_text_json_and_research(server, monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "sk-test")
    llm = OpenAICompatLLM(EndpointConfig(base_url=f"{server}/v1", api_key_env="FAKE_KEY", model="qwen"), retries=0)
    assert llm.text("outline", "hi") == "Plain answer"  # <think> stripped
    assert llm.json("script", "write", {"type": "object"}, {}) == {"title": {"zh": "盐", "en": "Salt"}}
    path, payload, auth = Handler.requests[-1]
    assert payload["response_format"] == {"type": "json_object"} and auth == "Bearer sk-test"
    assert "Generated without web search" in llm.research("topic").markdown


def test_images_and_speech(server, tmp_path: Path):
    img = OpenAICompatImage(EndpointConfig(base_url=f"{server}/v1", model="gpt-image-1"), retries=0)
    img.generate("a salt field", "16:9", tmp_path / "a.png", [])
    assert Image.open(tmp_path / "a.png").size == (64, 36)

    sd = SDWebUIImage(SDWebUIConfig(base_url=server), retries=0)
    sd.generate("a salt field", "16:9", tmp_path / "b.png", [])
    _, payload, _ = Handler.requests[-1]
    assert payload["width"] == 1344 and payload["height"] == 752

    tts = OpenAICompatTTS(EndpointConfig(base_url=f"{server}/v1", model="kokoro"), retries=0)
    tts.synthesize("你好", "zf_xiaobei", "warm", "zh", tmp_path / "c.wav")
    with wave.open(str(tmp_path / "c.wav")) as wf:
        assert wf.getframerate() == 24000


def test_command_tts(tmp_path: Path):
    script = tmp_path / "fake_tts.py"
    script.write_text("import sys, wave\n"
                      "text = sys.stdin.read()\n"
                      "w = wave.open(sys.argv[1], 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)\n"
                      "w.writeframes(b'\\0\\0' * 100 * len(text)); w.close()\n")
    tts = CommandTTS(CommandTTSConfig(zh=f"{sys.executable} {script} {{out}}"))
    tts.synthesize("管仲说：这很合理。", "voice", "", "zh", tmp_path / "out.wav")
    assert (tmp_path / "out.wav").stat().st_size > 44
    with pytest.raises(RuntimeError, match="no command"):
        tts.synthesize("hi", "v", "", "en", tmp_path / "x.wav")


def test_remote_endpoint_requires_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        OpenAICompatLLM(EndpointConfig(base_url="https://api.deepseek.com/v1", api_key_env="DEEPSEEK_API_KEY"), 0)


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Here you go:\n{"a": {"b": 2}}\nThanks') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        extract_json("no json here")
