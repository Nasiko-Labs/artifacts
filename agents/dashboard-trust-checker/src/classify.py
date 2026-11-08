"""Pure: the classification rules (PLAN section 9), all in code. Each inventory row becomes a Dashboard with
a primary status (broken > stale > unused > healthy; unknown when the row says nothing), every flag that
applies, a proposed action and the reasons. Chat complaints only ever upgrade keep to review."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from headers import HeaderMap
from models import Complaints, Dashboard
from parse import DEFAULT_STALE_DAYS, DEFAULT_UNUSED_DAYS
from table import Table
from values import parse_cadence, parse_count, parse_date

CADENCE_FACTOR = 2
MIN_VIEWERS_TO_FIX = 5
COMPLAINTS_TO_QUESTION = 2
MAX_QUOTED_CHARS = 80
_BROKEN = re.compile(r"\b(?:error\w*|fail(?:ed|ing|s|ure)?|broken|time ?outs?|timed out|invalid)\b|"
                     r"\b0 rows\b|\bno data\b", re.IGNORECASE)
_NO_ERROR = frozenset({"", "-", "--", "none", "n/a", "na", "null", "0", "ok", "false", "no", "nil"})
_PRECEDENCE = ("broken", "stale", "unused", "healthy")
_SORT_RANK = {"broken": 0, "stale": 1, "unused": 2, "unknown": 3, "healthy": 4}


@dataclass(frozen=True)
class Thresholds:
    stale_days: int | None = None      # the user's explicit threshold; None: 2x cadence, else the default
    unused_days: int | None = None


@dataclass(frozen=True)
class _Row:
    name: str
    owner: str | None
    last_refresh: date | None
    viewers: int | None
    last_viewed: date | None
    status_text: str
    error_text: str
    cadence: int | None


DEFAULT_LIMITS = Thresholds()


def classify(table: Table, header_map: HeaderMap, today: date, limits: Thresholds = DEFAULT_LIMITS
             ) -> tuple[Dashboard, ...]:
    rows = [_row(cells, header_map.fields) for cells in table.rows]
    found = [_dashboard(r, today, limits) for r in rows if r.name]
    return sort(found)


def sort(dashboards: Sequence[Dashboard]) -> tuple[Dashboard, ...]:
    """Broken first (most viewers first), then stale (most viewers first), then unused (fewest first)."""
    def key(d: Dashboard) -> tuple:
        viewers = d.viewers if d.viewers is not None else 0
        return _SORT_RANK[d.status], viewers if d.status == "unused" else -viewers, d.name.casefold()
    return tuple(sorted(dashboards, key=key))


def apply_complaints(dashboards: Sequence[Dashboard], evidence: Mapping[str, Complaints]) -> tuple[Dashboard, ...]:
    return tuple(_with_complaints(d, evidence.get(d.name)) for d in dashboards)


def _with_complaints(dashboard: Dashboard, found: Complaints | None) -> Dashboard:
    if found is None or found.count == 0:
        return dashboard
    plural = "s" if found.count != 1 else ""
    reasons = (f"{found.count} chat complaint{plural} in the last 30 days",)
    if found.count < COMPLAINTS_TO_QUESTION:
        return dashboard.with_complaints(found, reasons, (), dashboard.action)
    action = "review" if dashboard.action == "keep" else dashboard.action
    return dashboard.with_complaints(found, reasons, ("trust_questioned",), action)


def _cell(cells: Sequence[str], fields: Mapping[str, int], name: str) -> str:
    index = fields.get(name)
    return cells[index].strip() if index is not None and index < len(cells) else ""


def _row(cells: Sequence[str], fields: Mapping[str, int]) -> _Row:
    def get(name: str) -> str:
        return _cell(cells, fields, name)
    return _Row(name=get("name"), owner=get("owner") or None, last_refresh=parse_date(get("last_refresh")),
                viewers=parse_count(get("viewers")), last_viewed=parse_date(get("last_viewed")),
                status_text=get("status"), error_text=get("error"), cadence=parse_cadence(get("cadence")))


def _fresh_limit(row: _Row, limits: Thresholds) -> tuple[int, str]:
    if limits.stale_days is not None:
        return limits.stale_days, "your threshold"
    if row.cadence is not None:
        return row.cadence * CADENCE_FACTOR, "twice the stated cadence"
    return DEFAULT_STALE_DAYS, "the default"


def _dashboard(row: _Row, today: date, limits: Thresholds) -> Dashboard:
    age = (today - row.last_refresh).days if row.last_refresh else None
    flags, reasons = _flags(row, today, age, limits)
    has_info = row.last_refresh is not None or bool(row.status_text) or bool(row.error_text)
    if not flags and has_info:
        flags, reasons = ("healthy",), _healthy_reasons(row, age)
    status = next((s for s in _PRECEDENCE if s in flags), "unknown")
    if status == "unknown":
        reasons = ("no refresh date, status or viewer count in the inventory",) if row.viewers is None else (
            f"{row.viewers} viewers, but no refresh date or status to judge it by",)
    action, priority = _action(status, flags, row.viewers)
    return Dashboard(name=row.name, owner=row.owner, status=status, flags=flags, viewers=row.viewers,
                     last_refresh=row.last_refresh, age_days=age, action=action, priority=priority,
                     reasons=reasons)


def _flags(row: _Row, today: date, age: int | None, limits: Thresholds) -> tuple[tuple[str, ...], tuple[str, ...]]:
    flags: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    broken = _broken_reason(row)
    if broken:
        flags, reasons = (*flags, "broken"), (*reasons, broken)
    limit, why = _fresh_limit(row, limits)
    if not broken and age is not None and age > limit:
        flags, reasons = (*flags, "stale"), (*reasons, f"last refresh {age} days ago (limit {limit} days, {why})")
    unused = _unused_reason(row, today, limits)
    if unused:
        flags, reasons = (*flags, "unused"), (*reasons, unused)
    return flags, reasons


def _broken_reason(row: _Row) -> str | None:
    if row.error_text and row.error_text.casefold() not in _NO_ERROR:
        return f"error: {_short(row.error_text)}"
    if row.status_text and _BROKEN.search(row.status_text):
        return f"status: {_short(row.status_text)}"
    return None


def _unused_reason(row: _Row, today: date, limits: Thresholds) -> str | None:
    if row.viewers == 0:
        return "0 viewers"
    unused_days = limits.unused_days or DEFAULT_UNUSED_DAYS
    if row.last_viewed is not None and (today - row.last_viewed).days > unused_days:
        return f"last viewed {(today - row.last_viewed).days} days ago (limit {unused_days} days)"
    return None


def _healthy_reasons(row: _Row, age: int | None) -> tuple[str, ...]:
    parts = []
    if age is not None:
        parts.append(f"refreshed {age} days ago")
    if row.status_text:
        parts.append(f"status: {_short(row.status_text)}")
    if row.viewers is not None:
        parts.append(f"{row.viewers} viewers")
    return ("; ".join(parts),)


def _action(status: str, flags: Sequence[str], viewers: int | None) -> tuple[str, str | None]:
    if "unused" in flags and ("broken" in flags or "stale" in flags):
        return "retire", None
    if status == "broken":
        return ("fix", "high") if viewers is None or viewers >= MIN_VIEWERS_TO_FIX else ("review", None)
    if status == "stale":
        return ("fix", "normal") if viewers is None or viewers >= MIN_VIEWERS_TO_FIX else ("review", None)
    if status == "healthy":
        return "keep", None
    return "review", None


def _short(text: str) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= MAX_QUOTED_CHARS else clean[:MAX_QUOTED_CHARS - 1] + "…"
