"""Claude via the official Anthropic SDK: research with server-side web search, outline and script writing.

Credentials come from the environment the SDK understands (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an
`ant auth login` profile). Requests stream, so long scripts never hit HTTP timeouts.
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from ..config import AnthropicConfig
from .base import ResearchResult, Source

log = logging.getLogger(__name__)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_PAUSE_RESUMES = 5


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Structured outputs need `additionalProperties: false` on every object."""
    schema = copy.deepcopy(schema)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node.setdefault("additionalProperties", False)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return schema


class ClaudeLLM:
    name = "anthropic"

    def __init__(self, cfg: AnthropicConfig, retries: int):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Claude providers need `pip install 'avp[anthropic]'` (anthropic SDK)") from exc
        self.anthropic = anthropic
        self.cfg = cfg
        self.model = cfg.model
        self.client = anthropic.Anthropic(max_retries=max(2, retries))

    def _stream(self, messages: list[dict], what: str, model: str | None = None, **extra: Any):
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": self.cfg.max_tokens,
            "thinking": {"type": "adaptive"},
            "messages": messages,
        }
        output_config = dict(extra.pop("output_config", {}))
        if self.cfg.effort:
            output_config["effort"] = self.cfg.effort
        if output_config:
            kwargs["output_config"] = output_config
        kwargs.update(extra)
        if self.cfg.fallbacks:
            kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
        try:
            with self.client.beta.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        except self.anthropic.APIStatusError as exc:
            raise RuntimeError(f"{what}: Claude API error {exc.status_code}: {exc.message}") from exc
        except self.anthropic.APIConnectionError as exc:
            raise RuntimeError(f"{what}: cannot reach the Claude API: {exc}") from exc
        if message.stop_reason == "refusal":
            details = message.stop_details
            raise RuntimeError(f"{what}: Claude declined ({getattr(details, 'category', None)}): "
                               f"{getattr(details, 'explanation', '')}")
        if message.stop_reason == "max_tokens":
            raise RuntimeError(f"{what}: output hit max_tokens={self.cfg.max_tokens}; raise [anthropic].max_tokens")
        return message

    @staticmethod
    def _text(message) -> str:
        return "".join(b.text for b in message.content if b.type == "text").strip()

    def research(self, prompt: str) -> ResearchResult:
        tool = {"type": "web_search_20260209", "name": "web_search", "max_uses": self.cfg.web_search_max_uses}
        messages: list[dict] = [{"role": "user", "content": prompt}]
        parts: list[str] = []
        sources: dict[str, Source] = {}
        for _ in range(MAX_PAUSE_RESUMES + 1):
            message = self._stream(messages, "research", model=self.cfg.research_model or None, tools=[tool])
            for block in message.content:
                if block.type == "text":
                    parts.append(block.text)
                    for cite in getattr(block, "citations", None) or []:
                        url = getattr(cite, "url", None)
                        if url and url not in sources:
                            sources[url] = Source(getattr(cite, "title", None) or url, url)
                elif block.type == "web_search_tool_result" and isinstance(block.content, list):
                    for result in block.content:
                        if result.url not in sources:
                            sources[result.url] = Source(result.title or result.url, result.url)
            if message.stop_reason != "pause_turn":
                break
            # A long server-side search loop paused; re-send to let it resume where it stopped.
            messages = [messages[0], {"role": "assistant", "content": message.content}]
        text = "".join(parts).strip()
        if not text:
            raise RuntimeError("research: Claude returned no text")
        return ResearchResult(text, list(sources.values()))

    def text(self, task: str, prompt: str) -> str:
        return self._text(self._stream([{"role": "user", "content": prompt}], task))

    def json(self, task: str, prompt: str, schema: dict[str, Any], hints: dict[str, Any]) -> dict[str, Any]:
        message = self._stream(
            [{"role": "user", "content": prompt}], task,
            output_config={"format": {"type": "json_schema", "schema": strict_schema(schema)}},
        )
        return json.loads(self._text(message))
