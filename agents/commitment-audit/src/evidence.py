"""Pure: tracker facts and chat signals for each commitment, read from the model's JSON and checked
against this turn's tool output.

Tracker: the item's key must be verbatim in tool output, or the commitment has no tracker item. Every
other field the model copied (status, assignee, dates, blocker keys, the status comment's quote and
link) is dropped unless a tool returned it verbatim; each drop is counted, and costs confidence.
Child counts must be integers with 0 ≤ done ≤ total ≤ 500, else both are unknown.

Chat: a signal survives only with a verbatim quote and link, dated inside the quarter and not in the
future; at most MAX_SIGNALS per commitment, newest first."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from commitments import Commitment
from quarter import Quarter
from verbatim import (
    appears_in,
    int_or_none,
    load_json_object,
    normalise_text,
    parse_day,
    text_of,
)

MAX_CHILDREN = 500
MAX_SIGNALS = 3
CATEGORIES = ("todo", "in_progress", "done", "unknown")
KINDS = ("scope_cut", "scope_added", "delay", "blocked", "done", "on_track", "other")
Category = Literal["todo", "in_progress", "done", "unknown"]


@dataclass(frozen=True)
class Blocker:
    key: str
    status_category: str


@dataclass(frozen=True)
class StatusComment:
    at: str
    quote: str
    link: str | None


@dataclass(frozen=True)
class TrackerFacts:
    key: str
    status: str | None = None
    status_category: str = "unknown"
    assignee: str | None = None
    due_date: str | None = None
    updated_at: str | None = None
    children_total: int | None = None
    children_done: int | None = None
    children_updated_max: str | None = None
    blockers: tuple[Blocker, ...] = ()
    last_update_comment: StatusComment | None = None
    link: str | None = None
    dropped: int = 0  # fields removed by the verbatim check


@dataclass(frozen=True)
class Signal:
    kind: str
    quote: str
    at: str
    link: str

    @property
    def day(self) -> date | None:
        return parse_day(self.at)


def parse_items(text: str, key: str) -> list[Mapping[str, Any]] | None:
    """The list under `key` in a JSON reply; None when the reply is not that JSON."""
    data = load_json_object(text)
    raw = data.get(key) if data else None
    if not isinstance(raw, list):
        return None
    return [item for item in raw if isinstance(item, Mapping)]


def check_tracker(items: Sequence[Mapping[str, Any]], commitments: Sequence[Commitment],
                  seen_text: str) -> dict[str, TrackerFacts]:
    seen = normalise_text(seen_text)
    known = {c.cid for c in commitments}
    facts: dict[str, TrackerFacts] = {}
    for item in items:
        cid = text_of(item.get("cid"))
        if cid not in known or cid in facts:
            continue
        checked = _facts(item, seen)
        if checked is not None:
            facts = {**facts, cid: checked}
    return facts


def _facts(item: Mapping[str, Any], seen: str) -> TrackerFacts | None:
    key = text_of(item.get("key"))
    if not appears_in(key, seen):
        return None
    dropped = 0
    status, d = _kept(item.get("status"), seen)
    dropped += d
    assignee, d = _kept(item.get("assignee"), seen)
    dropped += d
    due, d = _kept_date(item.get("due_date"), seen)
    dropped += d
    updated, d = _kept_date(item.get("updated_at"), seen)
    dropped += d
    children_max, d = _kept_date(item.get("children_updated_max"), seen)
    dropped += d
    total, done, d = _counts(item.get("children_total"), item.get("children_done"))
    dropped += d
    blockers, d = _blockers(item.get("blockers"), seen)
    dropped += d
    comment, d = _comment(item.get("last_update_comment"), seen)
    dropped += d
    link, d = _kept(item.get("link"), seen)
    category = text_of(item.get("status_category")).lower()
    return TrackerFacts(
        key=key, status=status, status_category=category if status and category in CATEGORIES else "unknown",
        assignee=assignee, due_date=due, updated_at=updated, children_total=total, children_done=done,
        children_updated_max=children_max, blockers=blockers, last_update_comment=comment, link=link,
        dropped=dropped + d,
    )


def _kept(value: Any, seen: str) -> tuple[str | None, int]:
    text = text_of(value)
    if not text:
        return None, 0
    return (text, 0) if appears_in(text, seen) else (None, 1)


def _kept_date(value: Any, seen: str) -> tuple[str | None, int]:
    text, dropped = _kept(value, seen)
    if text is None:
        return None, dropped
    return (text, 0) if parse_day(text) else (None, 1)


def _counts(total_raw: Any, done_raw: Any) -> tuple[int | None, int | None, int]:
    if total_raw is None and done_raw is None:
        return None, None, 0
    total, done = int_or_none(total_raw), int_or_none(done_raw)
    if total is None or done is None or not 0 <= done <= total <= MAX_CHILDREN:
        return None, None, 1
    return total, done, 0


def _blockers(raw: Any, seen: str) -> tuple[tuple[Blocker, ...], int]:
    if not isinstance(raw, list):
        return (), 0
    kept: tuple[Blocker, ...] = ()
    for item in raw:
        key = text_of(item.get("key")) if isinstance(item, Mapping) else ""
        category = text_of(item.get("status_category")).lower() if isinstance(item, Mapping) else ""
        if appears_in(key, seen):
            kept = (*kept, Blocker(key, category if category in CATEGORIES else "unknown"))
    return kept, len(raw) - len(kept)


def _comment(raw: Any, seen: str) -> tuple[StatusComment | None, int]:
    if not isinstance(raw, Mapping):
        return None, 0
    quote, at = text_of(raw.get("quote")), text_of(raw.get("at"))
    if not appears_in(quote, seen) or parse_day(at) is None:
        return None, 1
    link = text_of(raw.get("link"))
    return StatusComment(at, quote, link if appears_in(link, seen) else None), 0


def check_signals(items: Sequence[Mapping[str, Any]], commitments: Sequence[Commitment], seen_text: str,
                  quarter: Quarter, today: date) -> dict[str, tuple[Signal, ...]]:
    seen = normalise_text(seen_text)
    by_cid: dict[str, tuple[Signal, ...]] = {c.cid: () for c in commitments}
    for item in items:
        cid = text_of(item.get("cid"))
        signal = _signal(item, seen, quarter, today)
        if cid in by_cid and signal is not None and signal not in by_cid[cid]:
            by_cid = {**by_cid, cid: (*by_cid[cid], signal)}
    return {cid: _newest(signals) for cid, signals in by_cid.items() if signals}


def _signal(item: Mapping[str, Any], seen: str, quarter: Quarter, today: date) -> Signal | None:
    kind = text_of(item.get("kind")).lower()
    quote, link, at = text_of(item.get("quote")), text_of(item.get("link")), text_of(item.get("at"))
    day = parse_day(at)
    if kind not in KINDS or day is None or not quarter.contains(day) or day > today:
        return None
    if not appears_in(quote, seen) or not appears_in(link, seen):
        return None
    return Signal(kind, quote, at, link)


def _newest(signals: tuple[Signal, ...]) -> tuple[Signal, ...]:
    return tuple(sorted(signals, key=lambda s: s.day or date.min, reverse=True)[:MAX_SIGNALS])
