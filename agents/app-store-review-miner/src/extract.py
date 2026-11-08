"""Complaint and praise extraction: one tool-less model call per batch of reviews, batches in parallel.
Every item is checked in code: the review id is in the batch, the quote is a substring of that review, the
label is short and carries no number the review does not. A batch the budget or deadline refused is
reported as not analysed (the step-limit salvage)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

import checks
import prompts
from deps import TurnDeps
from llm import LlmError, complete_json
from records import Review

log = logging.getLogger("review_miner.extract")


class _Item(BaseModel):
    label: str
    quote: str


class _Row(BaseModel):
    review_id: str
    complaints: list[_Item] = []
    praise: list[_Item] = []


class _Answer(BaseModel):
    reviews: list[_Row]


@dataclass(frozen=True)
class Extracted:
    by_review: Mapping[str, dict] = field(default_factory=dict)  # id → {"complaints": [...], "praise": [...]}
    returned: int = 0
    dropped: int = 0
    not_analysed: tuple[str, ...] = ()


def _validate(raw: object) -> _Answer | None:
    try:
        return _Answer.model_validate(raw)
    except ValidationError:
        return None


def _keep(items: Sequence[_Item], review: Review, max_chars: int) -> list[dict]:
    kept = []
    for item in items:
        label = checks.clean_label(item.label, review.text)
        if label and checks.quote_ok(item.quote, review.text, max_chars):
            kept.append({"label": label, "quote": item.quote.strip()})
    return kept


def _verify(answer: _Answer, batch: Sequence[Review], max_chars: int) -> tuple[dict, int, int]:
    by_id = {r.id: r for r in batch}
    out: dict[str, dict] = {r.id: {"complaints": [], "praise": []} for r in batch}
    returned = dropped = 0
    for row in answer.reviews:
        review = by_id.get(row.review_id)
        returned += len(row.complaints) + len(row.praise)
        if review is None:
            dropped += len(row.complaints) + len(row.praise)
            continue
        complaints = _keep(row.complaints, review, max_chars)
        praise = _keep(row.praise, review, max_chars)
        dropped += len(row.complaints) + len(row.praise) - len(complaints) - len(praise)
        out[review.id] = {"complaints": [*out[review.id]["complaints"], *complaints],
                          "praise": [*out[review.id]["praise"], *praise]}
    return out, returned, dropped


async def _batch(deps: TurnDeps, batch: Sequence[Review], gate: asyncio.Semaphore) -> tuple[dict, int, int] | None:
    rows = [{"review_id": r.id, "rating": r.rating, "title": r.title, "text": r.body} for r in batch]
    messages = ({"role": "system", "content": prompts.EXTRACT_SYSTEM},
                {"role": "user", "content": prompts.fence_reviews(rows)})
    async with gate:
        try:
            answer = await complete_json(deps, messages, _validate)
        except LlmError as e:
            log.warning("extract batch of %d not analysed: %s", len(batch), e)
            return None
    return _verify(answer, batch, deps.config.max_quote_chars)


async def extract(deps: TurnDeps, reviews: Sequence[Review]) -> Extracted:
    size = deps.config.batch_size
    batches = [reviews[i:i + size] for i in range(0, len(reviews), size)]
    gate = asyncio.Semaphore(deps.config.extract_concurrency)
    results = await asyncio.gather(*(_batch(deps, b, gate) for b in batches))
    by_review: dict[str, dict] = {}
    returned = dropped = 0
    missed: list[str] = []
    for batch, result in zip(batches, results, strict=True):
        if result is None:
            missed.extend(r.id for r in batch)
            continue
        out, got, lost = result
        by_review.update(out)
        returned, dropped = returned + got, dropped + lost
    return Extracted(by_review, returned, dropped, tuple(missed))
