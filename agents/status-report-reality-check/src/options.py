"""Pure: the optional config phrases a message may carry, read in code and removed from the report text.

`as of 2026-11-04`, `stale after 10 days`, `due soon within 7 days`, `Status map: in review = in progress`,
`only epics`, `include unreported risks`. Each phrase changes one field; anything else is left alone.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any

import statuses

DEFAULT_STALE_DAYS = 14
DEFAULT_SOON_DAYS = 14
MIN_DAYS, MAX_DAYS = 1, 365

_AS_OF = re.compile(r"\bas of:?\s*(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_STALE = re.compile(r"\bstale (?:after|threshold(?: of)?|=)\s*(\d{1,3})\s*days?\b", re.IGNORECASE)
_SOON = re.compile(r"\bdue[- ]soon (?:within|window(?: of)?|=)\s*(\d{1,3})\s*days?\b", re.IGNORECASE)
_STATUS_MAP = re.compile(r"^[ \t]*status map\s*:[ \t]*(.+)$", re.IGNORECASE | re.MULTILINE)
_ONLY_EPICS = re.compile(r"\bonly epics\b", re.IGNORECASE)
_UNREPORTED = re.compile(r"\b(?:include|show|with) unreported (?:at[- ]risk tickets|risks?)\b", re.IGNORECASE)
_PATTERNS = (_STATUS_MAP, _AS_OF, _STALE, _SOON, _ONLY_EPICS, _UNREPORTED)
_BLANK_RUNS = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class Options:
    as_of: str | None = None
    stale_days: int = DEFAULT_STALE_DAYS
    soon_days: int = DEFAULT_SOON_DAYS
    status_map: tuple[tuple[str, str], ...] = ()
    only_epics: bool = False
    unreported: bool = False

    def to_state(self) -> dict:
        data = asdict(self)
        data["status_map"] = [list(pair) for pair in self.status_map]
        return data

    @staticmethod
    def from_state(data: Any) -> Options:
        if not isinstance(data, dict):
            return Options()
        pairs = tuple((str(p[0]), str(p[1])) for p in data.get("status_map") or () if len(p) == 2)
        return Options(as_of=data.get("as_of") or None, stale_days=int(data.get("stale_days") or DEFAULT_STALE_DAYS),
                       soon_days=int(data.get("soon_days") or DEFAULT_SOON_DAYS), status_map=pairs,
                       only_epics=bool(data.get("only_epics")), unreported=bool(data.get("unreported")))

    def mapping(self) -> dict[str, str]:
        return dict(self.status_map)


@dataclass(frozen=True)
class Parsed:
    options: Options
    text: str          # the message with every config phrase removed
    changed: bool      # at least one phrase was present


def parse(text: str, base: Options | None = None) -> Parsed:
    """Reads config phrases from prose lines only: a delimited export row (a ticket title saying "stale
    after 3 days") is never read as config."""
    current, changed, kept = base or Options(), False, []
    for line in (text or "").splitlines():
        if _is_data_row(line):
            kept.append(line)
            continue
        current, line_changed = _apply(current, line)
        changed |= line_changed
        kept.append(_strip(line) if line_changed else line)
    return Parsed(current, _BLANK_RUNS.sub("\n\n", "\n".join(kept)).strip(), changed)


def _apply(current: Options, line: str) -> tuple[Options, bool]:
    if match := _AS_OF.search(line):
        current = replace(current, as_of=match.group(1))
    if match := _STALE.search(line):
        current = replace(current, stale_days=_clamp(match.group(1)))
    if match := _SOON.search(line):
        current = replace(current, soon_days=_clamp(match.group(1)))
    if match := _STATUS_MAP.search(line):
        merged = {**current.mapping(), **statuses.parse_status_map(match.group(1))}
        current = replace(current, status_map=tuple(sorted(merged.items())))
    if _ONLY_EPICS.search(line):
        current = replace(current, only_epics=True)
    if _UNREPORTED.search(line):
        current = replace(current, unreported=True)
    return current, any(p.search(line) for p in _PATTERNS)


def _strip(line: str) -> str:
    for pattern in _PATTERNS:
        line = pattern.sub("", line)
    return line.strip(" \t,;.")


def _is_data_row(line: str) -> bool:
    stripped = line.lstrip()
    return "\t" in line or line.count(",") >= 2 or stripped[:1] in ("|", "{", "[", '"')


def _clamp(value: str) -> int:
    return max(MIN_DAYS, min(MAX_DAYS, int(value)))
