"""Pure: what a message asks for, read in code before any model call: JSON output, and the follow-up sentinels
(`what if x0.5`, `only <service>`, `slo <service> <n>`, `show numbers`, `why <service>`). Service names are
matched against the services in the stored data only."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_FACTOR = Decimal(100)
_JSON = re.compile(r"\b(?:as|in)\s+json\b|\bjson\s+only\b", re.IGNORECASE)
_WHAT_IF = re.compile(r"\bwhat\s+if\b|\bsuppose\b|\bif\b.*\b(?:halve[sd]?|double[sd]?|triple[sd]?)\b", re.IGNORECASE)
_HALF = re.compile(r"\bhal(?:f|ve[sd]?)\b", re.IGNORECASE)
_DOUBLE = re.compile(r"\b(?:double[sd]?|twice)\b", re.IGNORECASE)
_TRIPLE = re.compile(r"\btriple[sd]?\b", re.IGNORECASE)
_TIMES = re.compile(r"(?:\bx|×|\*|\btimes)\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE)
_DOWN = re.compile(r"\b(?:drops?|falls?|reduces?|decreases?|down|cut|lower(?:s|ed)?)\b\D{0,12}?(\d{1,3}(?:\.\d+)?)\s*%",
                   re.IGNORECASE)
_UP = re.compile(r"\b(?:rises?|increases?|up|grows?|higher|jumps?)\b\D{0,12}?(\d{1,3}(?:\.\d+)?)\s*%", re.IGNORECASE)
_ONLY = re.compile(r"\b(?:only|just)\b", re.IGNORECASE)
_NUMBERS = re.compile(r"\b(?:show|give|see)\b.*\b(?:numbers|details|breakdown|maths?|math)\b|\bshow\s+numbers\b",
                      re.IGNORECASE)
_WHY = re.compile(r"\bwhy\b|\bwhat\s+explains\b|\bwhat(?:'s| is)\s+causing\b|\brelated\s+(?:issues|incidents)\b",
                  re.IGNORECASE)
_DOMAIN = re.compile(r"\bslos?\b|\bslis?\b|error[\s-]?budget|burn(?:ing|\s+rate)?\b|\buptime\b|\bavailability\b|"
                     r"\berror\s+rate\b|\breliability\b", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    json_mode: bool = False


def parse(text: str, metadata: Mapping[str, Any] | None = None) -> Request:
    wants_json = bool(_JSON.search(text or "")) or str((metadata or {}).get("output") or "").casefold() == "json"
    return Request(json_mode=wants_json)


def mentions_domain(text: str) -> bool:
    return bool(_DOMAIN.search(text or ""))


def named(text: str, services: Iterable[str]) -> str | None:
    """The service whose exact name the text contains (the longest such name)."""
    for name in sorted(services, key=len, reverse=True):
        if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text or "", re.IGNORECASE):
            return name
    return None


def factor(text: str) -> Decimal | None:
    """The what-if multiplier the text states, or None."""
    if not _WHAT_IF.search(text or ""):
        return None
    for pattern, value in ((_HALF, Decimal("0.5")), (_DOUBLE, Decimal(2)), (_TRIPLE, Decimal(3))):
        if pattern.search(text):
            return value
    found = _TIMES.search(text)
    if found:
        return _bounded(found.group(1))
    down = _DOWN.search(text)
    if down:
        cut = _decimal(down.group(1))
        return None if cut is None or cut > 100 else 1 - cut / 100
    up = _UP.search(text)
    if up:
        rise = _decimal(up.group(1))
        return None if rise is None else _bounded(str(1 + rise / 100))
    return None


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _bounded(text: str) -> Decimal | None:
    value = _decimal(text)
    return value if value is not None and 0 <= value <= MAX_FACTOR else None


def wants_only(text: str) -> bool:
    return bool(_ONLY.search(text or ""))


def wants_numbers(text: str) -> bool:
    return bool(_NUMBERS.search(text or ""))


def wants_why(text: str) -> bool:
    return bool(_WHY.search(text or ""))
