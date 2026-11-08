"""Classification of new changelog entries: tool-less, one model call per batch, batches run concurrently
under the turn's call budget. Every answer is checked in code: the quote must be in the entry, `what` must
share at least half its words with the entry (else the entry's first sentence), keywords must come from the
entry. An entry the model skipped or that could not be classified stays a change, as `other`."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from deps import TurnDeps
from llm import LlmError, complete_json
from records import CATEGORIES, Change, Entry
from words import first_sentence, norm, overlap, tokens

log = logging.getLogger("change_watcher.classify")
MAX_WHAT_WORDS = 25
MIN_WHAT_OVERLAP = 0.5
MAX_KEYWORDS = 4


@dataclass(frozen=True)
class Classified:
    changes: tuple[Change, ...]
    dropped: int = 0        # entries where a model answer was rejected (missing, bad quote, bad `what`)
    unclassified: int = 0   # entries no model answer covered (call failed or budget spent)


def _answers(raw: Any) -> list[Mapping[str, Any]] | None:
    rows = raw.get("entries") if isinstance(raw, Mapping) else None
    return [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, list) else None


def _keywords(raw: Any, entry: Entry) -> tuple[str, ...]:
    words = tokens(entry.text)
    picked = [str(k).casefold().strip() for k in raw if isinstance(k, str)] if isinstance(raw, list) else []
    kept = tuple(dict.fromkeys(k for k in picked if k in words))[:MAX_KEYWORDS]
    return kept or tuple(sorted(tokens(first_sentence(entry.text)), key=len, reverse=True)[:MAX_KEYWORDS])


def fallback(entry: Entry) -> Change:
    return Change(competitor=entry.competitor, source=entry.source, category="other",
                  what=first_sentence(entry.text), quote=None, date=entry.date,
                  keywords=_keywords(None, entry), entry_id=entry.id)


def verify(entry: Entry, raw: Mapping[str, Any], max_quote: int) -> tuple[Change, bool]:
    """The checked change and whether any part of the model's answer was rejected."""
    category = str(raw.get("category") or "").casefold()
    quote = " ".join(str(raw.get("quote") or "").split())
    what = " ".join(str(raw.get("what") or "").split())
    quote_ok = bool(quote) and len(quote) <= max_quote and norm(quote) in norm(entry.text)
    what_ok = bool(what) and len(what.split()) <= MAX_WHAT_WORDS and overlap(what, entry.text) >= MIN_WHAT_OVERLAP
    change = Change(competitor=entry.competitor, source=entry.source,
                    category=category if category in CATEGORIES else "other",
                    what=what if what_ok else first_sentence(entry.text), quote=quote if quote_ok else None,
                    date=entry.date, keywords=_keywords(raw.get("keywords"), entry), entry_id=entry.id)
    return change, not (quote_ok and what_ok and category in CATEGORIES)


async def _batch(deps: TurnDeps, batch: Sequence[Entry], gate: asyncio.Semaphore) -> Classified:
    rows = [{"entry_id": e.id, "competitor": e.competitor, "text": e.text} for e in batch]
    messages = ({"role": "system", "content": prompts.CLASSIFY_SYSTEM},
                {"role": "user", "content": prompts.fence_entries(rows)})
    async with gate:
        try:
            answers = await complete_json(deps, messages, _answers)
        except LlmError as e:
            log.warning("classification batch failed: %s", e)
            return Classified(tuple(fallback(e) for e in batch), unclassified=len(batch))
    by_id = {str(a.get("entry_id")): a for a in answers}
    changes, dropped = [], 0
    for entry in batch:
        raw = by_id.get(entry.id)
        if raw is None:
            changes.append(fallback(entry))
            dropped += 1
            continue
        change, rejected = verify(entry, raw, deps.config.max_quote_chars)
        changes.append(change)
        dropped += rejected
    return Classified(tuple(changes), dropped=dropped)


async def classify(deps: TurnDeps, new_entries: Sequence[Entry]) -> Classified:
    size = deps.config.classify_batch
    batches = [new_entries[i:i + size] for i in range(0, len(new_entries), size)]
    gate = asyncio.Semaphore(deps.config.classify_concurrency)
    results = await asyncio.gather(*(_batch(deps, b, gate) for b in batches))
    return Classified(tuple(c for r in results for c in r.changes), dropped=sum(r.dropped for r in results),
                      unclassified=sum(r.unclassified for r in results))
