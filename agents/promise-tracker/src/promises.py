"""Pure data: the promise record, reading the model's JSON answers, the search window, state
(de)serialisation. The checks that decide what survives live in checks.py."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Literal

Status = Literal["ok", "no_mail_tool", "off_topic"]
State = Literal["open", "done", "unclear"]
_STATUSES = ("ok", "no_mail_tool", "off_topic")
_STATES = ("open", "done", "unclear")
_SOURCES = ("email", "chat")
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")

DEFAULT_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 14
_DAYS = re.compile(r"\b(\d{1,3})\s*(?:-\s*)?days?\b", re.IGNORECASE)
_WEEKS = re.compile(r"\b(\d{1,2}|one|two|a)\s*weeks?\b", re.IGNORECASE)
_WORD_WEEKS = {"a": 1, "one": 1, "two": 2}


@dataclass(frozen=True)
class Promise:
    id: str
    source: str
    to: str
    to_name: str
    quote: str
    sent_at: str
    due_phrase: str
    due_date: str | None
    link: str
    thread_ref: str
    topic: str
    state: str = "open"
    evidence_link: str | None = None

    @property
    def who(self) -> str:
        return self.to_name or self.to


@dataclass(frozen=True)
class SweepAnswer:
    status: Status
    promises: tuple[Promise, ...]


@dataclass(frozen=True)
class Verdict:
    state: State
    evidence_link: str | None


def load_json_object(text: str) -> dict | None:
    """The first-to-last-brace JSON object in a model reply, code fences tolerated."""
    body = _CODE_FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_sweep(text: str) -> SweepAnswer | None:
    data = load_json_object(text)
    if data is None or data.get("status") not in _STATUSES:
        return None
    raw = data.get("promises") or []
    if not isinstance(raw, list):
        return None
    parsed = tuple(p for p in (_promise(item) for item in raw) if p is not None)
    return SweepAnswer(status=data["status"], promises=parsed)


def _promise(raw: Any) -> Promise | None:
    if not isinstance(raw, Mapping):
        return None
    source = text_of(raw.get("source")).lower()
    return Promise(
        id=text_of(raw.get("id")),
        source=source if source in _SOURCES else "email",
        to=text_of(raw.get("to")),
        to_name=text_of(raw.get("to_name")),
        quote=text_of(raw.get("quote")),
        sent_at=text_of(raw.get("sent_at")),
        due_phrase=text_of(raw.get("due_phrase")),
        due_date=text_of(raw.get("due_date")) or None,
        link=text_of(raw.get("link")),
        thread_ref=text_of(raw.get("thread_ref")),
        topic=text_of(raw.get("topic")),
    )


def parse_verify(text: str) -> dict[str, Verdict]:
    """Verdicts by candidate id; anything unreadable is simply absent (the promise stays open)."""
    data = load_json_object(text)
    raw = data.get("results") if data else None
    if not isinstance(raw, list):
        return {}
    verdicts: dict[str, Verdict] = {}
    for item in raw:
        if not isinstance(item, Mapping) or item.get("state") not in _STATES:
            continue
        verdicts = {**verdicts, text_of(item.get("id")): Verdict(item["state"], text_of(item.get("evidence_link")) or None)}
    return verdicts


def text_of(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def window_days(query: str) -> int:
    """The search window the user asked for: 7 days by default, never more than 14."""
    days = _DAYS.search(query or "")
    if days:
        return _clamp(int(days.group(1)))
    weeks = _WEEKS.search(query or "")
    if weeks:
        word = weeks.group(1).lower()
        return _clamp(7 * (_WORD_WEEKS.get(word) or int(word)))
    return DEFAULT_WINDOW_DAYS


def _clamp(days: int) -> int:
    return max(1, min(MAX_WINDOW_DAYS, days))


def with_id(promise: Promise, new_id: str) -> Promise:
    return replace(promise, id=new_id)


def to_state(items: Sequence[Promise]) -> list[dict]:
    return [asdict(item) for item in items]


def from_state(raw: Sequence[Mapping[str, Any]] | None) -> tuple[Promise, ...]:
    names = [f.name for f in fields(Promise)]
    return tuple(Promise(**{k: item.get(k) for k in names if k in item}) for item in raw or ())
