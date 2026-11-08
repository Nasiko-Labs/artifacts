"""The data model and output contract (validated with pydantic): the rule spec, the event, and the catalog
envelope (`summary`, `items`, `confidence`, `needs_human`) plus this agent's `events`, `baseline`,
`first_run`, `rules`, `counts`, `post` and `coverage`. `confidence` and `needs_human` are computed in code
from checks, never taken from the model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

EventType = Literal["state_change", "row_changed", "new_row", "removed_row", "date_approaching", "date_overdue",
                    "required_blank"]
Confidence = Literal["high", "medium", "low"]
PostState = Literal["posted", "skipped", "copyable", "not_requested", "awaiting_approval"]
MAX_DAYS = 365


class When(BaseModel):
    column: str
    value: str


class DateRule(BaseModel):
    column: str
    days: int = Field(ge=0, le=MAX_DAYS)


class Required(BaseModel):
    column: str
    when: When | None = None


class RuleSpec(BaseModel):
    """What the user asked to watch, as stated (unset fields fall back to the defaults in rules.py)."""

    state_column: str | None = None
    key_column: str | None = None
    date_rules: list[DateRule] = Field(default_factory=list)
    required: list[Required] = Field(default_factory=list)
    track_new_rows: bool | None = None
    track_removed_rows: bool | None = None


class Event(BaseModel):
    type: EventType
    row_key: str
    row_label: str
    column: str | None = None
    old: str | None = None
    new: str | None = None
    detail: str = ""
    priority: int = 0
    repeat: bool = False

    @property
    def signature(self) -> str:
        return "\x1f".join((self.type, self.row_key, self.column or "", self.new or ""))


class Baseline(BaseModel):
    taken_at: str
    rows: int


class Post(BaseModel):
    channel: str | None = None
    state: PostState = "not_requested"
    link: str | None = None


class Envelope(BaseModel):
    summary: str
    items: list[Event]
    confidence: Confidence
    needs_human: bool
    events: list[Event]
    baseline: Baseline | None
    first_run: bool
    rules: dict
    counts: dict
    post: Post
    coverage: list[str]
    notes: list[str]
    sheet: dict


@dataclass(frozen=True)
class Quality:
    """The checks confidence is computed from."""

    pasted: bool = False
    first_run: bool = False
    duplicate_keys: bool = False
    bad_dates: bool = False
    capped: bool = False
    row_number_key: bool = False
    dropped: int = 0
    partial: bool = False


def confidence_of(quality: Quality) -> Confidence:
    """low: pasted data or a row-number key (reordering breaks the diff); medium: first run (changes unknown),
    duplicate keys, bad dates, the row cap, a dropped model value or a partial read; else high."""
    if quality.pasted or quality.row_number_key:
        return "low"
    if quality.first_run or quality.duplicate_keys or quality.bad_dates or quality.capped or quality.dropped \
            or quality.partial:
        return "medium"
    return "high"


def needs_human_of(events: list[Event]) -> bool:
    return any(e.type in ("date_overdue", "required_blank") for e in events)
