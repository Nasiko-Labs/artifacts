"""Pure: what the user's message asks for, read in code (channels, window, team hint, dry run, continue, JSON
output, a "watch"/assignment ask, an envelope from a previous step). No model is involved."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

DEFAULT_WINDOW_DAYS = 3
MAX_WINDOW_DAYS = 14
MAX_CHANNELS = 8

_DAYS = re.compile(r"\b(\d{1,3})\s*(?:-\s*)?days?\b", re.IGNORECASE)
_WEEKS = re.compile(r"\b(\d{1,2}|one|two|a)\s*weeks?\b", re.IGNORECASE)
_WORD_WEEKS = {"a": 1, "one": 1, "two": 2}
_CHANNEL = re.compile(r"(?<![\w&/])#([A-Za-z0-9][A-Za-z0-9._-]{0,79})")
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
_WATCH = re.compile(r"\b(?:watch|monitor|keep an eye on|subscribe)\b|\bevery (?:hour|day|morning|week|\d+\s*\w+)\b"
                    r"|\b(?:hourly|daily|weekly)\b|\bwhenever\b|\bautomatically\b", re.IGNORECASE)
_FENCED = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)


@dataclass(frozen=True)
class Ask:
    text: str
    window_days: int = DEFAULT_WINDOW_DAYS
    channels: tuple[str, ...] = ()
    team_hint: str | None = None
    dry_run: bool = False
    is_continue: bool = False
    wants_json: bool = False
    wants_assign: bool = False
    wants_watch: bool = False
    from_envelope: bool = False


def read(text: str, metadata: Mapping[str, Any] | None = None) -> Ask:
    body = text or ""
    wants_json = str((metadata or {}).get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(body))
    ask = Ask(text=body, window_days=window_days(body), channels=channels(body), team_hint=team_hint(body),
              dry_run=bool(_DRY_RUN.search(body)), is_continue=bool(_CONTINUE.match(body)), wants_json=wants_json,
              wants_assign=bool(_ASSIGN.search(body)), wants_watch=bool(_WATCH.search(body)))
    envelope = envelope_input(body)
    if envelope is None:
        return ask
    return replace(ask, channels=envelope[0], window_days=envelope[1], wants_watch=False, wants_assign=False,
                   is_continue=False, from_envelope=True,
                   text="Scan " + (" ".join(f"#{c}" for c in envelope[0]) or "my channels")
                   + f" for threads that need an issue, last {envelope[1]} days")


def window_days(text: str) -> int:
    """The window the user asked for: 3 days by default, clamped to 1-14."""
    days = _DAYS.search(text or "")
    if days:
        return clamp(int(days.group(1)))
    weeks = _WEEKS.search(text or "")
    if weeks:
        word = weeks.group(1).lower()
        return clamp(7 * (_WORD_WEEKS.get(word) or int(word)))
    return DEFAULT_WINDOW_DAYS


def clamp(days: int) -> int:
    return max(1, min(MAX_WINDOW_DAYS, days))


def channels(text: str) -> tuple[str, ...]:
    """Channel names written as #name (not a bare number like #412), at most 8, in order."""
    names = [m.group(1).rstrip("._-").lower() for m in _CHANNEL.finditer(text or "")]
    return tuple(dict.fromkeys(n for n in names if n and not n.isdigit()))[:MAX_CHANNELS]


def team_hint(text: str) -> str | None:
    match = _TEAM_LABEL.search(text or "") or _TEAM_HINT.search(text or "")
    if not match:
        return None
    hint = match.group(1).strip(" .,")
    return hint or None


def envelope_input(text: str) -> tuple[tuple[str, ...], int] | None:
    """(`channels`, `window_days`) from a previous step's envelope (JSON text, optionally fenced), else None."""
    body = (text or "").strip()
    fenced = _FENCED.match(body)
    body = fenced.group(1) if fenced else body
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or not ({"channels", "window_days"} & set(data)):
        return None
    raw = data.get("channels")
    names = tuple(str(c).lstrip("#").strip().lower() for c in raw if str(c).strip()) if isinstance(raw, list) else ()
    days = data.get("window_days")
    window = clamp(int(days)) if isinstance(days, (int, float)) and not isinstance(days, bool) else DEFAULT_WINDOW_DAYS
    return tuple(dict.fromkeys(names))[:MAX_CHANNELS], window
