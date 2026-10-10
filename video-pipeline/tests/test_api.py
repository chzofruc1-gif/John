"""The agent-facing interfaces: avp.api, `avp --json`, the MCP server and custom provider plugins."""

import json
import re
import shutil
from pathlib import Path

import pytest
from PIL import Image

from avp import api
from avp.cli import main
from avp.mcp_server import handle

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


class FakeImage:
    """A custom provider loaded from series.toml as "test_api:FakeImage"."""

    def __init__(self, config, kind):
        self.name, self.model = "fake", "v1"
        self.calls = 0

    def generate(self, prompt, aspect, out_path, references):
        Image.new("RGB", (64, 36), "teal").save(out_path, "PNG")


def _series(tmp_path: Path, **swap) -> Path:
    root = tmp_path / "show"
    api.init_series(root)
    toml = (root / "series.toml").read_text(encoding="utf-8")
    toml = re.sub(r"^minutes = .*$", "minutes = 1", toml, flags=re.M)
    for key, value in swap.items():
        toml = re.sub(rf"^{key} = .*$", f'{key} = "{value}"', toml, count=1, flags=re.M)
    (root / "series.toml").write_text(toml, encoding="utf-8")
    return root


def test_discovery():
    names = [s["name"] for s in api.stages()]
    assert names[:3] == ["research", "outline", "script"] and names[-1] == "qc"
    assert {s["name"] for s in api.stages() if not s["paid"]} == {"compose", "publish", "qc"}
    assert "scenes" in api.script_schema()["properties"]
    assert "econ-history" in api.templates()


def test_agent_workflow_without_spending(tmp_path: Path):
    series = _series(tmp_path)
    ep = api.new_episode(series, "管仲与盐铁专营", slug="salt")["episode"]

    first = api.run(ep, provider="mock", to_stage="art")
    assert first["status"] == "paused" and "approve" in first["message"]       # the review gate
    assert [s["status"] for s in first["stages"]][-1] == "paused"

    # Another agent fact-checks the script and writes it back: approval is reset, the old revision is kept.
    script = api.get_script(ep)
    script["scenes"][0]["lines"][0]["text"]["en"] = "A fact-checked opening line."
    saved = api.put_script(ep, script)
    assert saved == {"ok": True, "changed": True, "revision": 2, "approved": False}
    assert (Path(ep) / "revisions" / "script.r1.json").exists()
    bad = dict(script, scenes=[{**script["scenes"][0], "lines": [{"speaker": "nobody", "text": {"zh": "x", "en": "x"}}]}])
    assert api.put_script(ep, bad)["ok"] is False

    api.put_script(ep, script)
    assert api.approve(ep)["ok"] is True

    plan = api.plan(ep, provider="mock", to_stage="voice")
    by_name = {s["name"]: s for s in plan["stages"]}
    assert plan["status"] == "planned" and "review_gate" not in plan
    assert by_name["art"]["counts"]["planned"] > 0 and by_name["voice"]["counts"]["planned"] > 0
    assert not list(Path(ep).glob("assets/*/image.png"))                         # a plan writes nothing

    refused = api.run(ep, provider="mock", to_stage="voice", max_paid_calls=0)
    assert refused["status"] == "refused" and refused["paid_calls"] == plan["paid_calls"]

    done = api.run(ep, provider="mock", from_stage="cast", to_stage="voice", max_paid_calls=10_000)
    assert done["status"] == "done" and done["generated"] == plan["paid_calls"]
    assert api.plan(ep, provider="mock", to_stage="voice")["paid_calls"] == 0      # all up to date now

    info = api.status(series)
    assert info["episodes"][0]["approved"] and info["episodes"][0]["art_done"] == info["episodes"][0]["illustrations"]


def test_adopt_keeps_assets_made_elsewhere(tmp_path: Path):
    series = _series(tmp_path)
    ep = api.new_episode(series, "管仲", slug="g")["episode"]
    api.run(ep, provider="mock", to_stage="script")
    script = api.get_script(ep)
    first = next(s["id"] for s in script["scenes"] if s["kind"] == "illustration")
    hand_made = Path(ep) / "assets" / first / "image.png"
    hand_made.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 36), "purple").save(hand_made)

    before = api.plan(ep, provider="mock", stages=["art"], skip_review=True)["stages"][0]["counts"]
    report = api.adopt(ep, stages=["art"], provider="mock")
    assert report["stages"]["art"]["adopted"] == 1 and report["stages"]["art"]["planned"] == before["planned"] - 1
    api.run(ep, provider="mock", stages=["art"], skip_review=True)
    assert Image.open(hand_made).getpixel((0, 0)) == (128, 0, 128)          # kept, not redrawn


def test_plugin_provider(tmp_path: Path):
    series = _series(tmp_path, image="test_api:FakeImage")
    ep = api.new_episode(series, "盐铁论", slug="debate")["episode"]
    api.run(ep, provider="mock", to_stage="script")
    # --provider mock would override the image job too, so set the others to mock in the config instead
    toml = (series / "series.toml").read_text(encoding="utf-8")
    for job in ("research", "llm", "tts"):
        toml = re.sub(rf"^{job} = .*$", f'{job} = "mock"', toml, count=1, flags=re.M)
    (series / "series.toml").write_text(toml, encoding="utf-8")
    result = api.run(ep, stages=["art"], skip_review=True)
    assert result["status"] == "done", result
    image = next(Path(ep).glob("assets/*/image.png"))
    assert Image.open(image).getpixel((0, 0)) == (0, 128, 128)


def test_bad_plugin_is_reported(tmp_path: Path):
    series = _series(tmp_path, image="no_such_module:Thing")
    ep = api.new_episode(series, "x", slug="x")["episode"]
    api.run(ep, provider="mock", to_stage="script")
    toml = re.sub(r'^llm = .*$', 'llm = "mock"', (series / "series.toml").read_text(), count=1, flags=re.M)
    (series / "series.toml").write_text(toml)
    result = api.run(ep, stages=["art"], skip_review=True)
    assert result["status"] == "failed" and "no_such_module" in result["message"]


def test_cli_json(tmp_path: Path, capsys):
    series = _series(tmp_path)
    main(["--json", "new", str(series), "brief", "--slug", "b"])
    ep = json.loads(capsys.readouterr().out)["episode"]
    main(["--json", "plan", ep, "--provider", "mock", "--to", "script"])
    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "planned" and plan["paid_calls"] == 3
    with pytest.raises(SystemExit):
        main(["--json", "check", ep])                                            # no script yet → error JSON
    assert "error" in json.loads(capsys.readouterr().out)


def test_mcp_protocol(tmp_path: Path):
    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t"}}})
    assert init["result"]["protocolVersion"] == "2025-03-26" and "tools" in init["result"]["capabilities"]
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = {t["name"]: t for t in handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
    assert {"plan", "run", "get_script", "put_script", "approve", "list_outputs"} <= set(tools)
    assert tools["run"]["inputSchema"]["required"] == ["episode_dir"]

    series = _series(tmp_path)
    call = lambda name, **a: handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",  # noqa: E731
                                     "params": {"name": name, "arguments": a}})["result"]
    ep = call("new_episode", series_dir=str(series), brief="管仲", slug="g")["structuredContent"]["episode"]
    refused = call("run", episode_dir=ep, to_stage="script", provider="mock")
    assert refused["isError"] and refused["structuredContent"]["status"] == "refused"   # paid calls need allow_paid
    ok = call("run", episode_dir=ep, to_stage="script", provider="mock", allow_paid=True)
    assert not ok["isError"] and ok["structuredContent"]["status"] == "done"
    assert call("check", episode_dir=ep)["structuredContent"]["scenes"] > 0
    assert call("check", episode_dir=ep, bogus=1)["isError"]
    unknown = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope"}})
    assert unknown["error"]["code"] == -32602
    assert handle({"jsonrpc": "2.0", "id": 4, "method": "resources/list"})["error"]["code"] == -32601


def test_load_env(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nAVP_T_A=one   # trailing comment\nexport AVP_T_B='two # kept'\nAVP_T_C=\nAVP_T_D=new\n")
    monkeypatch.setenv("AVP_T_D", "already set")
    for key in ("AVP_T_A", "AVP_T_B", "AVP_T_C"):
        monkeypatch.delenv(key, raising=False)
    assert api.load_env(env) == ["AVP_T_A", "AVP_T_B"]
    import os
    assert os.environ["AVP_T_A"] == "one" and os.environ["AVP_T_B"] == "two # kept"
    assert "AVP_T_C" not in os.environ and os.environ["AVP_T_D"] == "already set"
    for key in ("AVP_T_A", "AVP_T_B"):
        monkeypatch.delenv(key)


def test_schema_file_is_current():
    schema = json.loads((Path(__file__).parents[1] / "schemas" / "script.schema.json").read_text(encoding="utf-8"))
    assert {k: v for k, v in schema.items() if k not in ("$schema", "title")} == api.script_schema(), \
        "regenerate schemas/script.schema.json (see README)"


@needs_ffmpeg
def test_doctor_reports_checks():
    report = api.doctor()
    assert {c["check"] for c in report["checks"]} >= {"ffmpeg", "ffprobe", "chromium", "cjk font"}
