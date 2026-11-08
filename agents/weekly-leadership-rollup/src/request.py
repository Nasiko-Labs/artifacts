"""Pure: what the user asked for, read in code from the message.

- window: "last N days" / "past N days" (clamped 3 to 14), "since Monday" / "this week" (back to the most
  recent such weekday, clamped the same way), else 7 days
- "stale after N days", "due soon within N days", a `Status map:` line (copied Reality Check options)
- a pasted status update: the lines after the first when there are at least two of them
- an envelope from a previous rollup (`{"disagreements": [...]}`): compared as still open vs resolved
- "as json" / "json only": the text part is the envelope
- "include unreported risks", and asks to post or send the rollup (declined: copy-only)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import statuses as st
from dates import DEFAULT_DAYS, MAX_DAYS, MIN_DAYS
from reality import DUE_SOON_DAYS, STALE_DAYS

MIN_PASTE_LINES = 2
MAX_THRESHOLD_DAYS = 90
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_LAST_N = re.compile(r"\b(?:last|past|previous)\s+(\d{1,3})\s+days?\b", re.IGNORECASE)
_SINCE = re.compile(r"\bsince\s+(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)
_THIS_WEEK = re.compile(r"\bthis week\b", re.IGNORECASE)
_STALE = re.compile(r"\bstale\s+(?:after|over|beyond)\s+(\d{1,3})\s+days?\b", re.IGNORECASE)
_SOON = re.compile(r"\bdue\s+soon\s+(?:within|in)\s+(\d{1,3})\s+days?\b", re.IGNORECASE)
_STATUS_MAP = re.compile(r"^\s*status map\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_JSON_MODE = re.compile(r"\b(?:as json|json only|in json)\b", re.IGNORECASE)
_UNREPORTED = re.compile(r"\bunreported risks?\b", re.IGNORECASE)
_POST = re.compile(r"^(?:ok[,.]?\s+|now\s+|please\s+|can you\s+|could you\s+|go ahead and\s+)*"
                   r"(?:post|send|share|publish|email|dm|forward)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    ask: str  # the message without a pasted update, an envelope or the status map line
    days: int = DEFAULT_DAYS
    period: str = "last 7 days"
    stale_days: int = STALE_DAYS
    soon_days: int = DUE_SOON_DAYS
    status_map: dict = field(default_factory=dict)
    paste: str = ""
    previous: tuple = ()
    wants_json: bool = False
    unreported: bool = False


def wants_post(text: str) -> bool:
    """An ask to post, send or share the rollup somewhere: declined (copy-only in v1)."""
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return bool(_POST.search(first.strip()))


def parse(text: str, today: date, json_metadata: bool = False) -> Request:
    body, previous = _envelope(text or "")
    status_line = _STATUS_MAP.search(body)
    status_map = st.parse_status_map(status_line.group(1)) if status_line else {}
    body = _STATUS_MAP.sub("", body) if status_line else body
    ask, paste = _split(body)
    days, period = _window(ask, today)
    return Request(ask=ask or "weekly leadership rollup", days=days, period=period,
                   stale_days=_days(_STALE, ask, STALE_DAYS), soon_days=_days(_SOON, ask, DUE_SOON_DAYS),
                   status_map=status_map, paste=paste, previous=previous,
                   wants_json=json_metadata or bool(_JSON_MODE.search(ask)), unreported=bool(_UNREPORTED.search(ask)))


def _window(ask: str, today: date) -> tuple[int, str]:
    last = _LAST_N.search(ask)
    if last:
        days = _clamp(int(last.group(1)))
        return days, f"last {days} days"
    since = _SINCE.search(ask)
    weekday = since.group(1).casefold() if since else ("monday" if _THIS_WEEK.search(ask) else None)
    if weekday is None:
        return DEFAULT_DAYS, f"last {DEFAULT_DAYS} days"
    back = (today.weekday() - _WEEKDAYS.index(weekday)) % 7 or 7
    days = _clamp(back)
    label = f"since {weekday.capitalize()} ({(today - timedelta(days=back)).isoformat()})"
    return days, label if days == back else f"{label}, widened to {days} days"


def _clamp(days: int) -> int:
    return max(MIN_DAYS, min(MAX_DAYS, days))


def _days(pattern: re.Pattern, text: str, default: int) -> int:
    found = pattern.search(text)
    return max(1, min(MAX_THRESHOLD_DAYS, int(found.group(1)))) if found else default


def _split(body: str) -> tuple[str, str]:
    lines = [line for line in body.strip().splitlines()]
    rest = [line for line in lines[1:] if line.strip()]
    if len(rest) >= MIN_PASTE_LINES:
        return lines[0].strip(), "\n".join(lines[1:]).strip()
    return " ".join(line.strip() for line in lines if line.strip()), ""


def _envelope(text: str) -> tuple[str, tuple]:
    """The message without a JSON envelope, and that envelope's disagreements (empty when none)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return text, ()
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return text, ()
    found = data.get("disagreements") if isinstance(data, dict) else None
    if not isinstance(found, list):
        return text, ()
    kept = tuple(d for d in found if isinstance(d, dict) and isinstance(d.get("item"), str))
    return (text[:start] + text[end + 1:]).strip(), kept
