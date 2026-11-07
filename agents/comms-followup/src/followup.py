"""Deterministic follow-up analysis over a merged Slack + Gmail timeline.

The LLM transcribes whatever the MCP tools returned into a flat list of
`{author, timestamp, text, platform, channel?, is_me?}` dicts; everything that
needs to be *counted* or *dated* — who owes the next message, how many times
the user has already chased, how long they've been waiting — is computed here
instead, because that is exactly where a model silently miscounts.

Pure: no I/O, no clock reads (`now` is passed in), no mutation of inputs.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from enum import Enum

# Several messages of mine sent this close together (e.g. an email and a Slack
# ping fired off at the same time) are one chase, not several.
NUDGE_MERGE_WINDOW = timedelta(minutes=30)

# Which follow-up the draft being written would be (1 = the first nudge after
# the original ask). From this number on, the draft changes approach.
SECOND_FOLLOWUP_NUMBER = 2
ESCALATE_FROM_FOLLOWUP_NUMBER = 3

# Epoch values above this are milliseconds, not seconds (1e12 s is year ~33658).
_EPOCH_MS_THRESHOLD = 1e12
_QUOTE_CHARS = 280
_SECONDS_PER_DAY = 86_400


class Stage(str, Enum):
    NO_HISTORY = "no_history"
    REPLY = "reply"
    FIRST_FOLLOWUP = "first_followup"
    SECOND_FOLLOWUP = "second_followup"
    ESCALATE = "escalate"


class AnalysisInputError(ValueError):
    """The transcribed timeline is malformed — reported back to the LLM to fix."""


@dataclass(frozen=True)
class Message:
    author: str
    timestamp: datetime
    text: str
    platform: str
    channel: str | None
    is_me: bool


@dataclass(frozen=True)
class Touch:
    """One outreach of mine: a run of my messages inside the merge window."""

    at: datetime
    platforms: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class FollowupAnalysis:
    stage: Stage
    owed_by: str | None  # "me" | "them" | None (no history)
    touches: tuple[Touch, ...]  # my unanswered outreach, oldest first; [0] is the original ask
    their_last_reply: Message | None
    participants: tuple[str, ...]
    message_count: int
    now: datetime

    @property
    def followups_already_sent(self) -> int:
        return max(len(self.touches) - 1, 0)

    @property
    def next_followup_number(self) -> int:
        return len(self.touches)

    def to_dict(self) -> dict:
        first = self.touches[0] if self.touches else None
        last = self.touches[-1] if self.touches else None
        return {
            "stage": self.stage.value,
            "owed_by": self.owed_by,
            "followups_already_sent": self.followups_already_sent,
            "next_followup_number": self.next_followup_number,
            "original_ask": _touch_dict(first) if first else None,
            "previous_followups": [_touch_dict(t) for t in self.touches[1:]],
            "days_waiting": _days_between(first.at, self.now) if first else None,
            "days_since_last_touch": _days_between(last.at, self.now) if last else None,
            "their_last_reply": _message_dict(self.their_last_reply) if self.their_last_reply else None,
            "channels_used": sorted({p for t in self.touches for p in t.platforms}),
            "participants": list(self.participants),
            "message_count": self.message_count,
            "summary": self.summary(),
        }

    def summary(self) -> str:
        if self.stage is Stage.NO_HISTORY:
            return "No prior messages found — this would be a fresh outreach."
        if self.stage is Stage.REPLY and self.their_last_reply is not None:
            reply = self.their_last_reply
            return (
                f"They wrote last ({reply.platform}, {_fmt_date(reply.timestamp)}, "
                f"{_days_between(reply.timestamp, self.now)} days ago) — you owe the reply."
            )
        dated = ", ".join(f"{'+'.join(t.platforms)} {_fmt_date(t.at)}" for t in self.touches)
        sent = self.followups_already_sent
        return (
            f"Original ask + {sent} follow-up{'' if sent == 1 else 's'} with no reply ({dated}); "
            f"waiting {_days_between(self.touches[0].at, self.now)} days → drafting follow-up "
            f"#{self.next_followup_number} ({self.stage.value})."
        )


def analyze(raw_messages: list[dict], my_identities: list[str], now: datetime) -> FollowupAnalysis:
    """Parse the transcribed timeline and compute where the conversation stands."""
    identities = frozenset(i for i in (_normalize_identity(x) for x in my_identities) if i)
    messages = sorted(
        (_parse_message(raw, identities, index) for index, raw in enumerate(raw_messages)),
        key=lambda m: m.timestamp,
    )
    substantive = tuple(m for m in messages if m.text.strip())
    participants = tuple(sorted({m.author for m in substantive if not m.is_me}))

    their_last = next((m for m in reversed(substantive) if not m.is_me), None)
    unanswered_mine = tuple(
        m
        for m in substantive
        if m.is_me and (their_last is None or m.timestamp > their_last.timestamp)
    )
    touches = _group_touches(unanswered_mine)

    return FollowupAnalysis(
        stage=_stage_for(substantive, touches),
        owed_by=None if not substantive else ("them" if touches else "me"),
        touches=touches,
        their_last_reply=their_last,
        participants=participants,
        message_count=len(substantive),
        now=now,
    )


def _stage_for(substantive: tuple[Message, ...], touches: tuple[Touch, ...]) -> Stage:
    if not substantive:
        return Stage.NO_HISTORY
    if not touches:
        return Stage.REPLY
    next_number = len(touches)
    if next_number >= ESCALATE_FROM_FOLLOWUP_NUMBER:
        return Stage.ESCALATE
    if next_number >= SECOND_FOLLOWUP_NUMBER:
        return Stage.SECOND_FOLLOWUP
    return Stage.FIRST_FOLLOWUP


def _group_touches(mine: tuple[Message, ...]) -> tuple[Touch, ...]:
    groups: tuple[tuple[Message, ...], ...] = ()
    for message in mine:
        if groups and message.timestamp - groups[-1][-1].timestamp <= NUDGE_MERGE_WINDOW:
            groups = (*groups[:-1], (*groups[-1], message))
        else:
            groups = (*groups, (message,))
    return tuple(
        Touch(
            at=group[0].timestamp,
            platforms=tuple(sorted({m.platform for m in group})),
            text="\n".join(m.text.strip() for m in group),
        )
        for group in groups
    )


def _parse_message(raw: object, identities: frozenset[str], index: int) -> Message:
    if not isinstance(raw, dict):
        raise AnalysisInputError(f"messages[{index}] must be an object, got {type(raw).__name__}")
    author = str(raw.get("author") or "").strip()
    if not author:
        raise AnalysisInputError(f"messages[{index}] is missing `author`")
    if raw.get("timestamp") in (None, ""):
        raise AnalysisInputError(f"messages[{index}] is missing `timestamp`")
    explicit_is_me = raw.get("is_me")
    return Message(
        author=author,
        timestamp=parse_timestamp(raw["timestamp"], index),
        text=str(raw.get("text") or ""),
        platform=str(raw.get("platform") or "unknown").strip().lower(),
        channel=str(raw["channel"]) if raw.get("channel") else None,
        is_me=explicit_is_me if isinstance(explicit_is_me, bool) else _is_me(author, identities),
    )


def parse_timestamp(value: object, index: int = 0) -> datetime:
    """Accepts epoch seconds/ms (incl. Slack's "1698412345.123456"), ISO 8601,
    and RFC 2822 (an email `Date:` header). Naive values are taken as UTC."""
    if isinstance(value, bool):
        raise AnalysisInputError(f"messages[{index}].timestamp is not a timestamp: {value!r}")
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    text = str(value).strip()
    try:
        return _from_epoch(float(text))
    except ValueError:
        pass
    for parse in (datetime.fromisoformat, parsedate_to_datetime):
        try:
            return _as_utc(parse(text))
        except (TypeError, ValueError):
            continue
    raise AnalysisInputError(
        f"messages[{index}].timestamp {text!r} is not epoch seconds/ms, ISO 8601 or RFC 2822"
    )


def _from_epoch(seconds: float) -> datetime:
    scaled = seconds / 1000 if seconds > _EPOCH_MS_THRESHOLD else seconds
    return datetime.fromtimestamp(scaled, tz=UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _normalize_identity(value: str) -> str:
    text = str(value).strip().lower()
    if "<" in text and ">" in text:  # "Ankit <a@x.com>" → "a@x.com"; "<@U123>" → "@u123"
        text = text[text.index("<") + 1 : text.rindex(">")]
    return text.lstrip("@").strip()


def _is_me(author: str, identities: frozenset[str]) -> bool:
    return _normalize_identity(author) in identities


def _days_between(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() / _SECONDS_PER_DAY, 1)


def _fmt_date(value: datetime) -> str:
    return f"{value.strftime('%b')} {value.day}"


def _touch_dict(touch: Touch) -> dict:
    return {"at": touch.at.isoformat(), "platforms": list(touch.platforms), "text": _quote(touch.text)}


def _message_dict(message: Message) -> dict:
    return {
        "author": message.author,
        "at": message.timestamp.isoformat(),
        "platform": message.platform,
        "channel": message.channel,
        "text": _quote(message.text),
    }


def _quote(text: str) -> str:
    stripped = text.strip()
    return stripped if len(stripped) <= _QUOTE_CHARS else stripped[: _QUOTE_CHARS - 1] + "…"
