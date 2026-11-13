"""Pure: the output envelope (every field computed in code) and another agent's envelope as input.

Input: a JSON object with at least one of the string fields `draft`, `alt_shorter`, `text`, `memo_markdown`
(the Reply Drafter / Decision Memo Writer outputs). Those fields are humanized; every other field passes
through untouched, and the fields below are added."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any

from outcome import CLEAN, OK, Outcome

FIELDS = ("draft", "alt_shorter", "text", "memo_markdown")
_FENCED = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
_RANK = {"high": 0, "medium": 1, "low": 2}


def parse(text: str) -> tuple[dict, str] | None:
    """(envelope, the request words around it), or None when the message holds no such envelope."""
    raw = text or ""
    fenced = _FENCED.search(raw)
    start, end = (fenced.start(1), fenced.end(1)) if fenced else (raw.find("{"), raw.rfind("}") + 1)
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end])
    except ValueError:
        return None
    if not isinstance(data, dict) or not fields_of(data):
        return None
    outer_start, outer_end = (fenced.start(), fenced.end()) if fenced else (start, end)
    return data, f"{raw[:outer_start]} {raw[outer_end:]}".strip()


def fields_of(data: Mapping[str, Any]) -> list[str]:
    return [f for f in FIELDS if isinstance(data.get(f), str) and data[f].strip()]


def summary_line(outcome: Outcome) -> str:
    if outcome.status == CLEAN:
        return f"Already natural: AI-pattern density {outcome.before}; nothing changed."
    if outcome.status != OK:
        return f"Unchanged ({outcome.status.replace('_', ' ')})."
    return (f"Humanized: AI-pattern density {outcome.before} -> {outcome.after} per 100 words; "
            f"{len(outcome.changes)} changes.")


def build(outcome: Outcome) -> dict:
    """The envelope for a free-text draft."""
    return {"summary": summary_line(outcome), "items": [dict(c) for c in outcome.changes], "text": outcome.text,
            "ai_pattern_density": {"before": outcome.before, "after": outcome.after},
            "changes_by_category": dict(outcome.removed), "confidence": outcome.confidence,
            "needs_human": outcome.needs_human}


def build_from(source: Mapping[str, Any], outcomes: Mapping[str, Outcome]) -> dict:
    """The input envelope with its humanized fields replaced and this agent's fields added."""
    primary = outcomes[next(iter(outcomes))]
    removed: Counter = Counter()
    items: list[dict] = []
    for name, outcome in outcomes.items():
        removed.update(outcome.removed)
        items += [{"field": name, **c} for c in outcome.changes]
    confidence = max((o.confidence for o in outcomes.values()), key=lambda c: _RANK[c])
    summary = f"{summary_line(primary)} Fields: {', '.join(outcomes)}."
    return {**source, **{name: o.text for name, o in outcomes.items()}, "summary": summary, "items": items,
            "ai_pattern_density": {"before": primary.before, "after": primary.after},
            "changes_by_category": dict(removed), "confidence": confidence, "needs_human": confidence == "low"}


def fenced(envelope: Mapping[str, Any]) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"
