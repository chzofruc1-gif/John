import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from PIL import Image

from avp.config import WanVideoConfig
from avp.providers.wan_video import WanVideo


class Handler(BaseHTTPRequestHandler):
    submitted: list = []
    polls = 0
    fail = False

    def log_message(self, *args):
        pass

    def _send(self, body: bytes, ctype: str = "application/json", code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        Handler.submitted.append((dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self._send(json.dumps({"output": {"task_id": "t1", "task_status": "PENDING"}}).encode())

    def do_GET(self):
        if self.path.startswith("/api/v1/tasks/"):
            Handler.polls += 1
            if Handler.fail:
                out = {"task_status": "FAILED", "code": "DataInspectionFailed", "message": "nope"}
            elif Handler.polls < 2:
                out = {"task_status": "RUNNING"}
            else:
                out = {"task_status": "SUCCEEDED", "video_url": f"http://{self.headers['Host']}/clip.mp4?sig=1"}
            self._send(json.dumps({"output": out}).encode())
        else:
            self._send(b"fake-mp4", "video/mp4")


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def _still(tmp_path: Path) -> Path:
    path = tmp_path / "image.png"
    Image.new("RGB", (64, 36), "red").save(path)
    return path


def test_submit_poll_download(server, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    cfg = WanVideoConfig(base_url=server, poll_interval=0.01, motion_style="paper puppets")
    WanVideo(cfg, retries=0).animate(_still(tmp_path), "the duke drools", tmp_path / "clip.mp4")
    assert (tmp_path / "clip.mp4").read_bytes() == b"fake-mp4"
    headers, payload = Handler.submitted[-1]
    assert headers["X-DashScope-Async"] == "enable" and headers["Authorization"] == "Bearer sk-test"
    assert payload["model"] == "wan2.2-i2v-flash"
    assert payload["input"]["prompt"] == "the duke drools paper puppets"
    assert payload["input"]["img_url"].startswith("data:image/jpeg;base64,")
    assert payload["parameters"]["resolution"] == "720P"


def test_failed_task_is_reported(server, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    Handler.fail = True
    try:
        with pytest.raises(RuntimeError, match="FAILED: DataInspectionFailed"):
            WanVideo(WanVideoConfig(base_url=server, poll_interval=0.01), retries=0).animate(
                _still(tmp_path), "x", tmp_path / "clip.mp4")
    finally:
        Handler.fail = False
