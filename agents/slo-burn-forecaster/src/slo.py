"""Pure: SLO targets out of the user's words. `payments 99.95% 30d`, `checkout: 99.9% over 30 days`,
`SLO 99.95 for payments`, `SLO 99.9 for all, 30 days`, JSON `{"service","slo","period_days"}`, and a bare
`99.9% over 30 days` (an answer to the SLO question). Only services present in the data are kept. No default
target is ever assumed: a service without one is asked about."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

MIN_PERCENT = Decimal(50)
MAX_PERCENT = Decimal(100)
MAX_PERIOD_DAYS = 365

_PCT = r"(?P<pct>\d{1,2}(?:\.\d+)?)"
_DAYS = r"(?P<days>\d{1,3})\s*(?:-?\s*d\b|-?\s*days?\b)"
_JOIN = r"\s*(?:,|/|over|per|in|for|across)?\s*(?:a\s+|the\s+)?(?:rolling\s+)?"
_NAME = r"(?P<name>[A-Za-z][\w.\-/]*)"
_PER_SERVICE = re.compile(rf"{_NAME}\s*(?::|=|-)?\s*(?:slo\s*(?:of|is|=|:)?\s*)?{_PCT}\s*%(?:\s*slo)?(?:{_JOIN}{_DAYS})?",
                          re.IGNORECASE)
_SLO_FOR = re.compile(rf"slo\s*(?:of|to|=|:)?\s*{_PCT}\s*%?\s*(?:for|on)\s+{_NAME}(?:{_JOIN}{_DAYS})?", re.IGNORECASE)
_SLO_NAME_FIRST = re.compile(rf"slo\s+{_NAME}\s*(?:to|=|:)?\s*{_PCT}\s*%?(?:{_JOIN}{_DAYS})?", re.IGNORECASE)
_GLOBAL = re.compile(rf"(?:slo\s*(?:of)?\s*)?{_PCT}\s*%?\s*(?:slo\s*)?for\s+(?:all|every|each)(?:\s+services?)?"
                     rf"(?:{_JOIN}{_DAYS})?", re.IGNORECASE)
_BARE = re.compile(rf"^\s*{_PCT}\s*%(?:{_JOIN}{_DAYS})?\s*$", re.IGNORECASE)
_PERIOD_ONLY = re.compile(rf"(?:over|per|in|across)\s+(?:a\s+|the\s+)?(?:rolling\s+)?{_DAYS}", re.IGNORECASE)
_HINT = re.compile(r"\d{2}(?:\.\d+)?\s*(?:%|percent|nines)|\bslo\b|\bnines\b", re.IGNORECASE)


@dataclass(frozen=True)
class Slo:
    percent: str          # as written, e.g. "99.95"
    period_days: int | None

    @property
    def target(self) -> Decimal:
        return Decimal(self.percent) / 100

    @property
    def complete(self) -> bool:
        return self.period_days is not None

    def to_list(self) -> list:
        return [self.percent, self.period_days]

    @staticmethod
    def from_list(raw) -> Slo:
        return Slo(str(raw[0]), None if raw[1] is None else int(raw[1]))


def valid_percent(text: str) -> str | None:
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return text if MIN_PERCENT < value < MAX_PERCENT else None


def valid_days(text: str | None) -> int | None:
    if text is None or not str(text).isdigit():
        return None
    days = int(text)
    return days if 1 <= days <= MAX_PERIOD_DAYS else None


def _slo(pct: str, days: str | None) -> Slo | None:
    percent = valid_percent(pct)
    return None if percent is None else Slo(percent, valid_days(days))


def find(text: str, services: Iterable[str]) -> dict[str, Slo]:
    """Service name -> Slo for every known service the text states a target for. A global target fills the
    services not named; a period stated alone (`... both over 30 days`) completes the targets when none of them
    carries its own period."""
    known = {s.casefold(): s for s in services}
    found: dict[str, Slo] = {}
    for pattern in (_PER_SERVICE, _SLO_FOR, _SLO_NAME_FIRST):
        for match in pattern.finditer(text):
            name = known.get(match.group("name").casefold())
            slo = _slo(match.group("pct"), match.group("days"))
            if name and slo and (name not in found or not found[name].complete):
                found = {**found, name: slo}
    found = {**found, **_json_slos(text, known)}
    glob = _GLOBAL.search(text)
    if glob:
        slo = _slo(glob.group("pct"), glob.group("days"))
        if slo:
            found = {**{name: slo for name in known.values()}, **found}
    period = _PERIOD_ONLY.search(text)
    if period and valid_days(period.group("days")) and not any(v.complete for v in found.values()):
        days = valid_days(period.group("days"))
        found = {k: v if v.complete else Slo(v.percent, days) for k, v in found.items()}
    return found


def _json_slos(text: str, known: Mapping[str, str]) -> dict[str, Slo]:
    found: dict[str, Slo] = {}
    for line in text.splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped.startswith("{"):
            continue
        try:
            obj = json.loads(stripped)
        except ValueError:
            continue
        if not isinstance(obj, Mapping) or "slo" not in obj:
            continue
        name = known.get(str(obj.get("service") or "").casefold())
        slo = _slo(str(obj["slo"]).rstrip("%"), str(obj.get("period_days")) if obj.get("period_days") else None)
        if name and slo:
            found = {**found, name: slo}
    return found


def bare(text: str) -> Slo | None:
    """A target with no service name (`99.9% over 30 days`), as the answer to the SLO question gives it."""
    match = _BARE.match(text or "")
    return _slo(match.group("pct"), match.group("days")) if match else None


def mentions_slo(text: str) -> bool:
    """Outside the phrasings code reads, the text still seems to state a target (worth one model read)."""
    rest = text or ""
    for pattern in (_PER_SERVICE, _SLO_FOR, _SLO_NAME_FIRST, _GLOBAL, _PERIOD_ONLY):
        rest = pattern.sub(" ", rest)
    return bool(_HINT.search(rest))
