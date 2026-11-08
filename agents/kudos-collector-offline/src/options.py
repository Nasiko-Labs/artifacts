"""Pure: the optional `Period:`, `People:` and `Min:` lines, and the cap of 400 records. The period filter runs in
code: dated records outside it are left out and counted; undated records are kept and counted."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date

import dates
from parse_records import MIN, PEOPLE, PERIOD
from records import Record

MAX_RECORDS = 400
MIN_RECORDS = 3
MAX_CHARS = 100_000
MAX_MIN = 50


def people(options: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(p.strip() for p in re.split(r"[,;]", options.get(PEOPLE, "")) if p.strip())


def min_examples(options: Mapping[str, str]) -> int:
    match = re.search(r"\d+", options.get(MIN, ""))
    return max(1, min(MAX_MIN, int(match.group(0)))) if match else 1


def by_period(records: Sequence[Record], options: Mapping[str, str], today: date) -> tuple[list[Record], list[str]]:
    value = options.get(PERIOD, "").strip()
    if not value:
        return list(records), []
    years = [int(r.date[:4]) for r in records if r.date]
    span = dates.parse_period(value, today, years)
    if span is None:
        return list(records), [f"I couldn't read the period '{value[:40]}', so no period filter was applied."]
    start, end = span
    kept = [r for r in records if r.date is None or start <= r.date <= end]
    outside, undated = len(records) - len(kept), sum(1 for r in kept if r.date is None)
    note = f"Period {start} to {end}: {outside} dated record{'s' if outside != 1 else ''} outside it left out"
    note += f"; {undated} undated record{'s' if undated != 1 else ''} kept." if undated else "."
    return kept, [note]


def most_recent(records: Sequence[Record], limit: int = MAX_RECORDS) -> list[Record]:
    """The `limit` newest records (undated last), kept in their pasted order."""
    order = sorted(range(len(records)), key=lambda i: (records[i].date is None, _desc(records[i].date), i))
    keep = set(order[:limit])
    return [r for i, r in enumerate(records) if i in keep]


def _desc(iso: str | None) -> tuple[int, ...]:
    return tuple(-int(x) for x in (iso or "0-0-0").split("-"))
