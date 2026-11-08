"""Pure: order the table's periods, drop a partial last period, and pick the current and the comparison
period in code. `compare` is "previous" (the period before, the default) or "month" (the same week last
month: four weeks back)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from table import Table, is_blank

PARTIAL_BLANK_SHARE = 0.5
MONTH_BACK_DAYS = 28
MONTH_BACK_PERIODS = 4
_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_SLASH_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_ISO_WEEK = re.compile(r"^(\d{4})-?W(\d{1,2})$", re.IGNORECASE)
_WEEK = re.compile(r"^(?:week|wk|w)\s*#?\s*(\d{1,2})(?:\D+(\d{4}))?$", re.IGNORECASE)
_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                            "dec"))}
_MONTH = re.compile(r"^([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2},\s*)?(\d{4})$")
_DAY_MONTH = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{4})$")


@dataclass(frozen=True)
class Selection:
    order: tuple[int, ...]            # data-row indexes, oldest first, partial last row removed
    current: int | None
    previous: int | None
    excluded_partial: str | None      # the partial row's period label
    compare: str
    compare_note: str = ""

    @property
    def prior(self) -> tuple[int, ...]:
        """Rows before the current one, oldest first."""
        if self.current is None:
            return ()
        return self.order[: self.order.index(self.current)]


def period_key(label: str) -> tuple[int, date | None, float] | None:
    """A sortable key for a period label; (kind, date, ordinal). None when the label is not a period."""
    text = (label or "").strip()
    found = _date_of(text)
    if found is not None:
        return (0, found, float(found.toordinal()))
    week = _ISO_WEEK.match(text)
    if week:
        return _week_key(int(week.group(1)), int(week.group(2)))
    week = _WEEK.match(text)
    if week:
        return _week_key(int(week.group(2) or 0), int(week.group(1)))
    if re.fullmatch(r"\d{1,6}", text):
        return (2, None, float(text))
    return None


def _week_key(year: int, week: int) -> tuple[int, date | None, float] | None:
    if not 1 <= week <= 53:
        return None
    if year:
        try:
            return (0, date.fromisocalendar(year, week, 1), float(date.fromisocalendar(year, week, 1).toordinal()))
        except ValueError:
            return None
    return (1, None, float(week))


def _date_of(text: str) -> date | None:
    try:
        iso = _ISO_DATE.match(text)
        if iso:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        slash = _SLASH_DATE.match(text)
        if slash:
            return date(int(slash.group(3)), int(slash.group(1)), int(slash.group(2)))
        month = _MONTH.match(text)
        if month and month.group(1).casefold() in _MONTHS:
            day = int(month.group(2).strip(", ")) if month.group(2) else 1
            return date(int(month.group(3)), _MONTHS[month.group(1).casefold()], day)
        dm = _DAY_MONTH.match(text)
        if dm and dm.group(2).casefold() in _MONTHS:
            return date(int(dm.group(3)), _MONTHS[dm.group(2).casefold()], int(dm.group(1)))
    except ValueError:
        return None
    return None


def select(table: Table, compare: str = "previous") -> Selection:
    labels = [r[table.period.index] for r in table.rows]
    order = _ordered(labels)
    excluded = None
    if order and _is_partial(table, order[-1]):
        excluded = labels[order[-1]] or "the last row"
        order = order[:-1]
    if not order:
        return Selection((), None, None, excluded, compare)
    current = order[-1]
    previous, note = _comparison(labels, order, compare)
    return Selection(tuple(order), current, previous, excluded, compare, note)


def _ordered(labels: list[str]) -> list[int]:
    """Oldest first: by period when every label reads as the same kind of period, else in table order."""
    indexes = [i for i, label in enumerate(labels) if not is_blank(label)]
    keys = [period_key(labels[i]) for i in indexes]
    if keys and all(k is not None for k in keys) and len({k[0] for k in keys}) == 1:
        return [i for _, i in sorted(zip((k[2] for k in keys), indexes))]
    return indexes


def _is_partial(table: Table, row: int) -> bool:
    cells = [table.rows[row][c.index] for c in table.metrics]
    return sum(1 for c in cells if is_blank(c)) > PARTIAL_BLANK_SHARE * len(cells)


def _comparison(labels: list[str], order: list[int], compare: str) -> tuple[int | None, str]:
    if len(order) < 2:
        return None, ""
    if compare != "month":
        return order[-2], ""
    current_key = period_key(labels[order[-1]])
    if current_key is not None and current_key[1] is not None:
        target = current_key[1] - timedelta(days=MONTH_BACK_DAYS)
        dated = [(period_key(labels[i]), i) for i in order[:-1]]
        same = [i for k, i in dated if k is not None and k[1] == target]
        if same:
            return same[0], ""
        return None, "No period four weeks before the current one, so there is no month-ago comparison."
    back = len(order) - 1 - MONTH_BACK_PERIODS
    if back < 0:
        return None, "Fewer than five periods, so there is no month-ago comparison."
    return order[back], ""
