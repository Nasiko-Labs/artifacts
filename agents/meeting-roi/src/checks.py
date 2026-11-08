"""Pure: turn a step's JSON answer into checked records. The guarantee, not the prompt.

The model only transcribes. An event survives only when its id appears in this turn's tool output and
its times parse and sit in the window; its URL, organizer and attendee emails are kept only when the
tool output contains them, and every count is recomputed from the attendee list (any total the model
gives is ignored). A notes doc needs a URL from tool output. A decision quote must appear verbatim in
the tool output; a chat timestamp too. Chat message text is never read."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from records import (
    Attendee,
    ChatFollowup,
    Decisions,
    Event,
    NotesFinding,
    OccurrenceCount,
    Series,
)

MAX_EVENTS = 300
MAX_ATTENDEES = 60
MAX_DOC_CHARS = 8_000
MAX_QUOTES = 3
MAX_CHAT_MESSAGES = 300
MAX_EVENT_HOURS = 8
FOLLOWUP_HOURS = 24
DECISION_WORDS = ("decided", "decision", "agreed", "approved", "chose", "chosen", "will", "going with",
                  "signed off", "resolved")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE = " .,;:!?\"'"
_EPOCH = re.compile(r"^\s*(\d{9,11})(?:\.\d+)?\s*$")
_HAS_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_CHANNEL = re.compile(r"#[a-z0-9][a-z0-9_.-]{0,79}", re.IGNORECASE)
_RESPONSES = {"accepted": "accepted", "accept": "accepted", "yes": "accepted", "tentative": "tentative",
              "maybe": "tentative", "declined": "declined", "decline": "declined", "no": "declined",
              "needsaction": "none", "needs_action": "none", "none": "none", "noresponse": "none"}


def normalise(text: str) -> str:
    """Case, whitespace, curly quotes and JSON escapes do not change what a sentence says."""
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


@dataclass(frozen=True)
class Seen:
    """This step's raw tool output, the only source a record may come from."""

    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "_norm", normalise(self.text))

    def has(self, value: object) -> bool:
        if not isinstance(value, str):
            return False
        wanted = normalise(value).strip(_EDGE)
        return bool(wanted) and wanted in self._norm  # type: ignore[attr-defined]


def parse_answer(text: str) -> dict | None:
    """The JSON object in a model answer (code fences and surrounding prose tolerated), or None."""
    body = _FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def items(answer: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    raw = answer.get(key)
    return [i for i in raw if isinstance(i, Mapping)] if isinstance(raw, list) else []


def parse_time(value: object) -> datetime | None:
    """An aware ISO date-time with a time of day (an all-day date is not a meeting), or epoch seconds as UTC."""
    if not isinstance(value, str | int | float) or isinstance(value, bool):
        return None
    text = str(value).strip()
    epoch = _EPOCH.match(text)
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)), tz=UTC)
    if not _HAS_TIME.match(text):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)  # the event's own offset is kept


def _str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _seen_str(value: object, seen: Seen) -> str:
    return _str(value) if seen.has(value) else ""


# --- calendar -------------------------------------------------------------------------------------

def check_events(answer: Mapping[str, Any], seen: Seen, start: datetime,
                 end: datetime) -> tuple[tuple[Event, ...], bool]:
    """Events whose id the tools returned and that started inside [start, end]; (events, capped)."""
    kept: dict[str, Event] = {}
    for raw in items(answer, "events"):
        event = _event(raw, seen, start, end)
        if event is not None:
            kept.setdefault(event.id, event)
    events = tuple(sorted(kept.values(), key=lambda e: e.start))
    return events[-MAX_EVENTS:], len(events) > MAX_EVENTS


def _event(raw: Mapping[str, Any], seen: Seen, start: datetime, end: datetime) -> Event | None:
    event_id, title = _str(raw.get("id")), _str(raw.get("title"))
    begins, ends = parse_time(raw.get("start")), parse_time(raw.get("end"))
    if not event_id or not seen.has(event_id) or not title or not seen.has(title) or begins is None or ends is None:
        return None
    if not start <= begins <= end or ends <= begins or ends - begins > timedelta(hours=MAX_EVENT_HOURS):
        return None
    attendees = _attendees(raw.get("attendees"), seen)
    series_id = _seen_str(raw.get("series_id"), seen) or None
    return Event(id=event_id, title=title, start=begins, end=ends, series_id=series_id,
                 url=_seen_str(raw.get("url"), seen), organizer_email=_seen_str(raw.get("organizer_email"), seen),
                 organizer_self=raw.get("organizer_self") is True, attendees=attendees[:MAX_ATTENDEES],
                 attachments=_attachments(raw.get("attachments"), seen), channel_hint=_channel(raw, seen),
                 attendees_capped=len(attendees) > MAX_ATTENDEES)


def _attendees(raw: object, seen: Seen) -> tuple[Attendee, ...]:
    out: dict[str, Attendee] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        email = _str(item.get("email")).casefold()
        if "@" not in email or not seen.has(email):
            continue
        response = _RESPONSES.get(_str(item.get("response")).casefold().replace(" ", ""), "none")
        out.setdefault(email, Attendee(email=email, response=response, is_self=item.get("self") is True))
    return tuple(out.values())


def _attachments(raw: object, seen: Seen) -> tuple[tuple[str, str], ...]:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, Mapping) and seen.has(item.get("url")):
            out.append((_str(item.get("title")) or "attachment", _str(item.get("url"))))
    return tuple(out[:3])


def _channel(raw: Mapping[str, Any], seen: Seen) -> str:
    """A `#channel` named in the title, else one the model read in the description (seen in the output)."""
    in_title = _CHANNEL.search(_str(raw.get("title")))
    if in_title:
        return in_title.group(0).casefold()
    hint = _str(raw.get("channel_hint"))
    return hint.casefold() if _CHANNEL.fullmatch(hint) and seen.has(hint) else ""


# --- notes ----------------------------------------------------------------------------------------

def check_docs(answer: Mapping[str, Any], seen: Seen, series: Sequence[Series]) -> dict[str, NotesFinding]:
    """One notes doc per series title (first wins); a doc needs a URL the tools returned."""
    titles = {normalise(s.title): s.title for s in series}
    found: dict[str, NotesFinding] = {}
    for raw in items(answer, "docs"):
        title = titles.get(normalise(_str(raw.get("series_title"))))
        url = _str(raw.get("url"))
        if title is None or title in found or not url or not seen.has(url):
            continue
        found[title] = NotesFinding(series_title=title, doc_title=_seen_str(raw.get("doc_title"), seen) or "notes",
                                    url=url, modified_at=parse_time(raw.get("modified_at")),
                                    text=_str(raw.get("text"))[:MAX_DOC_CHARS])
    return found


def check_decisions(answer: Mapping[str, Any] | None, seen: Seen, doc_text: str) -> Decisions:
    """Quotes must be verbatim in the notes tool output and the doc text; a decision quote must state a
    choice. A claim whose quotes do not survive becomes `unclear`."""
    if answer is None:
        return Decisions("unclear", (), "unclear", ())
    doc = Seen(doc_text)
    decisions = _quotes(answer.get("decision_quotes"), seen, doc, require_decision_word=True)
    actions = _quotes(answer.get("action_quotes"), seen, doc, require_decision_word=False)
    return Decisions(_claim(answer.get("has_decisions"), answer.get("decision_quotes"), decisions), decisions,
                     _claim(answer.get("has_action_items"), answer.get("action_quotes"), actions), actions)


def _quotes(raw: object, seen: Seen, doc: Seen, require_decision_word: bool) -> tuple[str, ...]:
    out = []
    for quote in raw if isinstance(raw, list) else []:
        text = _str(quote)
        if not text or not seen.has(text) or not doc.has(text):
            continue
        if require_decision_word and not is_decision(text):
            continue
        out.append(text)
    return tuple(dict.fromkeys(out))[:MAX_QUOTES]


def is_decision(sentence: str) -> bool:
    lowered = f" {normalise(sentence)} "
    return any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in DECISION_WORDS)


def _claim(value: object, raw_quotes: object, kept: tuple[str, ...]) -> str:
    claim = value if isinstance(value, str) else ("true" if value is True else "false" if value is False else "")
    claim = claim.casefold()
    if claim == "true":
        offered = len(raw_quotes) if isinstance(raw_quotes, list) else 0
        return "true" if kept and len(kept) == min(offered, MAX_QUOTES) else "unclear"
    return claim if claim in ("false", "unclear") else "unclear"


# --- chat -----------------------------------------------------------------------------------------

def check_chat(answer: Mapping[str, Any], seen: Seen, series: Sequence[Series]) -> dict[str, ChatFollowup]:
    """Per series: messages counted within 24 h after each occurrence. Only timestamps and permalinks
    the tools returned are used; the count is computed here."""
    by_title = {normalise(s.title): s for s in series}
    out: dict[str, ChatFollowup] = {}
    for raw in items(answer, "channels"):
        target = by_title.get(normalise(_str(raw.get("series_title"))))
        channel = _str(raw.get("channel"))
        if target is None or target.title in out or not channel or not seen.has(channel.lstrip("#")):
            continue
        stamps = _stamps(raw.get("messages"), seen)
        out[target.title] = ChatFollowup(target.title, channel if channel.startswith("#") else f"#{channel}",
                                         tuple(_count(e.start, stamps) for e in target.events))
    return out


def _stamps(raw: object, seen: Seen) -> tuple[tuple[datetime, str | None], ...]:
    out: dict[datetime, str | None] = {}
    for item in (raw if isinstance(raw, list) else [])[:MAX_CHAT_MESSAGES]:
        if not isinstance(item, Mapping):
            continue
        ts = item.get("ts")
        at = parse_time(ts)
        if at is None or not (seen.has(str(ts)) or str(int(at.timestamp())) in seen.text):
            continue
        link = _str(item.get("permalink"))
        out.setdefault(at, link if link and seen.has(link) else None)
    return tuple(out.items())


def _count(start: datetime, stamps: Sequence[tuple[datetime, str | None]]) -> OccurrenceCount:
    inside = sorted((at, link) for at, link in stamps if start <= at < start + timedelta(hours=FOLLOWUP_HOURS))
    link = next((link for _, link in inside if link), None)
    return OccurrenceCount(occurrence_start=start, count=len(inside), permalink=link)


def without_self_declined(events: Sequence[Event]) -> tuple[Event, ...]:
    return tuple(e for e in events if not e.self_declined)


def with_series_channel(series: Series) -> Series:
    hint = next((e.channel_hint for e in series.events if e.channel_hint), "")
    return replace(series, channel_hint=hint)
