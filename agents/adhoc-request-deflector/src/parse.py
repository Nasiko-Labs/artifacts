"""Pure: the user's message read in code. Mode (one request, or a channel scan), channel, window (days,
default 3, clamped 1-14; "since Monday" counted in code), an index title the user names, json mode,
draft-only, follow-ups (continue, post #n), and an envelope as input. No model call happens here."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

DEFAULT_DAYS = 3
MIN_DAYS, MAX_DAYS = 1, 14
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

Mode = Literal["single", "scan", "continue", "post"]

_JSON = re.compile(r"\b(?:as|in)\s+json\b|\bjson\s+only\b", re.IGNORECASE)
_DRAFT_ONLY = re.compile(r"\b(?:just|only)\s+draft(?:\s+it)?\b|\bdraft\s+only\b|\bdon'?t\s+post(?:\s+it)?\b",
                         re.IGNORECASE)
_CHANNEL = re.compile(r"(?<![\w&])#([A-Za-z0-9][\w.-]{0,79})")
_SCAN_VERB = re.compile(r"^\s*(?:please\s+)?(?:check|scan|look\s+(?:at|through|in)|go\s+through|review|read|triage|"
                        r"deflect|what(?:'s| is)\s+(?:new\s+)?in)\b", re.IGNORECASE)
_SINCE_DAY = re.compile(r"\bsince\s+(?:last\s+)?(" + "|".join(WEEKDAYS) + r")\b", re.IGNORECASE)
_DAYS = re.compile(r"\b(?:last|past)?\s*(\d{1,3})\s*days?\b", re.IGNORECASE)
_THIS_WEEK = re.compile(r"\bthis\s+week\b", re.IGNORECASE)
_LAST_WEEK = re.compile(r"\b(?:last|past)\s+week\b", re.IGNORECASE)
_YESTERDAY = re.compile(r"\b(?:since\s+)?yesterday\b", re.IGNORECASE)
_TODAY = re.compile(r"\btoday\b", re.IGNORECASE)
_INDEX_TITLE = re.compile(r"\b(?:index|sheet|spreadsheet|list|using|from)\s+[\"“']([^\"”']{2,80})[\"”']", re.IGNORECASE)
_CONTINUE = re.compile(r"^\s*(?:continue|next|more|keep going|the rest|go on)\s*[.!]*\s*$", re.IGNORECASE)
_POST = re.compile(r"^\s*(?:post|send|reply(?:\s+to)?)\s+(?:it|that|this|#?(\d{1,2})|the\s+(first|second|third|fourth|"
                   r"fifth|last)(?:\s+one)?)(?:\s+(?:one|in\s+the\s+thread|in\s+thread))?\s*[.!]*\s*$", re.IGNORECASE)
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Parsed:
    mode: Mode
    text: str = ""            # the request (single mode), cleaned
    channel: str | None = None
    days: int = DEFAULT_DAYS
    index_title: str | None = None
    as_json: bool = False
    draft_only: bool = False
    post_number: int = 0       # post mode: 1-based item number; 0 = the only/first draft; -1 = the last one
    from_envelope: bool = False


def wants_json(text: str) -> bool:
    return bool(_JSON.search(text or ""))


def clamp_days(value: int) -> int:
    return max(MIN_DAYS, min(MAX_DAYS, value))


def window_days(text: str, today: date) -> int:
    match = _SINCE_DAY.search(text)
    if match:
        back = (today.weekday() - WEEKDAYS.index(match.group(1).casefold())) % 7
        return clamp_days(back or 7)
    match = _DAYS.search(text)
    if match:
        return clamp_days(int(match.group(1)))
    if _THIS_WEEK.search(text):
        return clamp_days(today.weekday() or 1)
    if _LAST_WEEK.search(text):
        return 7
    if _YESTERDAY.search(text):
        return 1
    if _TODAY.search(text):
        return 1
    return DEFAULT_DAYS


def parse(text: str, today: date, as_json: bool = False) -> Parsed:
    envelope = _envelope(text)
    if envelope is not None:
        return envelope
    body = text or ""
    as_json = as_json or wants_json(body)
    body = _JSON.sub(" ", body)
    draft_only = bool(_DRAFT_ONLY.search(body))
    body = _DRAFT_ONLY.sub(" ", body)
    if _CONTINUE.match(body):
        return Parsed("continue", as_json=as_json)
    post = _POST.match(body)
    if post:
        number = int(post.group(1)) if post.group(1) else (-1 if post.group(2) == "last" else
                                                            _ORDINALS.get((post.group(2) or "").casefold(), 0))
        return Parsed("post", as_json=as_json, post_number=number)
    title_match = _INDEX_TITLE.search(body)
    title = title_match.group(1).strip() if title_match else None
    channels = _CHANNEL.findall(body)
    if channels and _SCAN_VERB.match(body):
        return Parsed("scan", channel=channels[0].rstrip("."), days=window_days(body, today), index_title=title,
                      as_json=as_json, draft_only=draft_only)
    cleaned = _SPACES.sub(" ", body).strip(" ,;:-")
    return Parsed("single", text=cleaned, index_title=title, as_json=as_json, draft_only=draft_only)


def _envelope(text: str) -> Parsed | None:
    stripped = (text or "").strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    request = data.get("request")
    if isinstance(request, dict):  # a previous step's item: {"request_text": ...}
        request = request.get("request_text")
    if not isinstance(request, str) or not request.strip():
        return None
    return Parsed("single", text=" ".join(request.split()), as_json=True, from_envelope=True, draft_only=True)
