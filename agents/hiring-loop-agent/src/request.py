"""Pure: what the user asked for, read in code from the request part of the message (the text before
the first pasted block), never from the feedback itself.

- Guards before any call: comparing or ranking candidates, or asking for a hire/no-hire decision.
- Candidate, role, expected competencies, the panel, a folder link, the email window, JSON output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

DEFAULT_WINDOW_DAYS = 60
MAX_WINDOW_DAYS = 120
DEFAULT_EXPECTED: tuple[str, ...] = ("coding", "system design", "communication", "ownership")
SEARCH_WORDS = ("feedback", "scorecard", "interview", "debrief")

_RANKING = re.compile(r"\b(?:compar(?:e|ed|ing|ison)|rank(?:s|ed|ing)?|versus|vs\.?|better than|who should we|"
                      r"best candidate|finalists|shortlist)\b", re.IGNORECASE)
_DECISION = re.compile(
    r"\bshould (?:we|i|they) (?:hire|reject|pass|move (?:forward|ahead)|make (?:an|the) offer|extend|advance)\b|"
    r"\bhire or not\b|\b(?:hire|reject) (?:him|her|them)\b|\b(?:do|would|what do) you (?:recommend|suggest|think we should)\b|"
    r"\byour (?:recommendation|verdict|call)\b|\bverdict\b|\bgo ?/ ?no.?go\b|\bhire\s*/\s*no.?hire\b|"
    r"\bis (?:he|she|they|\w+) (?:a )?(?:good|strong|weak|bad) (?:fit|candidate|hire)\b|"
    r"\b(?:yes or no|thumbs up or down) on\b",
    re.IGNORECASE)
_NAME = r"[A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+){0,3}"
_CANDIDATE = (
    re.compile(rf"\b(?i:candidate)\s*[:\-]\s*(?P<name>{_NAME})"),
    re.compile(rf"\b(?i:debrief|feedback|scorecards?|loop|interviews?|interviewers?|disagree(?:ment)?s?)\s+"
               rf"(?i:for|on|of|about|with)\s+(?P<name>{_NAME})"),
    re.compile(rf"\b(?i:for)\s+(?P<name>{_NAME})\s*(?:'s)?\s+(?i:loop|debrief|interviews?|scorecards?)"),
)
_ROLE = re.compile(r"\b(?:role|position)\s*:\s*(?P<role>[^\n]{2,80})", re.IGNORECASE)
_EXPECTED = re.compile(r"\b(?:expected(?:\s+competencies)?|competencies|cover(?:ing)?)\s*:\s*(?P<list>[^\n]+)",
                       re.IGNORECASE)
_PANEL = re.compile(r"\b(?:panel|interviewers)\s*:\s*(?P<list>[^\n]+)", re.IGNORECASE)
_FOLDER = re.compile(r"https?://\S+")
_DAYS = re.compile(r"\blast\s+(?P<n>\d{1,3})\s+days?\b", re.IGNORECASE)
_JSON = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_MAIL = re.compile(r"\b(?:e-?mails?|mail|inbox)\b", re.IGNORECASE)
_DOCS = re.compile(r"\b(?:docs?|documents?|folder|files?)\b", re.IGNORECASE)
_SCOPE_WORDS = re.compile(r"\b(?:debrief|feedback|scorecards?|interview\w*|loop|candidate|disagree\w*)\b",
                          re.IGNORECASE)
_STOP_NAMES = frozenset({"the", "my", "our", "this", "that", "email", "me", "i"})


@dataclass(frozen=True)
class Request:
    candidate: str = ""
    role: str = ""
    expected: tuple[str, ...] = DEFAULT_EXPECTED
    expected_named: bool = False
    panel: tuple[str, ...] = ()
    folder: str = ""
    window_days: int = DEFAULT_WINDOW_DAYS
    wants_json: bool = False
    in_scope: bool = False        # the request names the job in plain words (no intake call needed)
    wants_mail: bool = False
    wants_docs: bool = False


def asks_to_rank(instruction: str) -> bool:
    return bool(_RANKING.search(instruction or ""))


def asks_for_decision(instruction: str) -> bool:
    return bool(_DECISION.search(instruction or ""))


def read(instruction: str, metadata_json: bool = False) -> Request:
    text = instruction or ""
    expected_match = _EXPECTED.search(text)
    expected = _split_list(expected_match.group("list")) if expected_match else ()
    panel_match = _PANEL.search(text)
    role_match = _ROLE.search(text)
    folder = _FOLDER.search(text)
    days = _DAYS.search(text)
    return Request(
        candidate=candidate_in(text), role=role_match.group("role").strip(" .") if role_match else "",
        expected=expected or DEFAULT_EXPECTED, expected_named=bool(expected),
        panel=_split_list(panel_match.group("list")) if panel_match else (),
        folder=folder.group(0).rstrip(".,)") if folder else "",
        window_days=min(int(days.group("n")), MAX_WINDOW_DAYS) if days and int(days.group("n")) > 0
        else DEFAULT_WINDOW_DAYS,
        wants_json=metadata_json or bool(_JSON.search(text)),
        in_scope=bool(_SCOPE_WORDS.search(text)),
        wants_mail=bool(_MAIL.search(text)), wants_docs=bool(_DOCS.search(text)),
    )


def candidate_in(text: str) -> str:
    for pattern in _CANDIDATE:
        found = pattern.search(text or "")
        if found and found.group("name").split()[0].casefold() not in _STOP_NAMES:
            return " ".join(found.group("name").split()).rstrip(".")
    return ""


def window(today: date, days: int) -> tuple[str, str]:
    """(first day, last day) inclusive, ISO dates, computed here so the model never does date maths."""
    return (today - timedelta(days=days)).isoformat(), today.isoformat()


def search_terms(candidate: str) -> str:
    return f"\"{candidate}\" AND ({' OR '.join(SEARCH_WORDS)})"


def _split_list(raw: str) -> tuple[str, ...]:
    parts = re.split(r",|;|\band\b", raw)
    return tuple(" ".join(p.split()).strip(" .") for p in parts if p.strip(" ."))
