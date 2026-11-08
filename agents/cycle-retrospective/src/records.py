"""Pure data: the records, reading the model's JSON answers, and state (de)serialisation. What survives
the code checks is decided in checks.py; nothing here trusts a value."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any, TypeVar

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
STATUSES = ("ok", "off_topic", "no_tracker_tool", "not_found")
REASONS = ("blocked_dependency", "scope_growth", "underestimated", "unplanned_work", "external",
           "unclear_requirements", "no_reason_found")
NO_REASON = "no_reason_found"

T = TypeVar("T")


@dataclass(frozen=True)
class Team:
    id: str
    name: str


@dataclass(frozen=True)
class Cycle:
    id: str
    number: str = ""
    name: str = ""
    starts_at: str = ""
    ends_at: str = ""
    team_id: str = ""


@dataclass(frozen=True)
class Issue:
    id: str                       # the human identifier, e.g. PLAT-120
    title: str = ""
    url: str = ""
    state: str = ""
    state_type: str = ""
    estimate: float | None = None
    created_at: str = ""
    completed_at: str = ""
    added_to_cycle_at: str = ""
    canceled: bool = False


@dataclass(frozen=True)
class Evidence:
    issue_id: str
    source: str                   # comment | chat
    text: str
    url: str = ""
    posted_at: str = ""


@dataclass(frozen=True)
class Spill:
    issue_id: str
    title: str = ""
    url: str = ""
    estimate: float | None = None
    reason: str = NO_REASON
    quote: str = ""
    source: str = ""
    posted_at: str = ""


@dataclass(frozen=True)
class CycleAnswer:
    status: str
    teams: tuple[Team, ...] = ()
    cycles: tuple[Cycle, ...] = ()


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


def text_of(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    return str(value).strip()


def number_of(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def parse_cycles(text: str) -> CycleAnswer | None:
    data = load_json_object(text)
    if data is None or data.get("status") not in STATUSES:
        return None
    teams = tuple(Team(text_of(t.get("id")), text_of(t.get("name"))) for t in _list(data.get("teams"))
                  if text_of(t.get("id")) and text_of(t.get("name")))
    cycles = tuple(Cycle(id=text_of(c.get("id")), number=text_of(c.get("number")), name=text_of(c.get("name")),
                         starts_at=text_of(c.get("starts_at")), ends_at=text_of(c.get("ends_at")),
                         team_id=text_of(c.get("team_id"))) for c in _list(data.get("cycles")) if text_of(c.get("id")))
    return CycleAnswer(data["status"], teams, cycles)


def parse_issues(text: str) -> tuple[Issue, ...] | None:
    data = load_json_object(text)
    if data is None or not isinstance(data.get("issues"), list):
        return None
    return tuple(_issue(raw) for raw in _list(data["issues"]) if text_of(raw.get("id")))


def _issue(raw: Mapping[str, Any]) -> Issue:
    names = ("title", "url", "state", "state_type", "created_at", "completed_at", "added_to_cycle_at")
    canceled = raw.get("canceled") is True or text_of(raw.get("state_type")).casefold() in ("canceled", "cancelled")
    return Issue(id=text_of(raw.get("id")), estimate=number_of(raw.get("estimate")), canceled=canceled,
                 **{n: text_of(raw.get(n)) for n in names})


def parse_evidence(text: str) -> tuple[Evidence, ...] | None:
    data = load_json_object(text)
    if data is None or not isinstance(data.get("evidence"), list):
        return None
    return tuple(Evidence(issue_id=text_of(e.get("issue_id")), source="chat" if text_of(e.get("source")) == "chat"
                          else "comment", text=text_of(e.get("text")), url=text_of(e.get("url")),
                          posted_at=text_of(e.get("posted_at")))
                 for e in _list(data["evidence"]) if text_of(e.get("issue_id")) and text_of(e.get("text")))


def parse_reasons(text: str) -> tuple[tuple[str, str, str], ...] | None:
    """(issue_id, reason, quote) per labelled issue; an unknown reason label reads as no_reason_found."""
    data = load_json_object(text)
    if data is None or not isinstance(data.get("reasons"), list):
        return None
    out = []
    for raw in _list(data["reasons"]):
        reason = text_of(raw.get("reason"))
        out.append((text_of(raw.get("issue_id")), reason if reason in REASONS else NO_REASON, text_of(raw.get("quote"))))
    return tuple(r for r in out if r[0])


def _list(value: Any) -> list[Mapping[str, Any]]:
    return [v for v in value if isinstance(v, Mapping)] if isinstance(value, list) else []


def to_state(items: Sequence[Any]) -> list[dict]:
    return [asdict(item) for item in items]


def from_state(cls: type[T], raw: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None) -> Any:
    """One record from a dict (None from None), or a tuple of records from a list."""
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return _one(cls, raw)
    return tuple(_one(cls, item) for item in raw)


def _one(cls: type[T], raw: Mapping[str, Any]) -> T:
    names = {f.name for f in fields(cls)}
    values = {k: tuple(v) if isinstance(v, list) else v for k, v in raw.items() if k in names}
    return cls(**values)
