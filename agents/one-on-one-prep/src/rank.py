"""Pure: talking points and recognition, ordered in code.

Talking points (<= 7): the manager's own topics (verbatim, first); open carry-overs (no evidence before in
progress, the report's before the manager's own, an unfulfilled manager commitment flagged "your commitment");
blockers; concerns not already carried over; goals (movement when an update shares a content word).
Recognize (<= 5): accomplishments, newest first; attendance-only items excluded."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

import dates
from grounding import normalise

MAX_POINTS = 7
MAX_RECOGNIZE = 5
OPEN = ("no_evidence", "unassessed", "in_progress")
_ACTIVITY = re.compile(r"^(?:attended|joined|participated in|sat in on|went to|was in|took part in)\b|"
                       r"^(?:meetings?|syncs?|standups?|1:1s?)\b", re.IGNORECASE)
_STOP = frozenset(["the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with", "from", "about", "into", "our", "your", "their", "this", "that", "will", "would", "more", "less", "grow", "growing", "into", "work", "working", "team", "doc", "docs"])
_WORD = re.compile(r"[a-z][a-z-]{3,}")


def _owner_rank(who: str) -> int:
    return {"report": 0, "manager": 1}.get(who, 2) if who in ("report", "manager") else 2


def due_note(due: str, today: date, anchor: date | None) -> str:
    parsed = dates.parse(due, today, anchor) if due else None
    days = dates.overdue_days(parsed, today)
    return f"overdue by {days} day{'s' if days != 1 else ''}" if days else ""


def carry_over_point(c: dict, person: str, today: date, anchor: date | None) -> dict:
    who = "you" if c["who"] == "manager" else (person or "they") if c["who"] == "report" else c["who"]
    since = {"no_evidence": "nothing in the updates", "unassessed": "not checked (ran out of time)",
             "in_progress": f'in progress: "{c.get("evidence", "")}"'}[c["status"]]
    text = f"Last time: {c['text']} ({who}). Since then: {since}"
    point = {"id": c["id"], "text": text, "source": c["source"], "quote": c["quote"], "status": c["status"]}
    if c.get("due"):
        point["due"] = c["due"]
        overdue = due_note(c["due"], today, anchor)
        if overdue and c["status"] != "done":
            point["overdue"] = overdue
    if c["who"] == "manager" and c["status"] == "no_evidence":
        point["flag"] = "your commitment"
    return point


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD.findall(normalise(text)) if w not in _STOP}


def goal_point(goal: dict, updates: Sequence[dict]) -> dict:
    words = _content_words(goal["text"])
    match = next((u for u in updates if words & _content_words(u["text"] + " " + u["quote"])), None)
    if match:
        return {"id": goal["id"], "text": f"Movement on {goal['text']}: \"{match['quote']}\"", "source": goal["source"],
                "quote": goal["quote"], "evidence": match["quote"]}
    return {"id": goal["id"], "text": f"Check in on {goal['text']}", "source": goal["source"], "quote": goal["quote"]}


def talking_points(topics: Sequence[str], commitments: Sequence[dict], blockers: Sequence[dict],
                   concerns: Sequence[dict], goals: Sequence[dict], updates: Sequence[dict], person: str,
                   today: date, anchor: date | None) -> list[dict]:
    points = [{"id": f"m{n}", "text": t, "source": "manager", "quote": t} for n, t in enumerate(topics, start=1)]
    open_loops = sorted((c for c in commitments if c["status"] in OPEN),
                        key=lambda c: (c["status"] == "in_progress", _owner_rank(c["who"])))
    points += [carry_over_point(c, person, today, anchor) for c in open_loops]
    points += [{"id": b["id"], "text": b["text"], "source": b["source"], "quote": b["quote"]} for b in blockers]
    carried = {normalise(c["quote"]) for c in commitments}
    points += [{"id": k["id"], "text": k["text"], "source": k["source"], "quote": k["quote"]} for k in concerns
               if normalise(k["quote"]) not in carried]
    points += [goal_point(g, updates) for g in goals]
    return points[:max(MAX_POINTS, len(topics))]


def faithful(text: str, quote: str) -> str:
    """`text` when every content word in it is in the quote (no added adjectives or impact claims), else the
    quote."""
    words = set(_WORD.findall(normalise(text)))
    return text if all(w in normalise(quote) for w in words) else quote


def recognize(accomplishments: Sequence[dict], today: date, anchor: date | None) -> list[dict]:
    kept = [{**a, "text": faithful(a["text"], a["quote"])} for a in accomplishments
            if not _ACTIVITY.search(a["text"])]
    def when(a: dict) -> date:
        return dates.parse(a.get("date") or "", today, anchor) or date.min
    ordered = sorted(kept, key=when, reverse=True)
    return [{"id": a["id"], "text": a["text"] + (f" ({a['date']})" if a.get("date") else ""), "source": a["source"],
             "quote": a["quote"], **({"date": a["date"]} if a.get("date") else {})} for a in ordered[:MAX_RECOGNIZE]]
