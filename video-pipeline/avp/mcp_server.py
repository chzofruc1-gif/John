"""MCP server on stdio — exposes avp.api to any MCP client (Claude Code, Claude Desktop, Cursor, Cline …).

No third-party dependency: JSON-RPC 2.0, one message per line, as the MCP stdio transport specifies.
Register it in a client, e.g. Claude Code:  claude mcp add avp -- avp mcp
Logs go to stderr; stdout carries only protocol messages.

Paid generation is guarded: `run` refuses to start when its plan needs paid model calls unless the caller
passes allow_paid=true (and optionally max_paid_calls) — so an agent cannot spend money by accident.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import sys
import traceback
from typing import Any, Callable

from . import __version__, api

log = logging.getLogger("avp")
PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]

EP = {"type": "string", "description": "episode folder, e.g. channels/econ/episodes/01-guanzhong"}
STAGE_LIST = {"type": "array", "items": {"type": "string", "enum": list(api.STAGE_INFO)}}
RUN_PROPS = {
    "episode_dir": EP,
    "stages": {**STAGE_LIST, "description": "run exactly these stages (default: all, or from/to)"},
    "from_stage": {"type": "string", "enum": list(api.STAGE_INFO)},
    "to_stage": {"type": "string", "enum": list(api.STAGE_INFO)},
    "force": {"type": "boolean", "description": "regenerate even if up to date (with scenes: only those)"},
    "scenes": {"type": "array", "items": {"type": "string"}, "description": "limit to these scene ids"},
    "outputs": {"type": "array", "items": {"type": "string"}, "description": "limit rendering to these output ids"},
    "skip_review": {"type": "boolean", "description": "produce a draft although the script is not approved"},
    "draft": {"type": "boolean", "description": "540p quick preview"},
    "provider": {"type": "string", "enum": ["anthropic", "gemini", "openai", "mock"],
                 "description": "override providers (mock = offline, free)"},
}


def _run_tool(episode_dir: str, allow_paid: bool = False, max_paid_calls: int | None = None, **options) -> dict:
    if not allow_paid:
        max_paid_calls = 0
    result = api.run(episode_dir, max_paid_calls=max_paid_calls, **options)
    if result["status"] == "refused" and not allow_paid:
        result["message"] += ". Show the plan to the user and call again with allow_paid=true once they agree."
    return result


TOOLS: dict[str, tuple[str, dict, Callable[..., Any]]] = {
    "stages": ("List pipeline stages in order and whether each calls paid models.", {}, api.stages),
    "status": ("Progress of a series (all episodes) or of one episode.",
               {"path": {"type": "string", "description": "series folder or episode folder"}}, api.status),
    "init_series": ("Create a new series folder from a template (series.toml is the series bible).",
                    {"series_dir": {"type": "string"}, "template": {"type": "string", "default": "econ-history"}},
                    api.init_series),
    "new_episode": ("Start an episode from a brief (topic, angle, must-cover points).",
                    {"series_dir": {"type": "string"}, "brief": {"type": "string"}, "slug": {"type": "string"},
                     "number": {"type": "integer"}}, api.new_episode),
    "plan": ("Dry run: what a run with these options would generate, and how many paid model calls it needs. "
             "Calls nothing, writes nothing. Use before run.", RUN_PROPS, api.plan),
    "run": ("Run pipeline stages. Stops at the review gate (status 'paused') until the script is approved. "
            "Refuses paid generation unless allow_paid=true; set max_paid_calls to cap it.",
            {**RUN_PROPS, "allow_paid": {"type": "boolean"}, "max_paid_calls": {"type": "integer"}}, _run_tool),
    "get_script": ("Read script.json, the bilingual master script (see script_schema).", {"episode_dir": EP}, api.get_script),
    "put_script": ("Replace script.json (validated; a changed script loses its approval). The main hand-off for "
                   "agents that fact-check or rewrite the script.",
                   {"episode_dir": EP, "script": {"type": "object"}, "keep_approval": {"type": "boolean"}}, api.put_script),
    "script_schema": ("JSON Schema of script.json.", {}, api.script_schema),
    "check": ("Validate script.json, regenerate script.md, report length and problems.", {"episode_dir": EP}, api.check),
    "revise": ("Apply review / fact-check notes to the script with the configured LLM (paid).",
               {"episode_dir": EP, "notes": {"type": "string"}, "provider": RUN_PROPS["provider"]}, api.revise),
    "approve": ("Approve the current script revision for production (do this only after a human or fact-check review).",
                {"episode_dir": EP, "force": {"type": "boolean"}}, api.approve),
    "adopt": ("Accept images / clips / voice files that another agent or a person put into assets/<scene>/ as up "
              "to date, so runs keep them. Calls no model.",
              {"episode_dir": EP, "stages": {"type": "array", "items": {"type": "string",
                                              "enum": ["cast", "art", "motion", "voice"]}},
               "scenes": RUN_PROPS["scenes"], "provider": RUN_PROPS["provider"]}, api.adopt),
    "list_outputs": ("Finished videos with subtitles, publish copy, contact sheets and the QC verdict.",
                     {"episode_dir": EP}, api.list_outputs),
    "doctor": ("Check ffmpeg, Chromium, CJK font and the API keys the series' providers need.",
               {"series_dir": {"type": "string"}}, api.doctor),
}
REQUIRED = {"status": ["path"], "init_series": ["series_dir"], "new_episode": ["series_dir", "brief"],
            "plan": ["episode_dir"], "run": ["episode_dir"], "get_script": ["episode_dir"],
            "put_script": ["episode_dir", "script"], "check": ["episode_dir"], "revise": ["episode_dir", "notes"],
            "approve": ["episode_dir"], "list_outputs": ["episode_dir"], "adopt": ["episode_dir"]}


def tool_list() -> list[dict]:
    return [{"name": name, "description": desc,
             "inputSchema": {"type": "object", "properties": props, "required": REQUIRED.get(name, [])}}
            for name, (desc, props, _) in TOOLS.items()]


def call_tool(name: str, arguments: dict) -> dict:
    if name not in TOOLS:
        raise KeyError(name)
    _, props, fn = TOOLS[name]
    unknown = set(arguments) - set(props)
    if unknown:
        return _content({"error": f"unknown argument(s): {', '.join(sorted(unknown))}"}, error=True)
    try:
        with contextlib.redirect_stdout(io.StringIO()) as stray:  # never let library output corrupt the protocol
            result = fn(**arguments)
        if stray.getvalue():
            log.info(stray.getvalue().rstrip())
    except Exception as exc:  # tool errors are results, not protocol errors
        log.debug(traceback.format_exc())
        return _content({"error": str(exc)}, error=True)
    failed = isinstance(result, dict) and (result.get("status") in ("failed", "refused") or result.get("ok") is False)
    return _content(result, error=failed)


def _content(result: Any, error: bool = False) -> dict:
    out: dict[str, Any] = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                           "isError": error}
    if isinstance(result, dict):
        out["structuredContent"] = result
    return out


def handle(message: dict) -> dict | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    method, msg_id, params = message.get("method"), message.get("id"), message.get("params") or {}
    if msg_id is None:  # notification (e.g. notifications/initialized)
        return None
    try:
        if method == "initialize":
            requested = params.get("protocolVersion")
            result = {"protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "avp", "version": __version__},
                      "instructions": "Video pipeline. Typical flow: new_episode → run(to_stage='script') → "
                                      "get_script / put_script / check → approve (after review) → plan → "
                                      "run(allow_paid=true) → list_outputs. Always show plan results to the user "
                                      "before paid runs."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_list()}
        elif method == "tools/call":
            try:
                result = call_tool(params.get("name", ""), params.get("arguments") or {})
            except KeyError:
                return _error(msg_id, -32602, f"unknown tool: {params.get('name')}")
        else:
            return _error(msg_id, -32601, f"method not found: {method}")
    except Exception as exc:  # pragma: no cover - defensive
        return _error(msg_id, -32603, str(exc))
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def serve(stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    log.info("avp MCP server ready (stdio)")
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, f"parse error: {exc}")
        else:
            batch = message if isinstance(message, list) else [message]
            responses = [r for r in (handle(m) for m in batch) if r is not None]
            response = responses if isinstance(message, list) else (responses[0] if responses else None)
        if response:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
