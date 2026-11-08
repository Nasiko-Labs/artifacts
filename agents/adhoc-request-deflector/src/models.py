"""Data model. `Row` is one dashboard from the index; `Intent` is what a request asks for; `Match`,
`Item` and `Envelope` are the output contract (catalog fields `match` and `reply`, plus the shared
`summary`, `items`, `confidence`, `needs_human`). Every value in the output is set by code: the model
only picks a row and names terms, and both are checked."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

Verdict = Literal["exact", "partial", "none"]
Confidence = Literal["high", "medium", "low"]
ItemState = Literal["draft", "posted", "skipped", "copyable"]
GRAINS = ("hourly", "daily", "weekly", "monthly", "quarterly", "yearly")


@dataclass(frozen=True)
class Row:
    """One dashboard in the index, as the sheet or the paste gave it."""

    id: str  # R1, R2 ... in index order
    name: str
    url: str
    description: str = ""
    owner: str = ""
    metrics: str = ""
    dimensions: str = ""
    tags: str = ""
    last_refreshed: str = ""
    tokens: frozenset[str] = frozenset()

    @property
    def text(self) -> str:
        return " ".join(p for p in (self.name, self.description, self.metrics, self.dimensions, self.tags) if p)


@dataclass(frozen=True)
class Index:
    title: str
    rows: tuple[Row, ...]
    source: Literal["sheet", "paste"]
    dropped: int = 0  # rows without a name or a url


@dataclass(frozen=True)
class Intent:
    is_data_request: bool
    metric_terms: tuple[str, ...] = ()
    dimension_terms: tuple[str, ...] = ()
    time_grain: str | None = None
    filters: tuple[str, ...] = ()

    @property
    def terms(self) -> tuple[str, ...]:
        return (*self.metric_terms, *self.dimension_terms)


@dataclass(frozen=True)
class Request:
    """One data request: typed or pasted by the user, or a message read from a chat channel."""

    text: str
    link: str | None = None
    requester: str | None = None
    channel_id: str = ""
    channel_label: str = ""
    thread_ts: str = ""


class Match(BaseModel):
    dashboard: str | None
    url: str | None
    confidence: Verdict
    covers: list[str]
    missing: list[str]
    why: str


class Item(BaseModel):
    request_text: str
    request_link: str | None
    requester: str | None
    match: Match
    reply: str
    state: ItemState
    confidence: Confidence
    channel_id: str = ""
    thread_ts: str = ""


class IndexInfo(BaseModel):
    title: str
    rows: int


class Envelope(BaseModel):
    summary: str
    items: list[Item]
    confidence: Confidence
    needs_human: bool
    match: Match | None
    reply: str | None
    requests: list[Item]
    index: IndexInfo | None
    coverage: list[str]


@dataclass(frozen=True)
class Outcome:
    """One turn's reply; `envelope` is None for a fixed reply. `state` is what to keep for follow-ups."""

    reply: str
    envelope: dict | None = None
    state: dict = field(default_factory=dict)
