"""Pure: what a customer-facing reply may say, checked in code.

A drafted reply may state only the shipped facts: the fix version, the release date, and that the fix
is released. It is rejected when it (a) names any date or month/weekday other than the verified
release date, (b) makes a future commitment, (c) names a version other than the fix version,
(d) carries the internal tracker key, any internal-looking issue key, or any link, or (e) is too long."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_MONTHS = ("january", "february", "march", "april", "june", "july", "august", "september", "october",
           "november", "december")
_MONTH_ABBREVIATIONS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
FUTURE_PHRASES = ("will be", "we'll", "we will", "soon", "eta", "upcoming", "next week", "next month",
                  "next release", "next version", "expected", "planned", "shortly", "in the coming", "tomorrow",
                  "tonight", "going to", "by the end of")

_DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
    re.compile(rf"\b(?:{'|'.join(_MONTHS + _WEEKDAYS)})\b"),
    re.compile(rf"\b(?:{'|'.join(_MONTH_ABBREVIATIONS)})\.?\s+\d{{1,2}}\b"),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{'|'.join(_MONTH_ABBREVIATIONS)})\b"),
)
_FUTURE = re.compile(rf"(?<![a-z']){'|'.join(re.escape(p) for p in FUTURE_PHRASES)}(?![a-z])")
_VERSION = re.compile(r"(?<![\w.])v?\d+(?:\.\d+)+(?![\w])")
_ISSUE_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
_LINK = re.compile(r"https?://|www\.", re.IGNORECASE)


@dataclass(frozen=True)
class Facts:
    """The shipped facts block: everything a customer reply may state."""

    version: str
    release_date: str   # YYYY-MM-DD
    summary: str
    bug_key: str

    def block(self) -> str:
        return (f"Fix version: {self.version}\nReleased on: {self.release_date} ({_long_date(self.release_date)})\n"
                f"What was fixed: {self.summary}\nStatus: the fix is released and available now.")


def problems(text: str, facts: Facts, max_chars: int = 1200) -> tuple[str, ...]:
    """Why this reply may not go to a customer as drafted; empty when it may."""
    found: list[str] = []
    if len(text) > max_chars:
        found.append(f"it is longer than {max_chars} characters")
    lowered = _without_release_date(text.casefold(), facts.release_date)
    if any(p.search(lowered) for p in _DATE_PATTERNS):
        found.append("it names a date, month or weekday other than the release date")
    future = _FUTURE.search(lowered)
    if future:
        found.append(f"it makes a future commitment (\"{future.group(0)}\")")
    others = {v for v in _VERSION.findall(lowered) if _version(v) != _version(facts.version)}
    if others:
        found.append(f"it names a version other than {facts.version} ({', '.join(sorted(others))})")
    if facts.bug_key and facts.bug_key.casefold() in text.casefold() or _ISSUE_KEY.search(text):
        found.append("it contains an internal issue key")
    if _LINK.search(text):
        found.append("it contains a link")
    return tuple(found)


def _version(name: str) -> str:
    text = name.casefold().strip()
    return text.removeprefix("v")


def _long_date(iso: str) -> str:
    try:
        day = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{day.strftime('%B')} {day.day}, {day.year}"


def _without_release_date(lowered: str, iso: str) -> str:
    """The release date may appear, in the usual written forms; strip those before scanning."""
    try:
        day = date.fromisoformat(iso)
    except ValueError:
        return lowered
    month, short = day.strftime("%B").casefold(), day.strftime("%b").casefold()
    forms = (iso, f"{month} {day.day}, {day.year}", f"{day.day} {month} {day.year}", f"{month} {day.day}",
             f"{day.day} {month}", f"{short} {day.day}, {day.year}", f"{short} {day.day}", f"{day.day} {short}")
    for form in sorted(forms, key=len, reverse=True):
        lowered = re.sub(rf"(?<!\w){re.escape(form)}(?:st|nd|rd|th)?(?!\w)", " ", lowered)
    return lowered
