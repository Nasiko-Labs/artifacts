"""Pure: the competency list, which items are usable, the coverage table and the evidence gaps.

A competency with fewer than 2 usable items, or none from someone other than the person, is a gap. No
statement is written for a gap, and a weakness is never inferred from missing evidence."""

from __future__ import annotations

import re
from collections.abc import Sequence

from evidence import Evidence

DEFAULT_COMPETENCIES = {
    "delivery": "ships what was committed, scope, timeliness",
    "quality": "correctness, reliability, craft",
    "collaboration": "works with others, reviews, helping",
    "communication": "written and verbal clarity, stakeholder updates",
    "ownership": "initiative, follows through, drives problems to done",
    "growth": "learning, feedback taken, new skills",
    "leadership": "mentoring, setting direction",
}
LEADERSHIP = "leadership"
MIN_ITEMS = 2
MAX_COMPETENCIES = 12
_SENIOR = re.compile(r"\b(?:senior|sr\.?|staff|principal|lead|manager|head|director|l(?:[5-9]|1[0-2]))\b",
                     re.IGNORECASE)
_SPLIT = re.compile(r"\s*[,;/]\s*|\s+and\s+")
PROMPT = "What is one project where {c} was visible, with the date and outcome?"


def competencies(header: dict) -> dict[str, str]:
    """Defaults, or the names after `Competencies:` in the user's own message (never from policy text)."""
    raw = header.get("competencies") or ""
    names = [" ".join(n.casefold().split()) for n in _SPLIT.split(raw) if n.strip()]
    names = [n.strip(" .") for n in names if n.strip(" .")][:MAX_COMPETENCIES]
    if not names:
        return dict(DEFAULT_COMPETENCIES)
    return {n: DEFAULT_COMPETENCIES.get(n, "as the manager uses the word") for n in dict.fromkeys(names)}


def is_senior(header: dict) -> bool:
    return bool(_SENIOR.search(f"{header.get('level') or ''} {header.get('role') or ''}"))


def required(header: dict) -> list[str]:
    names = list(competencies(header))
    if header.get("competencies"):
        return names
    return [n for n in names if n != LEADERSHIP or is_senior(header)]


def usable(items: Sequence[Evidence]) -> list[Evidence]:
    return [i for i in items if i.specific and i.tagged and not i.excluded]


def table(items: Sequence[Evidence], names: Sequence[str]) -> dict[str, dict]:
    """Per competency: usable count, non-self usable count, ids, and non-specific ids."""
    rows: dict[str, dict] = {n: {"usable": 0, "non_self": 0, "ids": [], "unusable": []} for n in names}
    for item in items:
        if item.excluded:
            continue
        for name in item.competencies:
            row = rows.get(name)
            if row is None:
                continue
            if item.specific and item.tagged:
                row["usable"] += 1
                row["non_self"] += item.source != "self"
                row["ids"].append(item.id)
            else:
                row["unusable"].append(item.id)
    return rows


def gaps(items: Sequence[Evidence], header: dict) -> list[dict]:
    rows = table(items, list(competencies(header)))
    found: list[dict] = []
    for name in required(header):
        row = rows.get(name) or {"usable": 0, "non_self": 0, "unusable": []}
        if row["usable"] >= MIN_ITEMS and row["non_self"] > 0:
            continue
        found.append({"competency": name, "have": row["usable"], "needed": MIN_ITEMS, "non_self": row["non_self"],
                      "suggested_prompt": PROMPT.format(c=name), "unusable": list(row["unusable"])})
    return found


def unusable_items(items: Sequence[Evidence]) -> list[Evidence]:
    """Tagged but too general to use (never cited)."""
    return [i for i in items if i.tagged and not i.specific and not i.excluded]
