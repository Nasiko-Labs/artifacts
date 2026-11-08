"""Pure: the daily revenue-movement series per currency (UTC days), computed in code from subscriptions, plan changes
and failed payments, plus the reconcile self-check.

For each day `d`, per customer: `c_start` = total MRR at 00:00 of `d`, `c_end` = at 24:00.
- new = sum c_end where c_start = 0 < c_end; churned = sum c_start where c_start > 0 = c_end;
- expansion / contraction = positive / negative differences where both are > 0;
- net_new = new + expansion - contraction - churned;
- failed_count / failed_amount = failed payment attempts that day (already deduplicated by payment id).
Currencies are never summed. Without the events read, expansion, contraction and failed payments are "not computed"."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise

import mrr
from subs import Change, Failure, Sub

METRICS = ("new_mrr", "churned_mrr", "expansion_mrr", "contraction_mrr", "net_new_mrr", "failed_count",
           "failed_amount")
CHANGE_METRICS = frozenset({"expansion_mrr", "contraction_mrr"})
EVENT_METRICS = CHANGE_METRICS | {"failed_count", "failed_amount"}  # not computed without the events read


@dataclass(frozen=True)
class Move:
    """One customer's MRR change over one day (minor units)."""

    customer_id: str
    before: int
    after: int

    @property
    def kind(self) -> str:
        if self.before == 0:
            return "new"
        if self.after == 0:
            return "churned"
        return "expansion" if self.after > self.before else "contraction"


@dataclass(frozen=True)
class DayMetrics:
    day: date
    currency: str
    mrr_start: int = 0
    mrr_end: int = 0
    new_mrr: int = 0
    churned_mrr: int = 0
    expansion_mrr: int = 0
    contraction_mrr: int = 0
    failed_count: int = 0
    failed_amount: int = 0
    moves: tuple[Move, ...] = ()
    failure_ids: tuple[str, ...] = ()
    mrr_known: bool = True

    @property
    def net_new_mrr(self) -> int:
        return self.new_mrr + self.expansion_mrr - self.contraction_mrr - self.churned_mrr

    def value(self, metric: str) -> int:
        return getattr(self, metric)

    def to_dict(self) -> dict:
        data = asdict(self)
        return {**data, "day": self.day.isoformat(), "moves": [asdict(m) for m in self.moves],
                "failure_ids": list(self.failure_ids)}

    @staticmethod
    def from_dict(data: Mapping) -> DayMetrics:
        ints = ("mrr_start", "mrr_end", "new_mrr", "churned_mrr", "expansion_mrr", "contraction_mrr", "failed_count",
                "failed_amount")
        return DayMetrics(day=date.fromisoformat(str(data["day"])), currency=str(data["currency"]),
                          **{k: int(data.get(k) or 0) for k in ints},
                          moves=tuple(Move(str(m["customer_id"]), int(m["before"]), int(m["after"]))
                                      for m in data.get("moves") or ()),
                          failure_ids=tuple(str(i) for i in data.get("failure_ids") or ()),
                          mrr_known=bool(data.get("mrr_known", True)))


@dataclass(frozen=True)
class Series:
    """One currency's days, oldest first, and the first day the account had any data in it."""

    currency: str
    days: tuple[DayMetrics, ...]
    history_start: date | None = None
    changes_known: bool = True
    unsupported_items: int = 0
    notes: tuple[str, ...] = field(default=())

    def day(self, d: date) -> DayMetrics | None:
        return next((m for m in self.days if m.day == d), None)


def midnight(d: date) -> datetime:
    return datetime.combine(d, time(0), tzinfo=UTC)


def day_range(first: date, last: date) -> tuple[date, ...]:
    return tuple(first + timedelta(days=i) for i in range((last - first).days + 1))


def build(subs: Sequence[Sub], changes: Sequence[Change], failures: Sequence[Failure], first: date, last: date, *,
          changes_known: bool = True) -> tuple[Series, ...]:
    """One series per currency (sorted), covering `first`..`last` inclusive."""
    currencies = sorted({s.currency for s in subs} | {f.currency for f in failures})
    return tuple(_series(c, [s for s in subs if s.currency == c], changes, [f for f in failures if f.currency == c],
                         first, last, changes_known) for c in currencies)


def _series(currency: str, subs: Sequence[Sub], changes: Sequence[Change], failures: Sequence[Failure],
            first: date, last: date, changes_known: bool) -> Series:
    days: list[DayMetrics] = []  # a local accumulator, returned as a tuple
    start = mrr.by_customer(subs, changes, midnight(first))
    for d in day_range(first, last):
        end = mrr.by_customer(subs, changes, midnight(d + timedelta(days=1)))
        days.append(_day(d, currency, start, end, [f for f in failures if f.at.date() == d], changes_known))
        start = end
    return Series(currency, tuple(days), _history_start(subs, failures), changes_known, mrr.unsupported_items(subs))


def _day(d: date, currency: str, start: Mapping[str, int], end: Mapping[str, int], failures: Sequence[Failure],
         changes_known: bool) -> DayMetrics:
    moves = tuple(Move(c, start.get(c, 0), end.get(c, 0)) for c in sorted(set(start) | set(end))
                  if start.get(c, 0) != end.get(c, 0))
    by_kind = {k: [m for m in moves if m.kind == k] for k in ("new", "churned", "expansion", "contraction")}
    expansion = sum(m.after - m.before for m in by_kind["expansion"])
    contraction = sum(m.before - m.after for m in by_kind["contraction"])
    return DayMetrics(day=d, currency=currency, mrr_start=sum(start.values()), mrr_end=sum(end.values()),
                      new_mrr=sum(m.after for m in by_kind["new"]), churned_mrr=sum(m.before for m in by_kind["churned"]),
                      expansion_mrr=expansion if changes_known else 0,
                      contraction_mrr=contraction if changes_known else 0,
                      failed_count=len(failures), failed_amount=sum(f.amount_minor for f in failures), moves=moves,
                      failure_ids=tuple(f.id for f in failures))


def _history_start(subs: Sequence[Sub], failures: Sequence[Failure]) -> date | None:
    dates = [s.created_at.date() for s in subs] + [f.at.date() for f in failures]
    return min(dates) if dates else None


def reconciles(days: Sequence[DayMetrics]) -> bool:
    """The code self-check: each day's start + net new equals its end, and each day's end equals the next day's
    start. Days built from pasted rows carry no MRR and are not checked."""
    for prev, nxt in pairwise(days):
        if prev.mrr_known and nxt.mrr_known and prev.mrr_end != nxt.mrr_start:
            return False
    return all(not m.mrr_known or m.mrr_start + m.net_new_mrr == m.mrr_end for m in days)
