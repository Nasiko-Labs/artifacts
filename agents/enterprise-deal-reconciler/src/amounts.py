"""Pure: money, period and date helpers. All arithmetic of the agent happens here or in compare.py."""

from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

ZERO_DECIMAL = frozenset({"BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF", "UGX", "VND",
                          "VUV", "XAF", "XOF", "XPF"})
MONTHS_IN = {"month": 1, "quarter": 3, "year": 12}
SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR"}
_NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SLASH_DATE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")
_MONTH_NAMES = {name.casefold(): n for n, name in enumerate(calendar.month_name) if name}
_MONTH_NAMES.update({name.casefold(): n for n, name in enumerate(calendar.month_abbr) if name})
_MONTH_NAMES["sept"] = 9
_MONTH_WORD = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))
_DAY_MONTH_YEAR = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_WORD})\.?,?\s+(\d{{4}})\b", re.IGNORECASE)
_MONTH_DAY_YEAR = re.compile(rf"\b({_MONTH_WORD})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE)
_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                 "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "eighteen": 18, "twenty-four": 24,
                 "thirty": 30, "thirty-six": 36, "forty-five": 45, "sixty": 60, "ninety": 90}


def decimal_of(raw: Any) -> Decimal | None:
    """A finite Decimal from a number or a numeric string ("1,200.50" allowed), else None."""
    if isinstance(raw, bool) or raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def int_of(raw: Any) -> int | None:
    value = decimal_of(raw)
    if value is None or value != value.to_integral_value():
        return None
    return int(value)


def minor_digits(currency: str | None) -> int:
    return 0 if (currency or "").upper() in ZERO_DECIMAL else 2


def to_major(minor: int, currency: str | None) -> Decimal:
    return Decimal(minor).scaleb(-minor_digits(currency))


def half_up(value: Decimal, places: int = 2) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def money(value: Decimal | None, currency: str | None) -> str:
    if value is None:
        return "unknown"
    places = minor_digits(currency)
    text = f"{half_up(value, places):,.{places}f}"
    return f"{text} {(currency or '').upper()}".strip()


def per_period(amount: Decimal, from_months: Decimal, to_months: Decimal) -> Decimal:
    """An amount charged every `from_months` months, restated per `to_months` months."""
    return amount / from_months * to_months


def numbers_in(text: str) -> frozenset[Decimal]:
    """Every number written in `text` (thousands separators allowed), plus small number words."""
    found = {decimal_of(m.group(0)) for m in _NUMBER.finditer(text)}
    lowered = text.casefold()
    words = {Decimal(v) for w, v in _WORD_NUMBERS.items() if re.search(rf"\b{w}\b", lowered)}
    return frozenset(v.normalize() for v in (found | words) if v is not None)


def dates_in(text: str) -> tuple[frozenset[date], bool]:
    """(the unambiguous dates written in `text`, whether it also writes a day/month-swappable date)."""
    found: set[date] = set()
    ambiguous = False
    for y, m, d in _ISO_DATE.findall(text):
        found |= _maybe(int(y), int(m), int(d))
    for a, b, y in _SLASH_DATE.findall(text):
        first, second = int(a), int(b)
        if first <= 12 and second <= 12 and first != second:
            ambiguous = True
            continue
        found |= _maybe(int(y), second, first) if first > 12 else _maybe(int(y), first, second)
    for d, name, y in _DAY_MONTH_YEAR.findall(text):
        found |= _maybe(int(y), _MONTH_NAMES[name.casefold()], int(d))
    for name, d, y in _MONTH_DAY_YEAR.findall(text):
        found |= _maybe(int(y), _MONTH_NAMES[name.casefold()], int(d))
    return frozenset(found), ambiguous


def _maybe(year: int, month: int, day: int) -> set[date]:
    try:
        return {date(year, month, day)}
    except ValueError:
        return set()


def iso_date(raw: Any) -> date | None:
    """A date from "YYYY-MM-DD" (or a longer ISO timestamp)."""
    if not isinstance(raw, str):
        return None
    match = _ISO_DATE.search(raw)
    return next(iter(_maybe(*(int(g) for g in match.groups()))), None) if match else None


def utc_day(raw: Any) -> date | None:
    """A UTC date from Unix seconds (int or numeric string) or an ISO date / timestamp string."""
    seconds = int_of(raw) if not isinstance(raw, str) or raw.strip().isdigit() else None
    if seconds is not None and seconds > 10_000_000:
        return datetime.fromtimestamp(seconds, tz=UTC).date()
    return iso_date(raw)


def add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def normalise(text: str) -> str:
    """Case- and whitespace-insensitive form used by the verbatim checks."""
    unescaped = text.replace("\\n", " ").replace("\\t", " ").replace('\\"', '"')
    return " ".join(unescaped.split()).casefold()
