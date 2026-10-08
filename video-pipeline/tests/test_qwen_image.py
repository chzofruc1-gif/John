import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from PIL import Image

from avp.config import QwenImageConfig
from avp.providers.qwen_image import QwenImage


def _png(color="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 18), color).save(buf, "PNG")
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def _send(self, body: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.seen.append(payload)
        host = self.headers["Host"]
        body = {"output": {"choices": [{"message": {"content": [{"image": f"http://{host}/result.png?sig=1"}]}}]}}
        self._send(json.dumps(body).encode(), "application/json")

    def do_GET(self):
        self._send(_png(), "image/png")


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_generate_with_references_and_download(server, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    ref = tmp_path / "sheet.png"
    ref.write_bytes(_png("blue"))
    QwenImage(QwenImageConfig(base_url=server), retries=0).generate("a chancellor", "9:16", tmp_path / "out.png", [ref])
    assert Image.open(tmp_path / "out.png").size == (32, 18)
    payload = Handler.seen[-1]
    content = payload["input"]["messages"][0]["content"]
    assert content[0]["image"].startswith("data:image/png;base64,") and content[-1] == {"text": "a chancellor"}
    assert payload["parameters"]["size"] == "928*1664" and payload["model"] == "qwen-image-2.0"


def test_error_body_is_reported():
    with pytest.raises(RuntimeError, match="DataInspectionFailed"):
        QwenImage.image_url({"code": "DataInspectionFailed", "message": "sensitive"})
