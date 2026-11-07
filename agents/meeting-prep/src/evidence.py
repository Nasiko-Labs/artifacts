"""Pure: the context step's evidence for one meeting, and the code post-check on it.

An item survives only when its quote and its link (or id) appear in that meeting's tool output and its
time parses and falls in its window (the last 14 days; a prior meeting in its own 35-day window). At
most MAX_ITEMS survive, newest first, numbered E1..En for the card step."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, tzinfo
from typing import Any

import timewin
from verbatim import Seen, load_json_object, text_of

MAX_ITEMS = 12
KINDS = ("email", "chat", "document", "ticket", "prior_meeting")
_KIND_LABEL = {"email": "email", "chat": "chat", "document": "document", "ticket": "ticket",
               "prior_meeting": "calendar"}


@dataclass(frozen=True)
class Evidence:
    eid: str
    kind: str
    at: str           # ISO 8601, set by code from the parsed time
    who: str
    quote: str
    link: str
    status: str = ""  # tickets only, exactly as returned

    @property
    def label(self) -> str:
        return _KIND_LABEL.get(self.kind, self.kind)

    def when(self) -> datetime:
        return datetime.fromisoformat(self.at)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Evidence:
        return Evidence(**data)


@dataclass(frozen=True)
class Checked:
    items: tuple[Evidence, ...]
    dropped: int
    last_meeting_at: datetime | None


def parse_evidence(text: str) -> tuple[dict, ...] | None:
    """The raw items of an `{"evidence": [...]}` answer; None when the answer is not that JSON."""
    data = load_json_object(text)
    if data is None or not isinstance(data.get("evidence"), list):
        return None
    return tuple(item for item in data["evidence"] if isinstance(item, dict))


def check(raw_items: Sequence[dict], seen: Seen, *, tz: tzinfo, window: timewin.Window,
          prior: timewin.Window) -> Checked:
    kept = [e for e in (_checked(item, seen, tz, window, prior) for item in raw_items) if e is not None]
    unique = list({(e.kind, e.link, e.quote.casefold()): e for e in kept}.values())
    newest = sorted(unique, key=lambda e: e.when(), reverse=True)[:MAX_ITEMS]
    numbered = tuple(replace(e, eid=f"E{n}") for n, e in enumerate(newest, start=1))
    priors = [e.when() for e in numbered if e.kind == "prior_meeting"]
    return Checked(numbered, len(raw_items) - len(numbered), max(priors) if priors else None)


def _checked(raw: dict, seen: Seen, tz: tzinfo, window: timewin.Window, prior: timewin.Window) -> Evidence | None:
    kind = text_of(raw.get("kind"), 20)
    quote, link = text_of(raw.get("quote"), 400), text_of(raw.get("link"), 500)
    at = timewin.parse_instant(raw.get("at"), tz)
    if kind not in KINDS or at is None or not (seen.has(quote) and seen.has(link)):
        return None
    if not (prior if kind == "prior_meeting" else window).contains(at):
        return None
    status = text_of(raw.get("status"), 60) if kind == "ticket" else ""
    who = text_of(raw.get("who"), 120)
    return Evidence(eid="", kind=kind, at=at.isoformat(), who=who if seen.has(who) else "", quote=quote, link=link,
                    status=status if seen.has(status) else "")


def by_id(items: Sequence[Evidence]) -> dict[str, Evidence]:
    return {e.eid: e for e in items}


def describe(items: Sequence[Evidence], tz: tzinfo) -> list[dict[str, Any]]:
    """The numbered evidence as the card step sees it (dates already rendered, never computed by it)."""
    return [{"id": e.eid, "kind": e.kind, "date": timewin.short_date(e.when(), tz), "who": e.who, "quote": e.quote,
             "status": e.status or None} for e in items]
