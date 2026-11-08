"""Pure: read one cell. Numbers (with $, €, £, currency codes, thousands separators, %, K/M/B suffixes and
accounting parentheses), period labels (months, ISO dates, quarters, weeks, years) and a sort key for
dated labels. Nothing here guesses beyond these patterns."""

from __future__ import annotations

import re
from dataclasses import dataclass

MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|"
          "jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec")
MONTH_INDEX = {name: n for n, names in enumerate(
    (("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")), start=1) for name in names}
SUFFIX_SCALE = {"k": 1e3, "m": 1e6, "mn": 1e6, "b": 1e9, "bn": 1e9}

_NUMBER = re.compile(
    r"^(?P<open>\()?\s*(?P<sign>[-−+])?\s*(?P<cur>[$€£]|usd|eur|gbp|inr)?\s*(?P<sign2>[-−])?\s*"
    r"(?P<num>\d[\d,]*(?:\.\d+)?|\.\d+)\s*(?P<suf>%|k|mn|m|bn|b)?\s*(?P<cur2>usd|eur|gbp|inr)?\s*(?P<close>\))?$",
    re.IGNORECASE)
_TIME = (
    re.compile(rf"^(?:{MONTHS})\.?(?:\s+\d{{1,2}}(?:\s*[-–]\s*\d{{1,2}})?)?(?:[,\s'-]*\d{{2,4}})?$", re.IGNORECASE),
    re.compile(r"^\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?$"),
    re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$"),
    re.compile(r"^(?:q[1-4][\s'-]*(?:\d{2}|\d{4})?|\d{4}[\s-]*q[1-4]|h[12]\s*\d{4}|fy\s*'?\d{2,4})$", re.IGNORECASE),
    re.compile(r"^(?:week|wk|w|day|month|period)\s*\d{1,3}$", re.IGNORECASE),
)
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_ISO = re.compile(r"^(\d{4})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?$")
_MONTH_YEAR = re.compile(rf"^({MONTHS})\.?[\s,'-]*(\d{{4}})$", re.IGNORECASE)
_QUARTER = re.compile(r"^q([1-4])[\s'-]*(\d{4})$|^(\d{4})[\s-]*q([1-4])$", re.IGNORECASE)
_PARTIAL = re.compile(rf"(?:{MONTHS})\.?\s+\d{{1,2}}\s*[-–]\s*\d{{1,2}}|\b(?:partial|mtd|qtd|to date|so far|"
                      r"in progress)\b", re.IGNORECASE)
TIME_HEADER = re.compile(r"\b(?:year|month|date|week|quarter|period|day|time|fy)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Number:
    value: float
    pct: bool = False
    currency: str = ""
    suffix_scale: float = 1.0


def parse_number(cell: str) -> Number | None:
    text = (cell or "").strip().replace(" ", " ")
    match = _NUMBER.match(text)
    if not match or bool(match["open"]) != bool(match["close"]):
        return None
    try:
        value = float(match["num"].replace(",", ""))
    except ValueError:
        return None
    suffix = (match["suf"] or "").casefold()
    scale = SUFFIX_SCALE.get(suffix, 1.0)
    negative = bool(match["open"]) or (match["sign"] or match["sign2"] or "") in ("-", "−")
    currency = (match["cur"] or match["cur2"] or "").upper()
    return Number(-value * scale if negative else value * scale, suffix == "%", currency, scale)


def is_time(cell: str, header: str = "") -> bool:
    text = " ".join((cell or "").split())
    if any(p.match(text) for p in _TIME):
        return True
    return bool(_YEAR.match(text)) and bool(TIME_HEADER.search(header))


def time_key(cell: str) -> tuple[int, int, int] | None:
    """(year, month, day) for labels that carry a year; None for anything else (left in table order)."""
    text = " ".join((cell or "").split())
    if m := _ISO.match(text):
        return int(m[1]), int(m[2]), int(m[3] or 1)
    if m := _MONTH_YEAR.match(text):
        return int(m[2]), MONTH_INDEX[m[1].casefold()], 1
    if m := _QUARTER.match(text):
        quarter, year = (m[1], m[2]) if m[1] else (m[4], m[3])
        return int(year), (int(quarter) - 1) * 3 + 1, 1
    if _YEAR.match(text):
        return int(text), 1, 1
    return None


def looks_partial(label: str) -> bool:
    return bool(_PARTIAL.search(label or ""))


def month_names() -> tuple[str, ...]:
    return tuple(MONTH_INDEX)
