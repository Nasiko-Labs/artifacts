"""Map step for a long spec: one call per chunk (at most 4, concurrent) lists its requirements; code keeps a
requirement only when its verbatim fragment is really in that chunk, then merges and dedupes by text."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import prompts
from grounding import is_verbatim, norm
from llm import LlmError, TurnDeps, ask_json

MAX_REQUIREMENTS = 80
MAP_MAX_TOKENS = 2_500


def _valid(data: Any) -> dict | None:
    if isinstance(data, dict) and isinstance(data.get("requirements", []), list):
        return data
    return None


async def _one(deps: TurnDeps, chunk: str, part: int, total: int) -> list[str]:
    try:
        data = await ask_json(deps, prompts.map_messages(chunk, part, total), _valid, MAP_MAX_TOKENS)
    except LlmError:
        return []
    kept = []
    for item in (data or {}).get("requirements") or []:
        if not isinstance(item, dict):
            continue
        text, verbatim = str(item.get("text") or "").strip(), str(item.get("verbatim") or "").strip()
        if text and is_verbatim(verbatim, chunk):
            kept.append(text)
    return kept


async def requirements(deps: TurnDeps, chunks: Sequence[str]) -> tuple[list[str], int]:
    """(merged requirements, how many chunks answered)."""
    results = await asyncio.gather(*(_one(deps, c, i + 1, len(chunks)) for i, c in enumerate(chunks)))
    merged = list({norm(r): r for part in results for r in part}.values())
    return merged[:MAX_REQUIREMENTS], sum(1 for r in results if r)
