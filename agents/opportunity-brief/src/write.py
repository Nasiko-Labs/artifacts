"""Writing: one LLM call fills each theme's fields (why now, opportunity, risks, next step, cited quotes)
from that theme's fenced items and the code-computed FACTS. Drill-down is the same call over one theme."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

import metrics
import prompts
from cluster import Cluster
from deps import TurnDeps
from llm import call_json
from store import Item

WRITE_TEXT_CHARS = 600
DRILL_TEXT_CHARS = 1_200
MAX_DRILL_ITEMS = 15
ANGLES = {
    "build": "what to build (product opportunities)",
    "invest-time": "what to learn or adopt (where to invest time)",
    "watch": "what to watch (threats to an existing product)",
}


def theme_block(n: int, theme: Cluster, items: Sequence[Item], now: datetime, chars: int) -> str:
    facts = "\n".join(f"- {line}" for line in metrics.facts(items, now))
    evidence = "\n".join(prompts.fence(i.id, f"metrics: {metrics.metric(i, now)}\n{i.text[:chars]}")
                         for i in items)
    return f"THEME {n}: {theme.label} ({theme.kind})\nFACTS (exact, computed; never compute):\n{facts}\n{evidence}"


def write_user(topic: str, angle: str, window_days: int, themes: Sequence[tuple[int, Cluster, Sequence[Item]]],
               now: datetime, chars: int = WRITE_TEXT_CHARS, instruction: str = "") -> str:
    head = [f"TOPIC: {prompts.strip_markers(topic)}", f"ANGLE: {ANGLES.get(angle, ANGLES['build'])}",
            f"WINDOW: last {window_days} days"]
    if instruction:
        head.append(f"USER FOCUS: {prompts.strip_markers(instruction)[:300]}")
    blocks = [theme_block(n, t, items, now, chars) for n, t, items in themes]
    return "\n".join(head) + "\n\n" + "\n\n".join(blocks)


async def write(deps: TurnDeps, topic: str, angle: str, window_days: int,
                themes: Sequence[tuple[int, Cluster, Sequence[Item]]], instruction: str = "") -> dict:
    return await call_json(deps, prompts.WRITE_SYSTEM,
                           write_user(topic, angle, window_days, themes, deps.now, instruction=instruction))


async def drill(deps: TurnDeps, topic: str, angle: str, window_days: int, n: int, theme: Cluster,
                items: Sequence[Item]) -> dict:
    user = write_user(topic, angle, window_days, [(n, theme, items[:MAX_DRILL_ITEMS])], deps.now, DRILL_TEXT_CHARS)
    return await call_json(deps, prompts.DRILL_SYSTEM, user)


def drafts_by_index(answer: Mapping) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for raw in answer.get("themes") if isinstance(answer.get("themes"), list) else []:
        if not isinstance(raw, Mapping):
            continue
        try:
            n = int(raw.get("index"))
        except (TypeError, ValueError):
            continue
        out.setdefault(n, dict(raw))
    return out
