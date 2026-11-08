"""The map step for long notes: one call per chunk (at most 3) extracting points with verbatim fragments,
merged in code into one fenced POINTS block for the outline call."""

from __future__ import annotations

import json

import prompts
from deps import TurnDeps
from fence import fence
from llm import Budget, ask_json

MAX_POINTS = 60


async def map_points(deps: TurnDeps, budget: Budget, chunks: tuple[str, ...]) -> str:
    merged: list[dict] = []
    for n, part in enumerate(chunks, start=1):
        messages = ({"role": "system", "content": prompts.MAP_SYSTEM},
                    {"role": "user", "content": f"Part {n} of {len(chunks)}:\n{fence('notes', part)}"})
        data = await ask_json(deps, budget, messages) or {}
        points = data.get("points") if isinstance(data.get("points"), list) else []
        merged.extend(p for p in points if isinstance(p, dict))
    lines = []
    for point in merged[:MAX_POINTS]:
        facts = [str(f) for f in point.get("facts") or [] if str(f).strip()]
        lines.append(f"- {point.get('heading', '')}: " + " | ".join(facts))
    return "\n".join(lines) or json.dumps(merged[:MAX_POINTS])
