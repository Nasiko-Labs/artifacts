"""Pure data: the records, reading the model's JSON answers, and timestamp parsing. What survives the code
checks is decided in verify.py; nothing here trusts a value."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")


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
    first_reply_at: str = ""
    solved_at: str = ""
    reply_minutes: int | None = None
    resolve_minutes: int | None = None
    no_reply: bool = False        # pasted "first reply: none": known to have no reply yet


@dataclass(frozen=True)
class SlaPolicy:
    priority: str
    first_reply_minutes: int | None = None
    resolution_minutes: int | None = None


@dataclass(frozen=True)
class Breach:
    ticket_id: str
    kind: str                 # first_reply | resolution
    open: bool                # still waiting (no reply yet / unsolved)
    overrun_minutes: int
    priority: str
    customer: str = ""
    url: str = ""
    subject: str = ""


@dataclass(frozen=True)
class Theme:
    name: str
    count: int
    share_pct: float
    example: str


@dataclass(frozen=True)
class PreviousCount:
    count: int | None
    tickets: tuple[Ticket, ...] = ()


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


def int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = text_of(value)
    return int(text) if text.isdigit() else None


def parse_time(value: str) -> datetime | None:
    """An ISO-ish timestamp as UTC (a bare date is midnight UTC); None when there is none."""
    match = _STAMP.search(value or "")
    if not match:
        return None
    day, hour, minute, second = match.groups()
    try:
        base = datetime.fromisoformat(day)
    except ValueError:
        return None
    return base.replace(hour=int(hour or 0), minute=int(minute or 0), second=int(second or 0), tzinfo=UTC)


def _list(data: dict | None, key: str) -> list | None:
    raw = data.get(key) if data else None
    return raw if isinstance(raw, list) else None


_TICKET_TEXT = ("url", "subject", "status", "priority", "tier_hint", "organization", "created_at",
                "first_reply_at", "solved_at")


def ticket_from(raw: Any) -> Ticket | None:
    if not isinstance(raw, Mapping) or not text_of(raw.get("id")):
        return None
    return Ticket(id=text_of(raw.get("id")).lstrip("#"), reply_minutes=int_or_none(raw.get("reply_minutes")),
                  resolve_minutes=int_or_none(raw.get("resolve_minutes")),
                  **{n: text_of(raw.get(n)) for n in _TICKET_TEXT})


def parse_tickets(text: str) -> tuple[tuple[Ticket, ...], int | None] | None:
    """`{"tickets": [...], "total": N|null}` → (tickets, the total the search reported)."""
    data = load_json_object(text)
    raw = _list(data, "tickets")
    if raw is None:
        return None
    tickets = tuple(t for t in (ticket_from(item) for item in raw) if t is not None)
    return tickets, int_or_none(data.get("total"))


def parse_previous(text: str) -> PreviousCount | None:
    data = load_json_object(text)
    if data is None or ("count" not in data and "tickets" not in data):
        return None
    raw = _list(data, "tickets") or []
    return PreviousCount(count=int_or_none(data.get("count")),
                         tickets=tuple(t for t in (ticket_from(i) for i in raw) if t is not None))


def parse_policies(text: str) -> tuple[SlaPolicy, ...] | None:
    raw = _list(load_json_object(text), "policies")
    if raw is None:
        return None
    return tuple(SlaPolicy(priority=text_of(p.get("priority")).casefold(),
                           first_reply_minutes=int_or_none(p.get("first_reply_minutes")),
                           resolution_minutes=int_or_none(p.get("resolution_minutes")))
                 for p in raw if isinstance(p, Mapping) and text_of(p.get("priority")))


def parse_labels(text: str) -> dict[str, str] | None:
    """`{"labels": [{"ticket_id", "theme_label"}]}` → {ticket id: label}."""
    raw = _list(load_json_object(text), "labels")
    if raw is None:
        return None
    pairs = ((text_of(i.get("ticket_id")).lstrip("#"), text_of(i.get("theme_label")))
             for i in raw if isinstance(i, Mapping))
    return {tid: label for tid, label in pairs if tid and label}


def parse_clusters(text: str) -> tuple[tuple[str, tuple[str, ...]], ...] | None:
    raw = _list(load_json_object(text), "themes")
    if raw is None:
        return None
    out = []
    for item in raw:
        if not isinstance(item, Mapping) or not isinstance(item.get("labels"), list):
            continue
        out.append((text_of(item.get("name")), tuple(text_of(x) for x in item["labels"] if text_of(x))))
    return tuple(out)
