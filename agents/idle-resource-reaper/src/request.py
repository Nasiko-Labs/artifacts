"""Pure: what the user's message asks for, parsed in code (never by the model): "older than N days",
"min cost N", "owner X", "grace N days", which owners or rows to draft for, a typed owner contact, a
request to delete or stop something, and JSON output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

DEFAULT_GRACE_DAYS = 7
MIN_GRACE_DAYS, MAX_GRACE_DAYS = 1, 60
MAX_OLDER_THAN_DAYS = 3650

_OLDER = re.compile(r"\b(?:older than|idle (?:for )?(?:more than|over|at least)|unused for)\s+(\d{1,4})\s*"
                    r"(days?|weeks?|months?)\b", re.IGNORECASE)
_MIN_COST = re.compile(r"\b(?:min(?:imum)?\s+cost|cost\s+(?:above|over|at least)|costing\s+(?:more than|over|at "
                       r"least))\s*(?:of\s*)?[$]?\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_OWNER = re.compile(r"\bowner\s+(?:is\s+)?([A-Za-z][\w.@+-]{1,60})\b", re.IGNORECASE)
_GRACE = re.compile(r"\bgrace(?:\s+period)?(?:\s+of)?\s+(\d{1,3})\s*(days?|weeks?)\b", re.IGNORECASE)
_DRAFT = re.compile(r"\b(?:draft|write|message|messages|nudge|notify|send|ping|email)\b", re.IGNORECASE)
_ALL_KNOWN = re.compile(r"\ball(?:\s+(?:the|known|of the))*\s+owners?\b|\beveryone\b|\ball of them\b",
                        re.IGNORECASE)
_NUMBERED = re.compile(r"#\s*(\d{1,3})\b")
_DESTRUCTIVE = re.compile(r"\b(?:delete|deleting|remove|removing|terminate|terminating|stop|stopping|destroy|"
                          r"kill|release|deregister|detach|clean\s*up|cleanup|purge|wipe|nuke)\b", re.IGNORECASE)
_QUESTION = re.compile(r"^\s*(?:who|which|what|why|how|when|should|is|are|does|do|would)\b", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_HANDLE = re.compile(r"^@?([A-Za-z0-9][\w.-]{1,40})$")
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    older_than_days: int | None = None       # replaces every rule's minimum idle/age when given
    min_cost: Decimal | None = None          # findings with a known cost below it are left out (counted)
    owner: str = ""                           # only this owner's resources
    grace_days: int = DEFAULT_GRACE_DAYS      # the reply-by deadline in drafts
    explicit_grace: bool = False


def _days(number: str, unit: str) -> int:
    n = int(number)
    unit = unit.casefold()
    return n * 7 if unit.startswith("week") else n * 30 if unit.startswith("month") else n


def parse_request(text: str) -> Request:
    text = text or ""
    older = _OLDER.search(text)
    cost = _MIN_COST.search(text)
    owner = _OWNER.search(text)
    grace = _GRACE.search(text)
    grace_days = DEFAULT_GRACE_DAYS
    if grace:
        grace_days = max(MIN_GRACE_DAYS, min(MAX_GRACE_DAYS, _days(grace.group(1), grace.group(2))))
    return Request(older_than_days=min(_days(*older.groups()), MAX_OLDER_THAN_DAYS) if older else None,
                   min_cost=_decimal(cost.group(1)) if cost else None,
                   owner=owner.group(1).strip(".") if owner else "", grace_days=grace_days,
                   explicit_grace=bool(grace))


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def wants_draft(text: str) -> bool:
    return bool(_DRAFT.search(text or ""))


def drafts_all_known(text: str) -> bool:
    return wants_draft(text) and bool(_ALL_KNOWN.search(text or ""))


def numbered(text: str) -> tuple[str, ...]:
    """`#1 and #7` → ("1", "7"), de-duplicated in order."""
    return tuple(dict.fromkeys(str(int(n)) for n in _NUMBERED.findall(text or "")))


def asks_to_destroy(text: str) -> bool:
    """"Delete them all", "stop i-0abc", "clean up the unused volumes": a request for an action this agent
    never takes. Questions ("who should delete #3?", "how much would deleting save?") are not refused here."""
    text = text or ""
    return bool(_DESTRUCTIVE.search(text)) and not _QUESTION.search(text)


def contact_in(text: str) -> tuple[str, str]:
    """A typed owner contact: ("email", address) or ("chat", handle), or ("", "") when the text is neither."""
    text = (text or "").strip()
    email = _EMAIL.fullmatch(text) or (_EMAIL.search(text) if len(text.split()) <= 3 else None)
    if email:
        return "email", email.group(0)
    handle = _HANDLE.fullmatch(text)
    if handle and (text.startswith("@") or any(ch.isdigit() for ch in text)):
        return "chat", handle.group(1)
    return "", ""


def wants_json(text: str) -> bool:
    return bool(_JSON_WORDS.search(text or ""))
