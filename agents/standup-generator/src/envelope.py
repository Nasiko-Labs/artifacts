"""Pure: the output envelope every catalog agent returns, with `confidence` and `needs_human` computed
in code from coverage and checks (never taken from a model).

    {summary, items[], confidence, needs_human, standup_markdown, people[]}

`items` and `people` are the same per-person list. "as json" / "json only" in the request makes the
text reply the envelope as a fenced json block (a workflow step reads only text).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from render import View, counts, visible
from standup import PersonStandup

_JSON_ASK = re.compile(r"\b(?:as|in|json)\s+(?:json|only)\b|\bjson\s+only\b", re.IGNORECASE)


@dataclass(frozen=True)
class Quality:
    """What the turn could read: drives confidence and needs_human."""

    sources_ok: int  # 0, 1 or 2 sources read in full
    failed: bool  # a source could not be read
    gaps: bool  # unmatched identities, a record cap or a step limit
    pasted: bool = False


def wants_json(text: str) -> bool:
    return bool(_JSON_ASK.search(text or ""))


def confidence(quality: Quality) -> str:
    if quality.pasted:
        return "low"
    if quality.sources_ok == 2 and not quality.failed and not quality.gaps:
        return "high"
    return "medium" if quality.sources_ok else "low"


def build(people: Sequence[PersonStandup], markdown: str, view: View, quality: Quality, window_text: str) -> dict:
    shown = visible(people, view)
    shipped, progress, blocked = counts(people, view)
    rows = [_person(p) for p in shown]
    summary = (f"Standup for {len(shown)} {'person' if len(shown) == 1 else 'people'} {window_text}: "
               f"{shipped} shipped, {progress} in progress, {blocked} blocked.")
    return {"summary": summary, "items": rows, "confidence": confidence(quality),
            "needs_human": bool(blocked) or quality.failed, "standup_markdown": markdown, "people": rows}


def as_json_text(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, indent=2, ensure_ascii=False) + "\n```"


def _person(person: PersonStandup) -> dict:
    return {"name": person.name, "shipped": [line.to_dict() for line in person.shipped],
            "in_progress": [line.to_dict() for line in person.in_progress],
            "blocked": [line.to_dict() for line in person.blocked],
            "reviews": [line.to_dict() for line in person.reviews]}
