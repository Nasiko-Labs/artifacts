"""Data model: messages and threads as the tools returned them, and the output contract.

`PriorAnswer` and `Envelope` are the catalog contract (`prior_answers[]`) plus the shared envelope
fields (`summary`, `items`, `confidence`, `needs_human`). Every value in them is set by code from tool
output; the model only chooses which thread answers and which words to quote, and both are checked."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

Match = Literal["strong", "partial"]
Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class Message:
    channel_id: str
    ts: str
    author: str
    text: str
    user_id: str = ""
    channel_name: str = ""
    thread_ts: str = ""
    reply_count: int | None = None  # None: the tool did not say
    permalink: str = ""
    reactions: tuple[str, ...] = ()
    time_label: str = ""  # pasted text: the time as written

    @property
    def time(self) -> float | None:
        try:
            return float(self.ts)
        except ValueError:
            return None

    @property
    def root_ts(self) -> str:
        return self.thread_ts or self.ts

    @property
    def key(self) -> str:
        return f"{self.channel_id}:{self.root_ts}"

    @property
    def channel_label(self) -> str:
        name = self.channel_name.lstrip("#")
        return f"#{name}" if name else self.channel_id


@dataclass(frozen=True)
class Hit:
    """One message a search (or the history scan) returned, and which queries found it."""

    message: Message
    queries: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Thread:
    key: str
    channel_id: str
    channel_label: str
    root_ts: str
    root: Message | None
    replies: tuple[Message, ...] = ()
    queries: frozenset[str] = frozenset()
    reply_count: int | None = None
    score: float = 0.0

    @property
    def messages(self) -> tuple[Message, ...]:
        return ((self.root,) if self.root else ()) + self.replies

    @property
    def has_replies(self) -> bool:
        return bool(self.replies) or bool(self.reply_count)


class PriorAnswer(BaseModel):
    question: str
    answer: str
    answered_by: str
    answered_at: str
    channel: str
    link: str | None
    match: Match
    acknowledged: bool
    age_days: int | None
    stale: bool
    thread_id: str = Field(description="the thread key, for EXPAND")


class Searched(BaseModel):
    channels: list[str]
    months: int
    hits: int


class Envelope(BaseModel):
    summary: str
    items: list[PriorAnswer]
    confidence: Confidence
    needs_human: bool
    prior_answers: list[PriorAnswer]
    question: str
    restated_question: str | None
    queries: list[str]
    searched: Searched
    coverage: list[str]


@dataclass(frozen=True)
class Outcome:
    """One turn's reply; `envelope` is None for a fixed reply. `state` is what to keep for follow-ups."""

    reply: str
    envelope: dict | None = None
    state: dict = field(default_factory=dict)


def utc(ts: str) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(ts), UTC)
    except (ValueError, OverflowError, OSError):
        return None
