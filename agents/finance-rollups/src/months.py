"""Pure: month keys ("2026-03"), UTC month boundaries as [start, end) and the month range a message names, parsed
in code: "last month", "last N months", "Q1 [2026]", "Jan to Mar 2026", "March", "2026-03", "this month", "year to
date". The default is the last 3 full calendar months. A range is clamped to the latest 12 months, and months that
have not started yet are dropped."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

DEFAULT_MONTHS = 3
MAX_MONTHS = 12

_ABBR = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
_MONTH_WORD = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
               r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                 "ten": 10, "eleven": 11, "twelve": 12}
_LAST_MONTH = re.compile(r"\b(?:last|previous|prior)\s+month\b", re.IGNORECASE)
_LAST_N = re.compile(r"\b(?:last|past|previous|prior)\s+(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")\s+months?\b",
                     re.IGNORECASE)
_QUARTER = re.compile(r"\bq([1-4])(?:\s*(?:of\s+)?'?(\d{4}|\d{2}))?\b", re.IGNORECASE)
_THIS_MONTH = re.compile(r"\b(?:this\s+month|month[- ]to[- ]date|mtd)\b", re.IGNORECASE)
_YTD = re.compile(r"\b(?:year[- ]to[- ]date|ytd|this\s+year)\b", re.IGNORECASE)
_ISO_MONTH = re.compile(r"\b(\d{4})-(\d{2})\b(?!-\d)")
_NAMED = re.compile(rf"\b{_MONTH_WORD}\.?(?:\s+(\d{{4}}))?\b", re.IGNORECASE)
_RANGE_JOIN = re.compile(r"^\s*(?:to|through|thru|until|till|-|–|—|and)\s*$", re.IGNORECASE)
_MAY_CONTEXT = re.compile(r"\b(?:for|in|of|from|to|through|thru|until|till|and|since)\s+$", re.IGNORECASE)


def key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def parts(month_key: str) -> tuple[int, int]:
    year, month = month_key.split("-")
    return int(year), int(month)


def shift(month_key: str, n: int) -> str:
    year, month = parts(month_key)
    index = year * 12 + (month - 1) + n
    return key(index // 12, index % 12 + 1)


def of(moment: datetime | date) -> str:
    return key(moment.year, moment.month)


def start_of(month_key: str) -> datetime:
    year, month = parts(month_key)
    return datetime(year, month, 1, tzinfo=UTC)


def bounds(month_key: str) -> tuple[int, int]:
    """Unix seconds [start, end) of the month, UTC."""
    return int(start_of(month_key).timestamp()), int(start_of(shift(month_key, 1)).timestamp())


def of_unix(seconds: int) -> str:
    return of(datetime.fromtimestamp(seconds, tz=UTC))


def short(month_key: str) -> str:
    return _NAMES[parts(month_key)[1] - 1][:3]


def label(month_key: str) -> str:
    year, month = parts(month_key)
    return f"{_NAMES[month - 1][:3]} {year}"


def span(first: str, last: str) -> tuple[str, ...]:
    out: list[str] = []
    current = first
    while current <= last:
        out.append(current)
        current = shift(current, 1)
    return tuple(out)


def range_label(months: tuple[str, ...]) -> str:
    if not months:
        return ""
    if len(months) == 1:
        return label(months[0])
    first, last = months[0], months[-1]
    if parts(first)[0] == parts(last)[0]:
        return f"{short(first)}-{short(last)} {parts(last)[0]}"
    return f"{label(first)} - {label(last)}"


@dataclass(frozen=True)
class Range:
    months: tuple[str, ...]
    named: bool = False      # the message named a range (else the default)
    clamped: bool = False    # more than 12 months were asked for


def default(today: date) -> Range:
    current = of(today)
    return Range(span(shift(current, -DEFAULT_MONTHS), shift(current, -1)))


def parse(text: str, today: date) -> Range:
    current = of(today)
    found = _find(text or "", today, current)
    if found is None:
        return default(today)
    kept = tuple(m for m in found if m <= current)
    if not kept:
        return default(today)
    clamped = len(kept) > MAX_MONTHS
    return Range(kept[-MAX_MONTHS:], named=True, clamped=clamped)


def _find(text: str, today: date, current: str) -> tuple[str, ...] | None:
    last_n = _LAST_N.search(text)
    if last_n:
        raw = last_n.group(1).casefold()
        n = max(1, _NUMBER_WORDS.get(raw) or int(raw))
        return span(shift(current, -n), shift(current, -1))
    if _LAST_MONTH.search(text):
        return (shift(current, -1),)
    quarter = _QUARTER.search(text)
    if quarter:
        return _quarter(int(quarter.group(1)), quarter.group(2), today)
    if _THIS_MONTH.search(text):
        return (current,)
    if _YTD.search(text):
        first = key(today.year, 1)
        return span(first, max(first, shift(current, -1)))
    named = _named(text, today)
    if named:
        return span(min(named), max(named))
    return None


def _quarter(q: int, year_text: str | None, today: date) -> tuple[str, ...]:
    if year_text:
        year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
    else:
        year = today.year if date(today.year, 3 * q - 2, 1) <= today else today.year - 1
    return span(key(year, 3 * q - 2), key(year, 3 * q))


def _named(text: str, today: date) -> list[str]:
    """Every month the text names, ISO or by name; a named month without a year is its latest occurrence that has
    started, and the first month of "Jan to Mar 2026" takes the second's year."""
    tokens: list[tuple[int, int, int, int | None]] = []   # (start, end, month, year)
    for m in _ISO_MONTH.finditer(text):
        month = int(m.group(2))
        if 1 <= month <= 12:
            tokens.append((m.start(), m.end(), month, int(m.group(1))))
    for m in _NAMED.finditer(text):
        word = m.group(1).casefold()
        if word == "may" and not m.group(2) and not _MAY_CONTEXT.search(text[:m.start()]):
            continue  # "you may ..." is not a month
        tokens.append((m.start(), m.end(), _ABBR.index(word[:3]) + 1, int(m.group(2)) if m.group(2) else None))
    tokens.sort()
    years = _fill_years(tokens, text, today)
    return [key(y, mo) for (_, _, mo, _), y in zip(tokens, years, strict=True)]


def _fill_years(tokens, text: str, today: date) -> list[int]:
    years: list[int | None] = [t[3] for t in tokens]
    for i in range(len(tokens) - 1, 0, -1):  # "Jan to Mar 2026": the start takes the end's year
        start, end = tokens[i - 1], tokens[i]
        if years[i - 1] is None and years[i] is not None and _RANGE_JOIN.match(text[start[1]:end[0]]):
            years[i - 1] = years[i] if start[2] <= end[2] else years[i] - 1
    out: list[int] = []
    for (_, _, month, _), year in zip(tokens, years, strict=True):
        if year is None:
            year = today.year if month <= today.month else today.year - 1
        out.append(year)
    for i in range(1, len(out)):  # "Nov to Feb" with no years: Feb follows Nov
        if tokens[i][3] is None and key(out[i], tokens[i][2]) < key(out[i - 1], tokens[i - 1][2]) \
                and _RANGE_JOIN.match(text[tokens[i - 1][1]:tokens[i][0]]):
            out[i - 1] -= 1
    return out
