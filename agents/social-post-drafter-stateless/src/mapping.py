"""Long material: split it in code, pull verbatim facts from each part (one call per part, at most three),
merge them in code. The drafting call then receives facts, not the raw article. Nothing is cut silently:
the coverage line says what was read."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import prompts
from inputs import MAX_SOURCE_CHARS
from llm import Budget, TurnDeps, ask_json

MAX_CHUNKS = 3
MAX_CHUNK_CHARS = 12_000
MAX_FACTS = 30
MAX_FACT_CHARS = 200


@dataclass(frozen=True)
class Mapped:
    facts: tuple[str, ...]
    themes: tuple[str, ...]
    coverage: str


def chunks(text: str) -> list[str]:
    return split(text)[0]


def split(text: str) -> tuple[list[str], int]:
    """Up to three parts of near-equal size, cut at a paragraph or line break where one is close, and how
    many characters they cover (the last part may run long, up to MAX_CHUNK_CHARS)."""
    count = min(MAX_CHUNKS, max(1, math.ceil(len(text) / MAX_SOURCE_CHARS)))
    size = min(MAX_CHUNK_CHARS, math.ceil(len(text) / count))
    parts, start = [], 0
    while start < len(text) and len(parts) < count:
        last = len(parts) == count - 1
        end = min(len(text), start + (MAX_CHUNK_CHARS if last else size))
        if end < len(text) and not last:
            cut = max(text.rfind("\n", start + size // 2, end), text.rfind(". ", start + size // 2, end) + 1)
            end = cut if cut > start else end
        parts.append(text[start:end].strip())
        start = end
    return [p for p in parts if p], start


def covered(text: str, parts: list[str], consumed: int) -> str:
    if consumed >= len(text):
        return f"Drafted from the whole article ({len(parts)} parts)."
    return (f"Drafted from the first {consumed:,} of {len(text):,} characters ({len(parts)} parts); paste the rest "
            "separately for posts about it.")


async def map_material(deps: TurnDeps, budget: Budget, text: str) -> Mapped:
    parts, consumed = split(text)
    facts: list[str] = []
    themes: list[str] = []
    for index, part in enumerate(parts, start=1):
        messages = ({"role": "system", "content": prompts.MAP_SYSTEM},
                    {"role": "user", "content": prompts.map_user(part, index, len(parts))})
        data = await ask_json(deps, budget, messages)
        if not isinstance(data, dict):
            continue
        facts += verbatim(data.get("facts"), part)
        if isinstance(data.get("theme"), str) and data["theme"].strip():
            themes.append(data["theme"].strip()[:MAX_FACT_CHARS])
    merged = tuple(dict.fromkeys(facts))[:MAX_FACTS]
    return Mapped(facts=merged, themes=tuple(themes), coverage=covered(text, parts, consumed))


def verbatim(value, part: str) -> list[str]:
    """Only fragments really in the part (whitespace aside) are kept."""
    if not isinstance(value, list):
        return []
    flat = re.sub(r"\s+", " ", part).casefold()
    kept = []
    for item in value:
        if (isinstance(item, str) and 0 < len(item.strip()) <= MAX_FACT_CHARS
                and re.sub(r"\s+", " ", item.strip()).casefold() in flat):
            kept.append(item.strip())
    return kept
