"""Pure: the run's counts (kept across pastes in one conversation) and the output envelope
(`summary`, `items`, `confidence`, `needs_human` + `promises`, `overdue`). `confidence` and `needs_human`
are computed here from the checks, never taken from the model."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

from ledger import Ledger, Row

HIGH_MAX_DROP = 0.10
LOW_MIN_DROP = 0.30
MANY_FIRST_PERSON = 3


@dataclass(frozen=True)
class Stats:
    messages: int = 0
    candidates: int = 0
    dropped: int = 0
    parts: int = 0
    failed_parts: int = 0
    timed_out_parts: int = 0
    first_person: int = 0
    speaker_guess: str = ""
    as_of_explicit: bool = False

    def plus(self, other: Stats) -> Stats:
        summed = {f.name: getattr(self, f.name) + getattr(other, f.name)
                  for f in fields(Stats) if f.name not in ("speaker_guess", "as_of_explicit")}
        return replace(other, **summed)

    @property
    def drop_ratio(self) -> float:
        return self.dropped / self.candidates if self.candidates else 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Stats:
        data = data or {}
        values = {f.name: data[f.name] for f in fields(Stats) if f.name in data}
        ints = {k: int(v) for k, v in values.items() if k not in ("speaker_guess", "as_of_explicit")}
        return Stats(**{**values, **ints})


def confidence(ledger: Ledger, stats: Stats) -> str:
    promises = [r.promise for r in ledger.rows]
    if stats.drop_ratio > LOW_MIN_DROP or stats.failed_parts or stats.timed_out_parts:
        return "low"
    if not promises and stats.first_person >= MANY_FIRST_PERSON:
        return "low"
    clock = any(p.basis == "clock" for p in promises)
    if not promises or clock or stats.speaker_guess or stats.drop_ratio > HIGH_MAX_DROP:
        return "medium"
    return "high"


def needs_human(ledger: Ledger, stats: Stats, level: str) -> bool:
    unclear = any(r.promise.state == "unclear" for r in ledger.rows)
    return level == "low" or unclear or bool(ledger.overdue) or bool(stats.speaker_guess)


def promise_entry(row: Row) -> dict:
    p = row.promise
    return {"id": str(row.number), "to_whom": p.to_whom, "what": p.what, "due": p.due,
            "due_phrase": p.due_phrase or None, "source_quote": p.source_quote, "sent_on": p.sent_on or None,
            "state": p.state, "evidence_quote": p.evidence_quote or None}


def overdue_entry(row: Row) -> dict:
    p = row.promise
    return {"id": str(row.number), "to_whom": p.to_whom, "what": p.what, "due": p.due,
            "days_overdue": row.days_overdue}


def summary_line(ledger: Ledger) -> str:
    as_of = ledger.as_of.isoformat()
    if not ledger.rows:
        return f"No commitments found (as of {as_of})."
    open_count, done = ledger.count("open") + ledger.count("unclear"), ledger.count("done")
    noun = "promise" if open_count == 1 else "promises"
    tail = f", {done} done" if done else ""
    return f"{open_count} open {noun}, {len(ledger.overdue)} overdue{tail} (as of {as_of})."


def build(ledger: Ledger, stats: Stats) -> dict:
    entries = [promise_entry(r) for r in ledger.rows]
    level = confidence(ledger, stats)
    return {"summary": summary_line(ledger), "items": entries, "promises": entries,
            "overdue": [overdue_entry(r) for r in ledger.overdue], "confidence": level,
            "needs_human": needs_human(ledger, stats, level), "as_of": ledger.as_of.isoformat(),
            "left_out": stats.dropped, "capped": ledger.capped, "messages_read": stats.messages}
