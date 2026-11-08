"""Pure data: the records, reading the model's JSON answers, and state (de)serialisation. What survives
the code checks is decided in verify.py; nothing here trusts a value."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields, is_dataclass
from typing import Any, TypeVar

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
ROLES = ("customer", "agent", "system")
SENTIMENTS = ("negative", "neutral", "positive")

T = TypeVar("T")


@dataclass(frozen=True)
class Message:
    author_role: str          # customer | agent | system
    posted_at: str = ""
    public: bool = True
    text: str = ""


@dataclass(frozen=True)
class Ticket:
    id: str
    url: str = ""
    subject: str = ""
    status: str = ""
    priority: str = ""
    tier_hint: str = ""
    organization: str = ""
    created_at: str = ""
    updated_at: str = ""
    reopen_count: int | None = None
    comments: tuple[Message, ...] = ()
    comments_known: bool = True   # False when the comments could not be read


@dataclass(frozen=True)
class Issue:
    id: str
    title: str
    url: str = ""
    state: str = ""


@dataclass(frozen=True)
class Sentiment:
    ticket_id: str
    sentiment: str = "neutral"
    frustration_quote: str = ""
    escalation_quote: str = ""


@dataclass(frozen=True)
class RiskFactor:
    name: str
    points: int
    evidence: str


@dataclass(frozen=True)
class BugMatch:
    ticket_id: str
    issue_id: str
    title: str
    url: str
    state: str
    score: float
    closed: bool = False


def load_json_object(text: str) -> dict | None:
    """The first-to-last-brace JSON object in a reply, code fences tolerated."""
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


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = text_of(value)
    return int(text) if text.isdigit() else None


def _list(data: dict | None, key: str) -> list | None:
    raw = data.get(key) if data else None
    return raw if isinstance(raw, list) else None


def parse_tickets(text: str) -> tuple[Ticket, ...] | None:
    raw = _list(load_json_object(text), "tickets")
    if raw is None:
        return None
    return tuple(t for t in (_ticket(item) for item in raw) if t is not None)


def _ticket(raw: Any) -> Ticket | None:
    if not isinstance(raw, Mapping) or not text_of(raw.get("id")):
        return None
    comments = raw.get("comments") if isinstance(raw.get("comments"), list) else []
    messages = tuple(m for m in (_message(c) for c in comments) if m is not None)
    names = ("url", "subject", "status", "priority", "tier_hint", "organization", "created_at", "updated_at")
    return Ticket(id=text_of(raw.get("id")).lstrip("#"), reopen_count=_int_or_none(raw.get("reopen_count")),
                  comments=messages, comments_known=bool(messages), **{n: text_of(raw.get(n)) for n in names})


def _message(raw: Any) -> Message | None:
    if not isinstance(raw, Mapping) or not text_of(raw.get("text")):
        return None
    role = text_of(raw.get("author_role")).casefold()
    public = raw.get("public")
    return Message(author_role=role if role in ROLES else "system", posted_at=text_of(raw.get("posted_at")),
                   public=public is not False and text_of(public).casefold() != "false", text=text_of(raw.get("text")))


def parse_issues(text: str) -> tuple[Issue, ...] | None:
    raw = _list(load_json_object(text), "issues")
    if raw is None:
        return None
    issues = (Issue(id=text_of(i.get("id")), title=text_of(i.get("title")), url=text_of(i.get("url")),
                    state=text_of(i.get("state"))) for i in raw if isinstance(i, Mapping))
    return tuple(i for i in issues if i.id and i.title)


def parse_sentiments(text: str) -> tuple[Sentiment, ...] | None:
    raw = _list(load_json_object(text), "tickets")
    if raw is None:
        return None
    out = []
    for item in raw:
        if not isinstance(item, Mapping) or not text_of(item.get("ticket_id")):
            continue
        label = text_of(item.get("sentiment")).casefold()
        out.append(Sentiment(ticket_id=text_of(item.get("ticket_id")).lstrip("#"),
                             sentiment=label if label in SENTIMENTS else "neutral",
                             frustration_quote=text_of(item.get("frustration_quote")),
                             escalation_quote=text_of(item.get("escalation_quote"))))
    return tuple(out)


def parse_body(text: str) -> str:
    data = load_json_object(text)
    return text_of(data.get("body")) if data else ""


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
    names = {f.name: f for f in fields(cls)}
    values = {}
    for key, value in raw.items():
        if key not in names:
            continue
        if key == "comments":
            value = tuple(m if is_dataclass(m) else Message(**m) for m in value or ())
        values[key] = tuple(value) if isinstance(value, list) else value
    return cls(**values)
