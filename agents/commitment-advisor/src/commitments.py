"""Pure: the commitments block (after a `--- commitments` line) and the portfolio it forms over the usage span.

A savings plan's on-demand-equivalent cover is `hourly_commit / (1 - d)` for its term's (assumed or quoted)
discount; an RI's is `count * on_demand_hourly` when the paste gives that rate (else `hourly_commit / (1 - d)`
when given), otherwise it is listed as not modelled. Compute savings plans cover the `compute` scope; RIs and
instance savings plans cover their family/region scope when the usage has one, else `compute`."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date

import tabular
from constants import COMPUTE, EXPIRED_LOOKBACK_DAYS, EXPIRY_WINDOW_DAYS

KINDS = frozenset({"ri", "sp_compute", "sp_ec2"})
NOT_MODELLED = "{id}: RI not modelled: no on-demand rate given (add on_demand_hourly or hourly_commit)."
NOT_NO_UPFRONT = "{id}: payment {payment} is treated as no-upfront for the utilization maths (v1)."
STARTED_LATE = "{id}: starts after the data begins, so it is not counted as existing cover."


@dataclass(frozen=True)
class Commitment:
    id: str
    kind: str
    family: str
    region: str
    hourly_commit: float | None
    count: float | None
    on_demand_hourly: float | None
    term_years: int
    start: date | None
    end: date
    payment: str = "no_upfront"


@dataclass(frozen=True)
class Parsed:
    items: tuple[Commitment, ...] = ()
    notes: tuple[str, ...] = ()
    dropped: int = 0


@dataclass(frozen=True)
class Held:
    commitment: Commitment
    scope: str
    od_hourly: float
    discount: float
    whole_span: bool        # active over the whole usage span: counts as existing cover E
    expiring: bool          # ends within 90 days of the data end (or ended within the last 30)
    days_left: int


@dataclass(frozen=True)
class Portfolio:
    held: tuple[Held, ...] = ()
    notes: tuple[str, ...] = ()

    def cover(self, scope: str) -> float:
        return sum(h.od_hourly for h in self.held if h.scope == scope and h.whole_span)


def _term(text: str) -> int:
    digit = re.search(r"\d", text or "")
    return 3 if digit and digit.group(0) == "3" else 1


def _row(row, table: tabular.Table) -> Commitment | None:
    get = lambda *names: tabular.cell(row, table.column(*names))
    kind = get("type", "kind").casefold()
    end = tabular.day(get("end_date", "end", "expires", "expiry"))
    ident = get("id", "commitment_id", "name")
    if kind not in KINDS or end is None or not ident:
        return None
    return Commitment(ident, kind, get("family", "instance_family"), get("region"),
                      tabular.number(get("hourly_commit", "commitment_hourly")), tabular.number(get("count")),
                      tabular.number(get("on_demand_hourly", "ondemand_hourly")), _term(get("term_years", "term")),
                      tabular.day(get("start_date", "start")), end, get("payment").casefold() or "no_upfront")


def parse(text: str) -> Parsed:
    table = tabular.read(text)
    items, notes, dropped = [], [], 0
    for row in table.rows:
        c = _row(row, table)
        if c is None:
            dropped += 1
            continue
        items.append(c)
        if c.payment not in ("no_upfront", "no upfront"):
            notes.append(NOT_NO_UPFRONT.format(id=c.id, payment=c.payment))
    return Parsed(tuple(items), tuple(notes), dropped)


def od_equivalent(c: Commitment, discounts: Mapping[int, float]) -> float | None:
    if c.kind == "ri" and c.count is not None and c.on_demand_hourly is not None:
        return c.count * c.on_demand_hourly
    if c.hourly_commit is None:
        return None
    return c.hourly_commit / (1 - discounts[c.term_years])


def scope_of(c: Commitment, scopes: Collection[str]) -> str:
    if c.kind == "sp_compute":
        return COMPUTE
    key = f"{c.family}/{c.region}" if c.region else c.family
    return key if key in scopes else COMPUTE


def portfolio(parsed: Parsed, span_start: date, data_end: date, scopes: Collection[str],
              discounts: Mapping[int, float]) -> Portfolio:
    held, notes = [], list(parsed.notes)
    for c in parsed.items:
        od = od_equivalent(c, discounts)
        if od is None:
            notes.append(NOT_MODELLED.format(id=c.id))
            continue
        days_left = (c.end - data_end).days
        if days_left < -EXPIRED_LOOKBACK_DAYS:
            continue
        late = c.start is not None and c.start > span_start
        if late:
            notes.append(STARTED_LATE.format(id=c.id))
        held.append(Held(c, scope_of(c, scopes), od, discounts[c.term_years], days_left >= 0 and not late,
                         days_left <= EXPIRY_WINDOW_DAYS, days_left))
    return Portfolio(tuple(held), tuple(notes))
