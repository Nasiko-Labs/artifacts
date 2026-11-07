"""Pure: chat messages pasted into the request, one per line, read in code. Used when no chat tool is
connected (or the user simply pastes). A line looks like

    2026-11-03 #payments Mira: going with Postgres for the ledger
    [2026-11-03 10:02] Arjun: ship it

Date and channel are optional (a missing date is today, a missing channel is "pasted")."""

from __future__ import annotations

import re
from datetime import date

from models import Message

MIN_LINES = 2
MAX_MESSAGES = 200
_LINE = re.compile(
    r"^\s*(?:\[?(?P<day>\d{4}-\d{2}-\d{2})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\]?\s+)?"
    r"(?:#(?P<channel>[a-z0-9][a-z0-9_.-]{0,79})\s+)?"
    r"(?P<author>[A-Za-z][\w .'-]{0,40}?)\s*:\s+(?P<text>\S.*)$", re.IGNORECASE)
_URL_ONLY = re.compile(r"^\s*https?://", re.IGNORECASE)


def read_paste(text: str, today: date) -> tuple[Message, ...] | None:
    """The pasted messages, or None when fewer than two lines look like chat messages."""
    found: tuple[Message, ...] = ()
    for line in (text or "").splitlines():
        if _URL_ONLY.match(line):
            continue
        match = _LINE.match(line)
        if match is None:
            continue
        day = match.group("day") or today.isoformat()
        channel = (match.group("channel") or "pasted").lower()
        found = (*found, Message(id=f"m{len(found) + 1}", channel=channel, channel_name=channel,
                                 author="", author_name=match.group("author").strip(), ts=day, day=day,
                                 text=match.group("text").strip()))
    return found[:MAX_MESSAGES] if len(found) >= MIN_LINES else None
