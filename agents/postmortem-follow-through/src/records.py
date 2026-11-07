"""Pure: the records the model extracts, read from its JSON and checked against the step's raw tool
output. A record whose required field the tools never returned verbatim is dropped and counted;
optional fields that were not seen are set to null.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, timedelta
from typing import Any

from corpus import Corpus
from dates import parse_date

MAX_POSTMORTEMS = 40
MAX_ITEMS = 300
TICKET_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def load_json_object(text: str) -> dict | None:
    """The model's JSON object, tolerating a code fence or prose around it."""
    raw = _FENCE.sub("", (text or "").strip())
    for candidate in (raw, raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else ""):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def text_of(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ("" if value is None else str(value).strip())


def _opt(value: Any) -> str | None:
    text = text_of(value)
    return text or None


def to_state(records: Sequence[Any]) -> list[dict]:
    return [asdict(r) for r in records]


def from_state(cls: type, rows: Sequence[Mapping[str, Any]] | None) -> tuple[Any, ...]:
    names = {f.name for f in fields(cls)}
    out = []
    for row in rows or ():
        values = {k: v for k, v in row.items() if k in names}
        if "links" in values:
            values["links"] = tuple(values["links"] or ())
        out.append(cls(**values))
    return tuple(out)


@dataclass(frozen=True)
class Postmortem:
    id: str
    kind: str  # "ticket" | "doc"
    key_or_url: str
    title: str
    created_at: str
    incident_ref: str | None = None
    service: str | None = None
    incident_date: str | None = None

    @property
    def created(self) -> date:
        return parse_date(self.created_at)

    @property
    def date_shown(self) -> date:
        return parse_date(self.incident_date) or self.created


@dataclass(frozen=True)
class ActionItem:
    id: str
    postmortem_id: str
    ticket_key: str | None
    text: str
    from_doc: bool = False
    owner_text: str | None = None


@dataclass(frozen=True)
class TicketStatus:
    key: str
    status: str
    created_at: str
    updated_at: str
    status_category: str | None = None
    resolution: str | None = None
    assignee_name: str | None = None
    assignee_email: str | None = None
    resolved_at: str | None = None
    url: str | None = None
    links: tuple[str, ...] = ()
    dev_links: int | None = None
    attachments: int | None = None


@dataclass(frozen=True)
class Incident:
    id: str
    title: str
    created_at: str
    service: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class Checked:
    """Records that passed, how many were dropped, and whether a cap cut the list."""

    kept: tuple[Any, ...]
    dropped: int = 0
    capped: bool = False


def _seen_or_none(corpus: Corpus, value: Any) -> str | None:
    text = _opt(value)
    return text if text and corpus.contains(text) else None


def _seen_date(corpus: Corpus, value: Any) -> str | None:
    text = _seen_or_none(corpus, value)
    return text if text and parse_date(text) else None


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value >= 0 else None


def check_postmortems(raw: Any, corpus: Corpus, window_start: date, kind: str) -> Checked:
    rows = [r for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []
    kept: list[Postmortem] = []
    older = 0  # outside the window: left out, but not a failed check
    for n, row in enumerate(rows, 1):
        key, title = _seen_or_none(corpus, row.get("key_or_url")), _seen_or_none(corpus, row.get("title"))
        created_text = _seen_date(corpus, row.get("created_at"))
        created = parse_date(created_text) if created_text else None
        if not key or not title or created is None:
            continue
        if created < window_start:
            older += 1
            continue
        incident_date = _seen_date(corpus, row.get("incident_date"))
        if incident_date and parse_date(incident_date) > created + timedelta(days=1):
            incident_date = None
        kept.append(Postmortem(id=text_of(row.get("id")) or f"pm{n}", kind=kind, key_or_url=key, title=title,
                               created_at=created_text, incident_ref=_seen_or_none(corpus, row.get("incident_ref")),
                               service=_seen_or_none(corpus, row.get("service")), incident_date=incident_date))
    unique = _unique_ids(kept, "pm")
    return Checked(tuple(unique[:MAX_POSTMORTEMS]), len(rows) - len(kept) - older, len(unique) > MAX_POSTMORTEMS)


def check_items(raw: Any, corpus: Corpus, postmortem_ids: set[str]) -> Checked:
    """Tracker action items: the ticket key and its text must both have been returned."""
    rows = [r for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []
    kept: list[ActionItem] = []
    for n, row in enumerate(rows, 1):
        key, text = _seen_or_none(corpus, row.get("ticket_key")), _seen_or_none(corpus, row.get("text"))
        pm = text_of(row.get("postmortem_id"))
        if not key or not TICKET_KEY.fullmatch(key) or not text or pm not in postmortem_ids:
            continue
        kept.append(ActionItem(id=text_of(row.get("id")) or f"ai{n}", postmortem_id=pm, ticket_key=key, text=text))
    unique = list({i.ticket_key: i for i in reversed(kept)}.values())[::-1]
    unique = _unique_ids(unique, "ai")
    return Checked(tuple(unique[:MAX_ITEMS]), len(rows) - len(kept), len(unique) > MAX_ITEMS)


def check_statuses(raw: Any, corpus: Corpus, wanted: set[str]) -> Checked:
    rows = [r for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []
    kept: dict[str, TicketStatus] = {}
    for row in rows:
        status = _check_status(row, corpus)
        if status is not None and status.key in wanted:
            kept.setdefault(status.key, status)
    return Checked(tuple(kept.values()), len(rows) - len(kept))


def _check_status(row: Mapping[str, Any], corpus: Corpus) -> TicketStatus | None:
    key, status = _seen_or_none(corpus, row.get("key")), _seen_or_none(corpus, row.get("status"))
    created, updated = _seen_date(corpus, row.get("created_at")), _seen_date(corpus, row.get("updated_at"))
    if not key or not status or not created or not updated:
        return None
    name, email = _opt(row.get("assignee_name")), _opt(row.get("assignee_email"))
    if (name and not corpus.contains(name)) or (email and not corpus.contains(email)):
        return None  # an unseen assignee is dropped, never turned into "unassigned"
    resolution = _opt(row.get("resolution"))
    if resolution and not corpus.contains(resolution):
        return None
    resolved_raw = _opt(row.get("resolved_at"))
    resolved = _seen_date(corpus, resolved_raw)
    if resolved_raw and not resolved:
        return None
    links = tuple(u for u in (row.get("links") or ()) if isinstance(u, str) and corpus.contains(u))
    category = text_of(row.get("status_category")) or None
    return TicketStatus(key=key, status=status, created_at=created, updated_at=updated,
                        status_category=category if category in ("todo", "in_progress", "done") else None,
                        resolution=resolution, assignee_name=name, assignee_email=email, resolved_at=resolved,
                        url=_seen_or_none(corpus, row.get("url")), links=links,
                        dev_links=_count(row.get("dev_links")), attachments=_count(row.get("attachments")))


def check_incidents(raw: Any, corpus: Corpus) -> Checked:
    rows = [r for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []
    kept: dict[str, Incident] = {}
    for row in rows:
        iid, title = _seen_or_none(corpus, row.get("id")), _seen_or_none(corpus, row.get("title"))
        created = _seen_date(corpus, row.get("created_at"))
        if not iid or not title or not created:
            continue
        kept.setdefault(iid, Incident(id=iid, title=title, created_at=created,
                                      service=_seen_or_none(corpus, row.get("service")),
                                      url=_seen_or_none(corpus, row.get("url"))))
    return Checked(tuple(kept.values()), len(rows) - len(kept))


def _unique_ids(records: list, prefix: str) -> list:
    """Every record needs its own id; a blank or repeated one is replaced."""
    ids = [r.id for r in records]
    return [r if ids.count(r.id) == 1 else replace(r, id=f"{prefix}-auto-{n}") for n, r in enumerate(records, 1)]
