"""Pure: the model's PRD JSON into the PRD model. Only shape is handled here (types, caps, known fact ids);
the content rules live in checks.py. Fields the model may send but code owns (proposed, confidence) are
ignored."""

from __future__ import annotations

from collections.abc import Iterable

import prompts
from prd import (
    BASELINE_PH,
    LIMITS,
    PRIORITIES,
    STATEMENT_SECTIONS,
    TARGET_PH,
    WINDOW_PH,
    Metric,
    Prd,
    Question,
    Requirement,
    Statement,
)

MAX_TEXT_CHARS = 400
_PRD_KEYS = frozenset({"problem", "users", "goals", "non_goals", "requirements", "metrics", "open_questions"})


def validate(data: object) -> dict | None:
    """Accepts an off-topic answer or anything shaped like a PRD."""
    if not isinstance(data, dict):
        return None
    if data.get("status") == "off_topic":
        return {"status": "off_topic"}
    if not (_PRD_KEYS & data.keys()):
        return None
    return {**data, "status": "ok"}


def _clean(value: object, limit: int = MAX_TEXT_CHARS) -> str:
    text = value if isinstance(value, str) else ""
    return " ".join(prompts.strip_markers(text).split())[:limit]


def _basis(value: object, known: frozenset[str]) -> tuple[str, ...]:
    ids = value if isinstance(value, list) else []
    seen: list[str] = []
    for item in ids:
        fid = str(item).strip()
        if fid in known and fid not in seen:
            seen.append(fid)
    return tuple(seen)


def _items(data: dict, name: str) -> Iterable[dict]:
    raw = data.get(name)
    entries = raw if isinstance(raw, list) else []
    return [e if isinstance(e, dict) else {"text": e} for e in entries if isinstance(e, (dict, str))]


def statements(data: dict, name: str, known: frozenset[str]) -> tuple[Statement, ...]:
    out = [Statement(_clean(e.get("text")), _basis(e.get("basis"), known)) for e in _items(data, name)]
    return tuple(s for s in out if s.text)[:LIMITS[name]]


def requirements(data: dict, known: frozenset[str]) -> tuple[Requirement, ...]:
    out = []
    for e in _items(data, "requirements"):
        text = _clean(e.get("text"))
        if not text:
            continue
        priority = str(e.get("priority") or "").strip().casefold()
        out.append(Requirement(id=_clean(e.get("id"), 8), text=text,
                               priority=priority if priority in PRIORITIES else "",
                               basis=_basis(e.get("basis"), known),
                               acceptance_hint=_clean(e.get("acceptance_hint"), 240)))
    return tuple(out)[:LIMITS["requirements"]]


def metrics(data: dict, known: frozenset[str]) -> tuple[Metric, ...]:
    out = []
    for e in _items(data, "metrics"):
        name = _clean(e.get("name"), 120)
        if name:
            out.append(Metric(name=name, definition=_clean(e.get("definition"), 240),
                              baseline=_clean(e.get("baseline"), 80) or BASELINE_PH,
                              target=_clean(e.get("target"), 80) or TARGET_PH,
                              window=_clean(e.get("window"), 80) or WINDOW_PH,
                              basis=_basis(e.get("basis"), known)))
    return tuple(out)[:LIMITS["metrics"]]


def questions(data: dict) -> tuple[Question, ...]:
    raw = data.get("open_questions")
    entries = raw if isinstance(raw, list) else []
    texts = [_clean(e.get("question") if isinstance(e, dict) else e, 300) for e in entries]
    return tuple(Question(t, "general") for t in texts if t)[:LIMITS["open_questions"]]


def to_prd(data: dict, known: frozenset[str], title: str = "Untitled") -> Prd:
    sections = {name: statements(data, name, known) for name in STATEMENT_SECTIONS}
    return Prd(title=title, requirements=requirements(data, known), metrics=metrics(data, known),
               open_questions=questions(data), **sections)
