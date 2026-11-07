"""Pure: what the user asked for, parsed in code. Window, channel filter, only-decisions / only-mentions,
a typed handle ("I'm @priya"), a write ask (refused: read-only), json mode, an envelope as input and a
pasted transcript. No model call; all dates computed here."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

DEFAULT_WINDOW = timedelta(hours=24)
MIN_DAYS, MAX_DAYS = 1, 7
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

_CHANNEL = re.compile(r"(?<![\w<])#([a-z0-9][a-z0-9_\-]{0,79})", re.IGNORECASE)
_LAST_DAYS = re.compile(r"\b(?:last|past)\s+(\d{1,2}|a|one|two|three|four|five|six|seven)\s+days?\b", re.IGNORECASE)
_LAST_WEEK = re.compile(r"\b(?:last|past|this)\s+week\b", re.IGNORECASE)
_SINCE_YESTERDAY = re.compile(r"\bsince\s+yesterday\b|\byesterday\b", re.IGNORECASE)
_SINCE_DAY = re.compile(r"\bsince\s+(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)
_ONLY_DECISIONS = re.compile(r"\b(?:only|just)\s+(?:the\s+)?decisions?\b|\bdecisions?\s+only\b", re.IGNORECASE)
_ONLY_MENTIONS = re.compile(r"\b(?:only|just)\s+(?:my\s+|the\s+)?mentions?\b|\bmentions?\s+only\b"
                            r"|\banything\s+for\s+me\b", re.IGNORECASE)
_TYPED_HANDLE = re.compile(r"\b(?:i'?m|i am|my (?:handle|name) is)\s+@?([A-Za-z][\w.\-]{1,40})", re.IGNORECASE)
_WRITE_ASK = re.compile(
    r"\b(?:reply|respond|answer|post|send|react|forward|dm|message)\b.{0,40}\b(?:to|for me|in|on|them|him|her)\b"
    r"|\bmark\b.{0,30}\bread\b|\bjoin\b.{0,20}#|\bleave\b.{0,20}#", re.IGNORECASE)
_CATCH_UP_WORDS = re.compile(
    r"\bcatch(?:\s|-)?(?:me\s+)?up\b|\bcatch-?up\b|\bmiss(?:ed)?\b|\bmention|\bdecision|\bdecided\b|\bchannels?\b"
    r"|\bdms?\b|\bdirect messages?\b|#[a-z0-9]|\bwhat(?:'s| has| did)?\s+happen|\bsummar|\brecap\b|\bdigest\b"
    r"|\banything\s+for\s+me\b|\bagain\b|\bwhat'?s new\b|\bupdates?\b", re.IGNORECASE)
_AS_JSON = re.compile(r"\b(?:as|in) json\b|\bjson only\b|\bjson output\b|^\s*json\s*$", re.IGNORECASE)
_PASTE_LINE = re.compile(r"^\s*\[([^\]]{1,40})\]\s*([^:\n]{1,60}):\s*(.+)$")
_NUMBER_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


@dataclass(frozen=True)
class Request:
    text: str
    days: int | None = None  # an explicit window in whole days (clamped 1-7)
    since: datetime | None = None  # an explicit start (envelope input or "since Monday")
    channels: tuple[str, ...] = ()  # names without '#', casefolded
    only_decisions: bool = False
    only_mentions: bool = False
    typed_handle: str = ""
    wants_write: bool = False
    as_json: bool = False
    from_envelope: bool = False

    @property
    def explicit_window(self) -> bool:
        return self.days is not None or self.since is not None

    @property
    def looks_like_catch_up(self) -> bool:
        return bool(self.from_envelope or self.channels or self.only_decisions or self.only_mentions
                    or self.explicit_window or _CATCH_UP_WORDS.search(self.text))


def parse(text: str, now: datetime, json_metadata: bool = False) -> Request:
    envelope = _envelope(text)
    if envelope is not None:
        return _from_envelope(text, envelope, now, json_metadata)
    return Request(
        text=text,
        days=_days(text),
        since=_since(text, now),
        channels=tuple(dict.fromkeys(m.casefold() for m in _CHANNEL.findall(text))),
        only_decisions=bool(_ONLY_DECISIONS.search(text)),
        only_mentions=bool(_ONLY_MENTIONS.search(text)),
        typed_handle=_typed(text),
        wants_write=bool(_WRITE_ASK.search(text)) and not _CATCH_UP_WORDS.search(_strip_write(text)),
        as_json=json_metadata or bool(_AS_JSON.search(text)),
    )


def window(request: Request, now: datetime, stored_since: datetime | None) -> tuple[datetime, datetime]:
    """(start, end): explicit window, else the stored marker, else 24 h; clamped to 1-7 days back."""
    if request.since is not None:
        start = request.since
    elif request.days is not None:
        start = now - timedelta(days=request.days)
    elif stored_since is not None:
        start = stored_since
    else:
        start = now - DEFAULT_WINDOW
    earliest, latest = now - timedelta(days=MAX_DAYS), now - timedelta(minutes=1)
    return min(max(start, earliest), latest), now


def clamp_days(days: int) -> int:
    return max(MIN_DAYS, min(MAX_DAYS, days))


def wants_json(text: str) -> bool:
    return bool(_AS_JSON.search(text or ""))


def pasted_lines(text: str) -> tuple[tuple[str, str, str, str], ...]:
    """Pasted `[time] name: text` lines as (channel, time, name, text); a `#channel` line starts a block.
    Returned only when at least two lines match, so a one-line ask is never read as a paste."""
    channel = "pasted"
    found: list[tuple[str, str, str, str]] = []
    for raw in (text or "").splitlines():
        header = re.fullmatch(r"\s*#([a-z0-9][a-z0-9_\-]{0,79})\s*:?\s*", raw, re.IGNORECASE)
        if header:
            channel = header.group(1)
            continue
        match = _PASTE_LINE.match(raw)
        if match:
            found.append((channel, match.group(1).strip(), match.group(2).strip(), match.group(3).strip()))
    return tuple(found) if len(found) >= 2 else ()


def _strip_write(text: str) -> str:
    return _WRITE_ASK.sub(" ", text)


def _days(text: str) -> int | None:
    match = _LAST_DAYS.search(text)
    if match:
        raw = match.group(1).casefold()
        return clamp_days(int(raw) if raw.isdigit() else _NUMBER_WORDS[raw])
    return MAX_DAYS if _LAST_WEEK.search(text) else None


def _since(text: str, now: datetime) -> datetime | None:
    match = _SINCE_DAY.search(text)
    if match:
        back = (now.weekday() - _WEEKDAYS.index(match.group(1).casefold())) % 7 or 7
        day = (now - timedelta(days=back)).date()
        return datetime(day.year, day.month, day.day, tzinfo=UTC)
    if _SINCE_YESTERDAY.search(text):
        day = (now - timedelta(days=1)).date()
        return datetime(day.year, day.month, day.day, tzinfo=UTC)
    return None


def _typed(text: str) -> str:
    match = _TYPED_HANDLE.search(text)
    return match.group(1).rstrip(".,") if match else ""


def _envelope(text: str) -> dict | None:
    body = (text or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*|\s*```$", "", body)
    if not body.startswith("{"):
        return None
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) and ("since" in parsed or "channels" in parsed) else None


def _from_envelope(text: str, envelope: dict, now: datetime, json_metadata: bool) -> Request:
    since = _iso(envelope.get("since"))
    raw = envelope.get("channels")
    names = [str(c).lstrip("#").casefold() for c in raw if isinstance(c, str) and c.strip()] if isinstance(raw, list) else []
    return Request(text=text, since=since if since and since < now else None, channels=tuple(dict.fromkeys(names)),
                   as_json=json_metadata or bool(envelope.get("output") == "json"), from_envelope=True)


def _iso(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
