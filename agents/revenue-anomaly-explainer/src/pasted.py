"""Pure: the fallback when no payments tool can be read. The user pastes daily rows

    date, new, churned, expansion, contraction, failed_count, failed_amount

(amounts in major units, e.g. 1250.00; >= 14 days before the checked day) and the same anomaly math runs on them.
There are no accounts and no MRR level in pasted rows, so accounts are "not available" and the money floor is the
fixed 50.00. A header line and blank lines are skipped; a row that cannot be read is counted, never guessed."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from series import DayMetrics, Series

DEFAULT_CURRENCY = "USD"
MIN_ROWS = 2
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONEY_JUNK = re.compile(r"[\s$€£¥,]")
_CELLS = 7


@dataclass(frozen=True)
class Pasted:
    series: Series
    bad_rows: int = 0


def find(text: str) -> Pasted | None:
    """The pasted series, or None when the message holds fewer than two readable rows."""
    rows: dict[date, DayMetrics] = {}  # a local accumulator; one row per day, the last one wins
    bad = 0
    for line in (text or "").splitlines():
        cells = [c.strip() for c in re.split(r"[,\t;]", line.strip())]
        if len(cells) != _CELLS or not _DATE.match(cells[0]):
            continue
        row = _row(cells)
        if row is None:
            bad += 1
            continue
        rows[row.day] = row
    if len(rows) < MIN_ROWS:
        return None
    days = tuple(rows[d] for d in sorted(rows))
    return Pasted(Series(DEFAULT_CURRENCY, days, history_start=days[0].day), bad)


def _row(cells: list[str]) -> DayMetrics | None:
    try:
        day = date.fromisoformat(cells[0])
    except ValueError:
        return None
    amounts = [_minor(c) for c in (cells[1], cells[2], cells[3], cells[4], cells[6])]
    count = cells[5].replace(",", "")
    if any(a is None or a < 0 for a in amounts) or not count.isdigit():
        return None
    new, churned, expansion, contraction, failed_amount = amounts
    return DayMetrics(day=day, currency=DEFAULT_CURRENCY, new_mrr=new, churned_mrr=churned, expansion_mrr=expansion,
                      contraction_mrr=contraction, failed_count=int(count), failed_amount=failed_amount,
                      mrr_known=False)


def _minor(value: str) -> int | None:
    text = _MONEY_JUNK.sub("", value)
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
