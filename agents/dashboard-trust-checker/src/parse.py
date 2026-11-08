"""Pure: what the user asked, parsed in code: the sheet (and tab) to read, the stale and unused thresholds,
json mode, and the follow-up intents code can recognise without a model call."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

DEFAULT_STALE_DAYS = 7
DEFAULT_UNUSED_DAYS = 90
MAX_DAYS = 3650
_NAME = r"[A-Za-z0-9][\w&'./ -]{0,60}?"
_END = r"(?=$|[,.;!?]|\s+(?:and|then|tab|with|for|stale|unused|as)\b)"
_SHEET_PATTERNS = (
    re.compile(r"[\"“']([^\"”']{1,80})[\"”']\s+(?:sheet|spreadsheet)\b", re.IGNORECASE),
    re.compile(r"\b(?:sheet|spreadsheet)\s+(?:named\s+|called\s+|titled\s+)?[\"“']([^\"”']{1,80})[\"”']",
               re.IGNORECASE),
    re.compile(r"\b(?:sheet|spreadsheet)\s+(?:named\s+|called\s+|titled\s+)?(" + _NAME + r")" + _END, re.IGNORECASE),
    re.compile(r"\b(?:from|in|of|using|read|on)\s+(?:the\s+|my\s+|our\s+)?(" + _NAME + r")\s+(?:sheet|spreadsheet)\b",
               re.IGNORECASE),
    re.compile(r"\b(?:from|using)\s+[\"“']?(" + _NAME + r")[\"”']?" + _END, re.IGNORECASE),
)
_NOT_A_SHEET = re.compile(r"^(?:the|my|our|this|it|them|here|chat|a|an|table|paste|pasted|export|inventory "
                          r"below|below|above|the table|my dashboards|dashboards)$", re.IGNORECASE)
_TAB = re.compile(r"\b(?:tab|worksheet)\s+(?:named\s+|called\s+)?[\"“']?([\w &-]{1,40}?)[\"”']?(?=$|[,.;!?]|\s+(?:and|"
                  r"then|for|from|of)\b)", re.IGNORECASE)
_STALE = re.compile(r"\bstale\s+(?:after|at|over|beyond|past|if older than|>)\s*(\d{1,4})\s*d(?:ays?)?\b|"
                    r"\b(\d{1,4})\s*d(?:ays?)?\s+(?:stale|staleness)\b|\bthreshold(?:\s+(?:of|to|=|:))?\s*(\d{1,4})\b",
                    re.IGNORECASE)
_UNUSED = re.compile(r"\bunused\s+(?:after|at|over|beyond|past|if not viewed in)\s*(\d{1,4})\s*d(?:ays?)?\b",
                     re.IGNORECASE)
_JSON = re.compile(r"\b(?:as|in) json\b|\bjson only\b|\bjson output\b", re.IGNORECASE)
_RETIRE = re.compile(r"\b(?:kill|retire|retirement|sunset|decommission|clean ?up|get rid of|prune)\b|"
                     r"\bwhich (?:ones|dashboards) (?:should|can) (?:we|i) (?:drop|remove)\b", re.IGNORECASE)
_DELETE = re.compile(r"\b(?:delete|archive|remove|trash|deprecate|turn off|disable|unpublish)\b", re.IGNORECASE)
_HARD_DELETE = re.compile(r"\b(?:delete|archive|trash|unpublish)\b", re.IGNORECASE)
_WHY = re.compile(r"^\s*why\b|\bwhy (?:is|was|are|did|does)\b|\bwhat'?s wrong with\b|\bexplain\b", re.IGNORECASE)
_RERUN = re.compile(r"\b(?:audit|check|re-?run|run)\b.*\b(?:again|another|different|new)\b|\baudit\b.*\b(?:sheet|"
                    r"spreadsheet|inventory)\b", re.IGNORECASE)
_DOMAIN = re.compile(r"\b(dashboards?|inventory|bi|reports?|stale|broken|unused|refresh\w*|viewers?|audit|retire|"
                     r"kill|sheet|spreadsheet|trust\w*)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    sheet: str | None = None
    tab: str | None = None
    stale_days: int | None = None        # None: the default (or each row's cadence)
    unused_days: int | None = None
    json_mode: bool = False
    retire: bool = False
    delete: bool = False
    why: bool = False
    rerun: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Request:
        fields = Request.__dataclass_fields__
        return Request(**{k: v for k, v in dict(data or {}).items() if k in fields})


def parse(text: str, metadata: Mapping[str, Any] | None = None) -> Request:
    body = text or ""
    first_line = body.strip().splitlines()[0] if body.strip() else ""
    output = str((metadata or {}).get("output") or "").casefold()
    return Request(
        sheet=_sheet(first_line),
        tab=_group(_TAB.search(first_line)),
        stale_days=_days(_STALE.search(body)),
        unused_days=_days(_UNUSED.search(body)),
        json_mode=bool(_JSON.search(body)) or output == "json",
        retire=bool(_RETIRE.search(first_line)),
        delete=_wants_delete(first_line),
        why=bool(_WHY.search(first_line)),
        rerun=bool(_RERUN.search(first_line)),
    )


def _wants_delete(line: str) -> bool:
    """"Delete them" is a delete; "which ones should we remove?" is a retire question."""
    return bool(_HARD_DELETE.search(line)) or (bool(_DELETE.search(line)) and not _RETIRE.search(line))


def mentions_domain(text: str) -> bool:
    return bool(_DOMAIN.search(text or ""))


def _sheet(line: str) -> str | None:
    for pattern in _SHEET_PATTERNS:
        match = pattern.search(line)
        if match:
            name = match.group(1).strip().strip(".")
            if name and not _NOT_A_SHEET.match(name):
                return name
    return None


def _group(match: re.Match | None) -> str | None:
    return match.group(1).strip() if match else None


def _days(match: re.Match | None) -> int | None:
    if match is None:
        return None
    raw = next((g for g in match.groups() if g), None)
    if raw is None:
        return None
    value = int(raw)
    return value if 0 < value <= MAX_DAYS else None
