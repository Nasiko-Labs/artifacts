"""The output contract: one Doc per converted file, one Failure per file that was not, and the envelope
(computed in code, never by a model)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SourceName = Literal["file", "drive", "dropbox", "pasted"]
Confidence = Literal["high", "medium", "low"]


class Doc(BaseModel):
    name: str
    source: SourceName
    format: str
    bytes: int
    chars: int
    units: dict[str, int] = Field(default_factory=dict)  # {"pages": 12} | {"sheets": 3} | {"slides": 20}
    markdown: str
    warnings: list[str] = Field(default_factory=list)
    scanned: bool = False


class Failure(BaseModel):
    name: str
    reason: str


class Envelope(BaseModel):
    summary: str
    items: list[Doc]
    confidence: Confidence
    needs_human: bool
    converted_count: int
    failed: list[Failure]


def summary_line(docs: list[Doc], failed: list[Failure]) -> str:
    total = len(docs) + len(failed)
    chars = sum(d.chars for d in docs)
    noun = "file" if total == 1 else "files"
    return f"Converted {len(docs)} of {total} {noun} ({chars:,} characters)."


def confidence_of(docs: list[Doc], failed: list[Failure]) -> Confidence:
    if not docs:
        return "low"
    if failed or any(d.warnings for d in docs):
        return "medium"
    return "high"


def envelope(docs: list[Doc], failed: list[Failure], ambiguous: bool = False) -> Envelope:
    needs_human = bool(failed) or ambiguous or any(d.scanned for d in docs)
    return Envelope(summary=summary_line(docs, failed), items=docs, confidence=confidence_of(docs, failed),
                    needs_human=needs_human, converted_count=len(docs), failed=failed)
