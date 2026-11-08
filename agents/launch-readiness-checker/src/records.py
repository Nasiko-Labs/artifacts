"""Pure: the rows each step keeps, and the model's JSON turned into them. Every id, link, title, name and date
the model returns must appear in this step's tool output (`seen`), or it is dropped; enums are validated."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import dates

STATE_TYPES = frozenset({"backlog", "unstarted", "started", "completed", "canceled"})
PRIORITIES = frozenset({"urgent", "high", "medium", "low", "none"})
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Issue:
    id: str
    url: str | None
    title: str
    state: str
    state_type: str
    priority: str
    assignee: str | None
    labels: tuple[str, ...] = ()
    updated_at: str | None = None
    source: str = "tool"

    @property
    def open(self) -> bool:
        return self.state_type not in {"completed", "canceled"}


@dataclass(frozen=True)
class Doc:
    id: str
    url: str | None
    title: str
    modified_at: date | None
    text: str = ""
    capped: bool = False


@dataclass(frozen=True)
class Macro:
    id: str
    title: str
    body: str = ""


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    url: str | None = None


@dataclass(frozen=True)
class Task:
    id: str
    url: str | None
    title: str
    completed: bool
    due_date: date | None
    assignee: str | None
    source: str = "tool"
    project: str | None = None


@dataclass(frozen=True)
class Row:
    area: str
    rule_id: str
    title: str
    status: str                     # pass | warn | fail | unknown
    blocking: bool
    evidence: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict:
        return {"area": self.area, "rule_id": self.rule_id, "title": self.title, "status": self.status,
                "blocking": self.blocking, "evidence": list(self.evidence), "detail": self.detail}


@dataclass(frozen=True)
class Parsed:
    rows: tuple = ()
    extracted: int = 0
    dropped: int = 0
    capped: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)


def norm(text: str) -> str:
    return _SPACE.sub(" ", str(text)).strip().casefold()


class Seen:
    """This step's tool output: exact for ids and links, normalised for titles, names and quotes."""

    def __init__(self, text: str):
        self.raw = text
        self.folded = norm(text)

    def exact(self, value: Any) -> str | None:
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        return value.strip() if isinstance(value, str) and value.strip() and value.strip() in self.raw else None

    def loose(self, value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        return " ".join(value.split()) if norm(value) in self.folded else None


def json_object(text: str) -> Mapping[str, Any] | None:
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, Mapping) else None


def _list(data: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = data.get(key)
    return [x for x in value if isinstance(x, Mapping)] if isinstance(value, list) else []


def _unique(rows: Sequence) -> tuple:
    return tuple({r.id: r for r in reversed(rows)}.values())[::-1]


def issues(data: Mapping[str, Any], seen: Seen, cap: int) -> Parsed:
    raw = _list(data, "issues")
    kept = []
    for item in raw:
        ident, title = seen.exact(item.get("id")), seen.loose(item.get("title"))
        state_type, priority = str(item.get("state_type") or "").casefold(), str(item.get("priority") or "none")
        if not ident or not title or state_type not in STATE_TYPES:
            continue
        labels = tuple(x for x in (seen.loose(lb) for lb in item.get("labels") or () if isinstance(lb, str)) if x)
        kept.append(Issue(ident, seen.exact(item.get("url")), title, seen.loose(item.get("state")) or state_type,
                          state_type, priority.casefold() if priority.casefold() in PRIORITIES else "none",
                          seen.loose(item.get("assignee")), labels, seen.exact(item.get("updated_at"))))
    rows = _unique(kept)
    return Parsed(rows[:cap], len(raw), len(raw) - len(kept), len(rows) > cap)


def docs(data: Mapping[str, Any], seen: Seen, cap: int) -> Parsed:
    raw = _list(data, "docs")
    kept = []
    for item in raw:
        ident, title = seen.exact(item.get("id")), seen.loose(item.get("title"))
        if not ident or not title:
            continue
        modified = seen.exact(item.get("modified_at"))
        kept.append(Doc(ident, seen.exact(item.get("url")), title, dates.tool_date(modified) if modified else None))
    rows = _unique(kept)
    return Parsed(rows[:cap], len(raw), len(raw) - len(kept), len(rows) > cap)


def support(data: Mapping[str, Any], seen: Seen, cap: int) -> Parsed:
    macros_raw, articles_raw = _list(data, "macros"), _list(data, "articles")
    macros = [Macro(i, t, seen.loose(m.get("body_excerpt")) or "") for m in macros_raw
              for i, t in [(seen.exact(m.get("id")), seen.loose(m.get("title")))] if i and t]
    articles = [Article(i, t, seen.exact(a.get("url"))) for a in articles_raw
                for i, t in [(seen.exact(a.get("id")), seen.loose(a.get("title")))] if i and t]
    raw = len(macros_raw) + len(articles_raw)
    kept_m, kept_a = _unique(macros), _unique(articles)
    return Parsed((*kept_m[:cap], *kept_a[:cap]), raw, raw - len(macros) - len(articles),
                  len(kept_m) > cap or len(kept_a) > cap)


def tasks(data: Mapping[str, Any], seen: Seen, cap: int) -> Parsed:
    raw = _list(data, "tasks")
    kept = []
    for item in raw:
        ident, title = seen.exact(item.get("id")), seen.loose(item.get("title"))
        if not ident or not title or not isinstance(item.get("completed"), bool):
            continue
        due = seen.exact(item.get("due_date"))
        kept.append(Task(ident, seen.exact(item.get("url")), title, item["completed"],
                         dates.tool_date(due) if due else None, seen.loose(item.get("assignee")), "tool",
                         seen.loose(item.get("project"))))
    rows = _unique(kept)
    return Parsed(rows[:cap], len(raw), len(raw) - len(kept), len(rows) > cap)
