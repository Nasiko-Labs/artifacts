"""Pure: what the user's message asks for, read in code (window, channels, doc title, text-only, JSON
output, an envelope from a previous step, a follow-up command, a selection answer). No model is involved."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

DEFAULT_WINDOW_DAYS = 7
MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 14
MAX_CHANNELS = 15

LOG_ALL = "LOG_ALL"
LOG_PREFIX = "LOG:"
CHANGE_DOC = "CHANGE_DOC"
SHOW_ONLY = "SHOW_ONLY"

_THIS_WEEK = re.compile(r"\bthis week\b|\bsince monday\b|\bweek so far\b", re.IGNORECASE)
_LAST_WEEK = re.compile(r"\b(?:last|past|previous)\s+week\b", re.IGNORECASE)
_DAYS = re.compile(r"\b(\d{1,3})\s*(?:-\s*)?days?\b", re.IGNORECASE)
_WEEKS = re.compile(r"\b(\d{1,2}|one|two|a)\s*weeks?\b", re.IGNORECASE)
_WORD_WEEKS = {"a": 1, "one": 1, "two": 2}
_CHANNEL = re.compile(r"(?<![\w&])#([a-z0-9][a-z0-9_.-]{0,79})", re.IGNORECASE)
_QUOTED_DOC = re.compile(r"\b(?:to|in|into|use)\s+(?:the\s+|my\s+|our\s+)?[\"'“‘]([^\"'“”‘’]{1,80})[\"'”’]",
                         re.IGNORECASE)
_NAMED_DOC = re.compile(r"\b(?:to|in|into|use)\s+(?:the|my|our)\s+([A-Z][\w&\- ]{1,60}?)\s+(?:doc|document|page)\b")
_SHOW_ONLY = re.compile(r"\b(?:show\s+)?(?:the\s+)?text only\b|\bdon'?t write\b|\bdo not write\b|\bjust show\b"
                        r"|\bdry[\s-]?run\b|\bpreview\b|\bjust the text\b|\bwithout writing\b", re.IGNORECASE)
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_EDIT_HISTORY = re.compile(
    r"\b(?:delete|remove|erase|edit|rewrite|retire|undo|replace)\s+(?:the\s+|an?\s+|that\s+|this\s+|my\s+|our\s+)?"
    r"(?:old\s+|existing\s+|previous\s+|earlier\s+|last\s+)?(?:log\s+)?(?:entry|entries|decisions?|lines?)\b",
    re.IGNORECASE)
_FENCED = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)

_VERBS = r"(?:please\s+)?(?:log|add|append|write|include|pick|select|keep)?"
_LOG_ALL = re.compile(rf"^\s*{_VERBS}\s*(?:them\s+)?all(?:\s+(?:the\s+)?new)?(?:\s+(?:ones|of them|decisions))?"
                      r"(?:\s+(?:to|in)\s+the\s+(?:log|doc))?\s*[.!]?\s*$", re.IGNORECASE)
_LOG_NUMBERS = re.compile(rf"^\s*{_VERBS}\s*(?:entries|decisions|items|numbers?|#)?\s*#?\d{{1,3}}"
                          r"(?:\s*(?:,|and|&|\+)?\s*#?\d{1,3})*(?:\s+(?:to|in)\s+the\s+(?:log|doc))?\s*[.!]?\s*$",
                          re.IGNORECASE)
_NUMBER = re.compile(r"\d{1,3}")
_LEADING_NUMBER = re.compile(r"^\s*#?(\d{1,3})[.):\s]")
_CHANGE_DOC = re.compile(r"\b(?:different|another|other|new)\s+(?:doc|document|log)\b|\bchange\s+(?:the\s+)?doc",
                         re.IGNORECASE)
_SCAN_WORDS = re.compile(r"\b(?:build|scan|find|look|search|decisions?\s+(?:in|from|since)|this week|last|days?|"
                         r"weeks?|refresh|again)\b|#", re.IGNORECASE)
_SKIP = frozenset({"skip", "none", "no", "nothing", "cancel", "stop", "n", "not now"})


@dataclass(frozen=True)
class Ask:
    text: str
    window_days: int = DEFAULT_WINDOW_DAYS
    channels: tuple[str, ...] = ()
    doc_title: str | None = None
    show_only: bool = False
    wants_json: bool = False
    edits_history: bool = False
    envelope: Mapping[str, Any] | None = None


def read(text: str, today: date, metadata: Mapping[str, Any] | None = None) -> Ask:
    body = text or ""
    envelope = envelope_of(body)
    title = doc_title(body) or (str(envelope.get("doc_title") or "").strip() or None if envelope else None)
    wants_json = str((metadata or {}).get("output") or "").casefold() == "json" or bool(_JSON_WORDS.search(body))
    return Ask(text=body, window_days=window_days(body, today), channels=channels(body), doc_title=title,
               show_only=bool(_SHOW_ONLY.search(body)), wants_json=wants_json,
               edits_history=bool(_EDIT_HISTORY.search(body)), envelope=envelope)


def window_days(text: str, today: date) -> int:
    """7 days by default; "this week" / "since Monday" counts back to Monday; clamped to 1-14."""
    body = text or ""
    if _THIS_WEEK.search(body):
        return _clamp(today.weekday())
    days = _DAYS.search(body)
    if days:
        return _clamp(int(days.group(1)))
    weeks = _WEEKS.search(body)
    if weeks and not _LAST_WEEK.search(body):
        word = weeks.group(1).lower()
        return _clamp(7 * (_WORD_WEEKS.get(word) or int(word)))
    return DEFAULT_WINDOW_DAYS


def _clamp(days: int) -> int:
    return max(MIN_WINDOW_DAYS, min(MAX_WINDOW_DAYS, days))


def channels(text: str) -> tuple[str, ...]:
    names = [m.group(1).lower().rstrip(".") for m in _CHANNEL.finditer(text or "")]
    return tuple(dict.fromkeys(names))[:MAX_CHANNELS]


def doc_title(text: str) -> str | None:
    match = _QUOTED_DOC.search(text or "") or _NAMED_DOC.search(text or "")
    if not match:
        return None
    title = " ".join(match.group(1).split()).strip(" .,")
    return title or None


def envelope_of(text: str) -> Mapping[str, Any] | None:
    """A previous step's envelope (JSON text, optionally fenced) carrying a `decisions` list, else None."""
    body = (text or "").strip()
    fenced = _FENCED.match(body)
    body = fenced.group(1) if fenced else body
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("decisions"), list):
        return None
    return data


def command(text: str, has_list: bool) -> str | None:
    """A follow-up on the list already shown, read in code: LOG_ALL, LOG:<n,...>, SHOW_ONLY, CHANGE_DOC."""
    if not has_list:
        return None
    body = (text or "").strip()
    if _LOG_ALL.match(body):
        return LOG_ALL
    if _LOG_NUMBERS.match(body):
        return LOG_PREFIX + ",".join(dict.fromkeys(_NUMBER.findall(body)))
    if _SHOW_ONLY.search(body) and not _SCAN_WORDS.search(body):
        return SHOW_ONLY
    if _CHANGE_DOC.search(body) or (doc_title(body) and not _SCAN_WORDS.search(body)):
        return CHANGE_DOC
    return None


def numbers_of(command_text: str) -> tuple[str, ...]:
    """The ids in a `LOG:1,3` command (from code or from the model), in order, without repeats."""
    body = command_text[len(LOG_PREFIX):] if command_text.upper().startswith(LOG_PREFIX) else command_text
    return tuple(dict.fromkeys(str(int(n)) for n in _NUMBER.findall(body)))


@dataclass(frozen=True)
class Selection:
    ids: tuple[str, ...] = ()
    note: str = ""
    every: bool = False
    skip: bool = False


def read_selection(answer: str, labels: Mapping[str, str]) -> Selection:
    """The multi-select answer: picked labels (or `n.` lines) one per line, then any typed text as the last
    line, which becomes the block's note. A single line may also be "log 1 and 3" or "all new"."""
    text = (answer or "").strip()
    if not text or text.casefold().rstrip(".!") in _SKIP:
        return Selection(skip=True)
    if "\n" not in text and _LOG_ALL.match(text):
        return Selection(every=True)
    if "\n" not in text and _LOG_NUMBERS.match(text):
        return Selection(ids=numbers_of(text))
    ids: tuple[str, ...] = ()
    rest: tuple[str, ...] = ()
    for line in text.splitlines():
        picked = labels.get(line.strip()) or _leading(line, labels.values())
        if picked:
            ids = ids if picked in ids else (*ids, picked)
        elif line.strip():
            rest = (*rest, line.strip())
    return Selection(ids=ids, note=" ".join(rest))


def _leading(line: str, known: Sequence[str] | Any) -> str | None:
    match = _LEADING_NUMBER.match(line + " ")
    if not match:
        return None
    number = str(int(match.group(1)))
    return number if number in set(known) else None
