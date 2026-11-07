"""Pure: what the user's message asks for, read in code (window, team hint, dry run, continue, JSON output,
an assignment ask, an envelope from a previous step). No model is involved."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_WINDOW_DAYS = 3
MAX_WINDOW_DAYS = 14

_DAYS = re.compile(r"\b(\d{1,3})\s*(?:-\s*)?days?\b", re.IGNORECASE)
_WEEKS = re.compile(r"\b(\d{1,2}|one|two|a)\s*weeks?\b", re.IGNORECASE)
_WORD_WEEKS = {"a": 1, "one": 1, "two": 2}
_TEAM_HINT = re.compile(
    r"\b(?:in|into|to|for|under)\s+(?:the\s+)?([A-Za-z0-9][\w&.\- ]{0,40}?)\s+(?:team|project|board)\b",
    re.IGNORECASE)
_TEAM_LABEL = re.compile(r"\bteam\s*[:=]\s*([A-Za-z0-9][\w&.\- ]{0,40})", re.IGNORECASE)
_DRY_RUN = re.compile(r"\bdry[\s-]?run\b|\bjust show\b|\bonly show\b|\bdon'?t create\b|\bpreview\b", re.IGNORECASE)
_CONTINUE = re.compile(r"^\s*(?:continue|next|more|next (?:five|5)|show (?:me )?(?:the )?(?:next|rest))\b",
                       re.IGNORECASE)
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_ASSIGN = re.compile(r"\bassign(?:ed|ing)?\b(?:\s+\w+){0,4}\s+to\b|\bassignee\b|\bmake \w+ the owner\b",
                     re.IGNORECASE)
_FENCED = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Ask:
    text: str
    window_days: int = DEFAULT_WINDOW_DAYS
    team_hint: str | None = None
    dry_run: bool = False
    is_continue: bool = False
    wants_json: bool = False
    wants_assign: bool = False
    envelope_requests: tuple[Mapping[str, Any], ...] | None = None


def read(text: str, metadata: Mapping[str, Any] | None = None) -> Ask:
    body = text or ""
    wants_json = str((metadata or {}).get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(body))
    return Ask(text=body, window_days=window_days(body), team_hint=team_hint(body),
               dry_run=bool(_DRY_RUN.search(body)), is_continue=bool(_CONTINUE.match(body)),
               wants_json=wants_json, wants_assign=bool(_ASSIGN.search(body)),
               envelope_requests=envelope_requests(body))


def window_days(text: str) -> int:
    """The inbox window the user asked for: 3 days by default, clamped to 1-14."""
    days = _DAYS.search(text or "")
    if days:
        return _clamp(int(days.group(1)))
    weeks = _WEEKS.search(text or "")
    if weeks:
        word = weeks.group(1).lower()
        return _clamp(7 * (_WORD_WEEKS.get(word) or int(word)))
    return DEFAULT_WINDOW_DAYS


def _clamp(days: int) -> int:
    return max(1, min(MAX_WINDOW_DAYS, days))


def team_hint(text: str) -> str | None:
    match = _TEAM_LABEL.search(text or "") or _TEAM_HINT.search(text or "")
    if not match:
        return None
    hint = match.group(1).strip(" .,")
    return hint or None


def envelope_requests(text: str) -> tuple[Mapping[str, Any], ...] | None:
    """`requests` from a previous step's envelope (JSON text, optionally fenced), else None."""
    body = (text or "").strip()
    fenced = _FENCED.match(body)
    body = fenced.group(1) if fenced else body
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    raw = data.get("requests") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return None
    return tuple(item for item in raw if isinstance(item, Mapping))
