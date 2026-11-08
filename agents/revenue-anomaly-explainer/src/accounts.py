"""Pure: the accounts behind a flagged move, ranked by contribution, with only the facts the data shows.

churned -> customers whose MRR went to 0 that day (the cancellation reason verbatim, when the tools returned one);
new -> new customers; expansion / contraction -> customers whose MRR changed (old -> new monthly amounts); net new ->
every mover; failures -> customers of that day's failed payments (with decline codes). Top 5 by contribution,
then "and N more" with the remaining total. share_of_move = contribution / total of the move (one decimal);
one account at >= 50% is "concentrated in one account". No cause is ever stated beyond these facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal

from detect import Anomaly
from series import DayMetrics, Move
from subs import Customer, Failure, Sub

TOP_N = 5
CONCENTRATION_SHARE = Decimal(50)
_KIND_OF = {"new_mrr": ("new",), "churned_mrr": ("churned",), "expansion_mrr": ("expansion",),
            "contraction_mrr": ("contraction",), "net_new_mrr": ("new", "churned", "expansion", "contraction")}


@dataclass(frozen=True)
class AccountMove:
    customer_id: str
    customer: str              # a name or email from tool output, else the customer id
    contribution: int          # minor units
    share_of_move: str         # "93.8"
    fact: str
    link: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping) -> AccountMove:
        return AccountMove(customer_id=str(data.get("customer_id") or ""), customer=str(data.get("customer") or ""),
                           contribution=int(data.get("contribution") or 0),
                           share_of_move=str(data.get("share_of_move") or "0.0"), fact=str(data.get("fact") or ""),
                           link=str(data.get("link") or ""))


@dataclass(frozen=True)
class Behind:
    """The accounts behind one anomaly."""

    accounts: tuple[AccountMove, ...]
    more_count: int = 0
    more_total: int = 0

    @property
    def concentrated(self) -> bool:
        return bool(self.accounts) and Decimal(self.accounts[0].share_of_move) >= CONCENTRATION_SHARE

    def to_dict(self) -> dict:
        return {"accounts": [a.to_dict() for a in self.accounts], "more_count": self.more_count,
                "more_total": self.more_total}

    @staticmethod
    def from_dict(data: Mapping | None) -> Behind:
        data = data or {}
        return Behind(tuple(AccountMove.from_dict(a) for a in data.get("accounts") or ()),
                      int(data.get("more_count") or 0), int(data.get("more_total") or 0))


@dataclass(frozen=True)
class _Raw:
    customer_id: str
    contribution: int
    fact: str
    link: str = ""


def share(part: int, total: int) -> str:
    if total <= 0:
        return "0.0"
    return str((Decimal(part) * 100 / Decimal(total)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def money(minor: int) -> str:
    return f"{Decimal(minor) / 100:,.2f}"


def behind(anomaly: Anomaly, day: DayMetrics, subs: Sequence[Sub], failures: Sequence[Failure],
           names: Mapping[str, Customer]) -> Behind:
    raw = _failure_rows(day, failures) if anomaly.metric.startswith("failed_") else _move_rows(anomaly, day, subs)
    ranked = sorted(raw, key=lambda r: (-r.contribution, r.customer_id))
    total = sum(r.contribution for r in ranked)
    top = ranked[:TOP_N]
    rest = ranked[TOP_N:]
    accounts = tuple(AccountMove(r.customer_id, _name(r.customer_id, names), r.contribution,
                                 share(r.contribution, total), r.fact, r.link) for r in top)
    return Behind(accounts, len(rest), sum(r.contribution for r in rest))


def ids_to_name(anomalies: Sequence[Anomaly], days: Mapping[tuple[str, str], DayMetrics],
                failures: Sequence[Failure]) -> set[str]:
    """Customer ids that will be named: the movers and failed-payment customers of every flagged day."""
    out: set[str] = set()
    for a in anomalies:
        day = days.get((a.day, a.currency))
        if day is None:
            continue
        if a.metric.startswith("failed_"):
            out |= {f.customer_id for f in failures if f.id in day.failure_ids}
        else:
            out |= {m.customer_id for m in day.moves if m.kind in _KIND_OF.get(a.metric, ())}
    return out


def _name(customer_id: str, names: Mapping[str, Customer]) -> str:
    found = names.get(customer_id)
    return found.label if found else customer_id


def _move_rows(anomaly: Anomaly, day: DayMetrics, subs: Sequence[Sub]) -> list[_Raw]:
    kinds = _KIND_OF.get(anomaly.metric, ())
    return [_move_row(m, day, subs) for m in day.moves if m.kind in kinds]


def _move_row(move: Move, day: DayMetrics, subs: Sequence[Sub]) -> _Raw:
    contribution = abs(move.after - move.before)
    if move.kind == "new":
        return _Raw(move.customer_id, contribution, f"new subscription, {money(move.after)}/month")
    if move.kind == "churned":
        ended = _ended_that_day(move.customer_id, day, subs)
        reason = next((s.cancellation_reason for s in ended if s.cancellation_reason), None)
        link = next((s.link for s in ended if s.link), "")
        fact = f'canceled, reason: "{reason}"' if reason else "canceled (no reason recorded)"
        return _Raw(move.customer_id, contribution, fact, link or "")
    return _Raw(move.customer_id, contribution,
                f"plan changed {money(move.before)} -> {money(move.after)}/month")


def _ended_that_day(customer_id: str, day: DayMetrics, subs: Sequence[Sub]) -> list[Sub]:
    return [s for s in subs if s.customer_id == customer_id and s.ends_at is not None
            and s.ends_at.date() == day.day]


def _failure_rows(day: DayMetrics, failures: Sequence[Failure]) -> list[_Raw]:
    todays = [f for f in failures if f.id in day.failure_ids]
    rows: list[_Raw] = []  # a local accumulator
    for customer in sorted({f.customer_id for f in todays}):
        own = [f for f in todays if f.customer_id == customer]
        codes = sorted({f.decline_code for f in own if f.decline_code})
        count = f"{len(own)} failed payment{'s' if len(own) != 1 else ''}"
        fact = f"{count}, decline code: {', '.join(codes)}" if codes else f"{count}, no decline code"
        rows.append(_Raw(customer, sum(f.amount_minor for f in own), fact, next((f.link for f in own if f.link), "")
                         or ""))
    return rows
