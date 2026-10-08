"""Provider interfaces. Stages only talk to these, so model vendors are swappable."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class Source:
    title: str
    url: str


@dataclass
class ResearchResult:
    markdown: str
    sources: list[Source] = field(default_factory=list)


class LLMProvider(Protocol):
    name: str
    model: str

    def research(self, prompt: str) -> ResearchResult:
        """Answer with web-grounded research notes (markdown) plus the sources consulted."""

    def text(self, task: str, prompt: str) -> str:
        """Free-form generation (outlines, revisions notes)."""

    def json(self, task: str, prompt: str, schema: dict[str, Any], hints: dict[str, Any]) -> dict[str, Any]:
        """Structured generation matching `schema`. `hints` carries plain parameters for offline providers."""


class ImageProvider(Protocol):
    name: str
    model: str

    def generate(self, prompt: str, aspect: str, out_path: Path, references: list[Path]) -> None:
        """Write a PNG. `references` are character sheets the image must stay consistent with."""


class TTSProvider(Protocol):
    name: str
    model: str

    def synthesize(self, text: str, voice: str, style: str, language: str, out_path: Path) -> None:
        """Write speech as a 16-bit mono WAV."""
