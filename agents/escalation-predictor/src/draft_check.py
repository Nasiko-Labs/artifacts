"""Pure: the rules a status reply must keep. It never states a date, a time frame or an ETA, never
promises a fix, names no issue key or other ticket, and carries no link."""

from __future__ import annotations

import re

_MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|"
           "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
_DAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("a date", re.compile(rf"\b\d{{4}}-\d{{1,2}}-\d{{1,2}}\b|\b\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?\b|"
                          rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}\b|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTHS})\b",
                          re.IGNORECASE)),
    ("a day or time frame", re.compile(rf"\b(?:{_DAYS}|tomorrow|tonight|today|this week|next week|next month|"
                                       r"end of (?:the )?(?:day|week|month|quarter)|within \w+ (?:hours?|days?|weeks?)|"
                                       r"in (?:a|\d+|a few|two|three) (?:hours?|days?|weeks?))\b", re.IGNORECASE)),
    ("a promise", re.compile(r"\bsoon\b|\beta\b|\bwill (?:be )?(?:fix|fixed|resolve|resolved|release|released|ship|"
                             r"shipped|deploy|deployed|patch|patched|available|ready|done|live)\b|\bguarantee",
                             re.IGNORECASE)),
    ("a deadline", re.compile(r"\bby (?:the )?(?:end|next|mid|early|late|" + _DAYS + "|" + _MONTHS + r")\b",
                              re.IGNORECASE)),
    ("an issue key", re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")),
    ("a link", re.compile(r"https?://|www\.", re.IGNORECASE)),
)
_TICKET_REF = re.compile(r"#\s*([A-Za-z0-9_-]+)")


def problems(text: str, ticket_id: str, max_chars: int) -> tuple[str, ...]:
    found = [f"it states {name}" for name, pattern in RULES if pattern.search(text or "")]
    if any(ref != ticket_id for ref in _TICKET_REF.findall(text or "")):
        found.append("it names another ticket")
    if not (text or "").strip():
        found.append("it is empty")
    if len(text or "") > max_chars:
        found.append(f"it is longer than {max_chars} characters")
    return tuple(found)
