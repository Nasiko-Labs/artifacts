"""The report-reading step: one call per report part (map, at most 3 parts of ~12,000 characters, run
concurrently), validated in code. Only the report is sent; the export never reaches the model here."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

import llm
import prompts
from llm import LlmError, TurnDeps

CHUNK_CHARS = 12_000
MAX_CHUNKS = 3
MAX_REPORT_CHARS = 40_000
OK, OFF_TOPIC, FAILED = "ok", "off_topic", "failed"


@dataclass(frozen=True)
class Extracted:
    status: str
    items: list[dict] = field(default_factory=list)
    parts: int = 1
    coverage: str = ""


def chunks(report: str) -> tuple[list[str], str]:
    """(parts, coverage note). Cut on line breaks; over the cap only the first parts are read, and said so."""
    text = report[:MAX_REPORT_CHARS]
    parts: list[str] = []
    while text:
        if len(text) <= CHUNK_CHARS:
            parts.append(text)
            break
        cut = text.rfind("\n", 0, CHUNK_CHARS)
        cut = cut if cut > CHUNK_CHARS // 2 else CHUNK_CHARS
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    kept = parts[:MAX_CHUNKS]
    read = sum(len(p) for p in kept)
    note = ""
    if read < len(report.strip()):
        note = (f"The report is {len(report):,} characters; I read the first {read:,} of them. Paste the rest in a "
                "new message to check it too.")
    return kept, note


async def extract(deps: TurnDeps, report: str, request: str, policy: Sequence[str]) -> Extracted:
    parts, coverage = chunks(report)
    total = len(parts)
    calls = [_one(deps, part, request, policy, (n, total) if total > 1 else None)
             for n, part in enumerate(parts, start=1)]
    try:
        answers = await asyncio.gather(*calls)
    except LlmError:
        return Extracted(FAILED)
    if all(a["status"] == OFF_TOPIC for a in answers):
        return Extracted(OFF_TOPIC)
    items = [item for a in answers for item in a["items"]]
    return Extracted(OK, items, total, coverage)


async def _one(deps: TurnDeps, part: str, request: str, policy: Sequence[str],
               where: tuple[int, int] | None) -> dict:
    messages = prompts.extract_messages(part, request, policy, where)
    return await llm.complete_json(deps, messages, prompts.EXTRACT_CONTRACT, validate)


def validate(data: object) -> dict | None:
    if not isinstance(data, dict):
        return None
    if data.get("status") == OFF_TOPIC:
        return {"status": OFF_TOPIC, "items": []}
    raw = data.get("items")
    if not isinstance(raw, list):
        return None
    return {"status": OK, "items": [i for i in raw if isinstance(i, dict)]}
