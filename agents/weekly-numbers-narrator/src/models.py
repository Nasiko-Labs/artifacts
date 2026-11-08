"""The output contract (validated with pydantic): the catalog envelope (`summary`, `items`, `confidence`,
`needs_human`) plus this agent's `narrative`, `movers`, `facts`, `notes_used`, `post` and `coverage`.
`confidence` and `needs_human` are computed in code, never taken from the model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from facts import Facts

PostState = Literal["posted", "skipped", "copyable", "not_requested", "awaiting_approval"]
Confidence = Literal["high", "medium", "low"]
TINY_TABLE_ROWS = 3


class Mover(BaseModel):
    metric: str
    current: float
    previous: float
    delta: float
    pct: float | None
    direction: str
    rank: int
    display: dict[str, str] = Field(default_factory=dict)


class FactsOut(BaseModel):
    period_current: str
    period_previous: str
    comparison: str
    flat: list[str]
    excluded_partial: str | None


class NoteUsed(BaseModel):
    id: str
    metric: str | None
    text: str
    source: str


class Post(BaseModel):
    channel: str | None = None
    state: PostState = "not_requested"
    link: str | None = None


class Envelope(BaseModel):
    summary: str
    items: list[Mover]
    confidence: Confidence
    needs_human: bool
    narrative: str
    movers: list[Mover]
    facts: FactsOut
    notes_used: list[NoteUsed]
    post: Post
    coverage: list[str]


def confidence_of(facts: Facts, data_rows: int, used_template: bool, first_try: bool, dropped: int) -> Confidence:
    if not facts.period_previous or data_rows < TINY_TABLE_ROWS:
        return "low"
    if used_template or not first_try or facts.excluded_partial or facts.few_points or dropped:
        return "medium"
    return "high"


def needs_human_of(facts: Facts, confidence: Confidence) -> bool:
    top = facts.top
    return confidence == "low" or (top is not None and not facts.notes_for(top.metric))


def envelope(facts: Facts, narrative: str, confidence: Confidence, cited: tuple[str, ...], post: Post,
             coverage: tuple[str, ...] = ()) -> dict:
    movers = [Mover(metric=m.metric, current=m.current, previous=m.previous, delta=m.delta_value, pct=m.pct_value,
                    direction=m.direction, rank=m.rank,
                    display={"current": m.cur, "previous": m.prev, "delta": m.delta, "pct": m.pct})
              for m in facts.movers]
    names = ", ".join(f"{m.metric} {m.word} {m.pct}" for m in facts.movers) or "no material movers"
    summary = f"{facts.period_current} vs {facts.period_previous}: {names}." if facts.period_previous else \
        f"{facts.period_current}: no comparison period."
    used = [NoteUsed(**n.__dict__) for n in facts.notes if n.id in cited]
    env = Envelope(summary=summary, items=movers, confidence=confidence,
                   needs_human=needs_human_of(facts, confidence), narrative=narrative, movers=movers,
                   facts=FactsOut(period_current=facts.period_current, period_previous=facts.period_previous,
                                  comparison="same week last month" if facts.compare == "month" else "previous period",
                                  flat=list(facts.flat), excluded_partial=facts.excluded_partial),
                   notes_used=used, post=post, coverage=list(coverage))
    return env.model_dump()
