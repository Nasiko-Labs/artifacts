"""The extract step: one call over notes + updates (single pass up to 24,000 chars), or one call per chunk
(map, up to 3 chunks per part, run concurrently) with the same schema merged in code."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import prompts
from chunking import SINGLE_PASS_CHARS, chunk
from llm import LlmError, TurnDeps, complete_json

log = logging.getLogger("one-on-one-prep.extract")

LIST_FIELDS = ("commitments", "topics", "concerns_raised", "goals", "accomplishments", "blockers", "events")
NOTES_KINDS = ("commitments", "topics", "concerns_raised", "goals")
OFF_TOPIC = "off_topic"


@dataclass
class Raw:
    """What the model returned, per kind, with the part each item must be quoted from."""

    items: dict[str, list[tuple[str, dict]]] = field(default_factory=lambda: {k: [] for k in LIST_FIELDS})
    skipped_chars: int = 0
    chunked: bool = False
    off_topic: bool = False

    def add(self, part: str, data: dict) -> None:
        for kind in LIST_FIELDS:
            values = data.get(kind)
            if not isinstance(values, list):
                continue
            if part != "PASTED" and (kind in NOTES_KINDS) != (part == "NOTES"):
                continue  # a notes item from the updates block (or the reverse) is never accepted
            self.items[kind] += [(part, v) for v in values if isinstance(v, dict)]


def _validate(data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None
    if data.get("status") == OFF_TOPIC:
        return {"status": OFF_TOPIC}
    if not any(isinstance(data.get(k), list) for k in LIST_FIELDS):
        return None
    return data


async def _call(deps: TurnDeps, blocks: Sequence[tuple[str, str]], policy: Sequence[str]) -> dict:
    messages = prompts.extract_messages(blocks, policy)
    contract = prompts.extract_contract({name for name, _ in blocks})
    return await complete_json(deps, messages, contract, _validate)


def _blocks(notes: str, updates: str, mixed: bool) -> list[tuple[str, str]]:
    if mixed:
        return [("PASTED", notes)]
    return [(name, text) for name, text in (("NOTES", notes), ("UPDATES", updates)) if text.strip()]


async def run(deps: TurnDeps, notes: str, updates: str, mixed: bool, policy: Sequence[str] = ()) -> Raw:
    """Raises LlmError when nothing could be read."""
    raw = Raw()
    blocks = _blocks(notes, updates, mixed)
    if sum(len(text) for _, text in blocks) <= SINGLE_PASS_CHARS:
        data = await _call(deps, blocks, policy)
        raw.off_topic = data.get("status") == OFF_TOPIC
        for name, _ in blocks:
            raw.add(name, data)
        return raw
    raw.chunked = True
    jobs: list[tuple[str, str]] = []
    for name, text in blocks:
        parts = chunk(text)
        raw.skipped_chars += parts.skipped_chars
        jobs += [(name, piece) for piece in parts.parts]
    results = await asyncio.gather(*(_call(deps, [job], policy) for job in jobs), return_exceptions=True)
    read = 0
    for (name, piece), result in zip(jobs, results, strict=True):
        if isinstance(result, LlmError):
            log.warning("extract chunk failed: %s", result)
            raw.skipped_chars += len(piece)
            continue
        if isinstance(result, BaseException):
            raise result
        read += 1
        raw.add(name, result)
    if not read:
        raise LlmError("no chunk could be read")
    return raw
