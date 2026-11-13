"""The signals step: batched model calls (20 tickets each, 4 at a time) that return a category and quoted
signals per ticket, then the code check (quote verbatim in that ticket, known names, category enum, a
data_loss quote that says data was lost), one
re-ask for skipped tickets, and the lexicon pass. Batches that fail or miss the deadline are lexicon-only."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

import lexicon
import prompts
from llm import LlmError, TurnDeps, complete_json
from model import CATEGORIES, LEXICON_ONLY, MODEL, NOT_ANALYSED, SIGNALS, Signal, Ticket

log = logging.getLogger("ticket-triager.signals")

BATCH_SIZE = 20
CONCURRENCY = 4
BODY_MAX_CHARS = 1_500
MIN_QUOTE_CHARS = 8
QUOTE_MAX_CHARS = 160
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "…": "..."})
_SPACES = re.compile(r"\s+")
# A data_loss quote must say stored data was destroyed; "export drops rows" is a data_issue, not lost data.
_LOSS_WORDS = re.compile(r"\b(?:lost|lose|losing|loss|deleted|deleting|wiped|erased|disappeared|gone|corrupt\w*|"
                         r"overwritten|purged|destroyed)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Outcome:
    status: str                       # ok | off_topic | failed
    tickets: tuple[Ticket, ...] = ()
    dropped_quotes: int = 0


def normalise(text: str) -> str:
    return _SPACES.sub(" ", (text or "").translate(_QUOTES).casefold()).strip()


def check(entry: dict, ticket: Ticket) -> tuple[str, tuple[Signal, ...], int]:
    """(category, verified signals, dropped count) for one model entry against its ticket."""
    category = entry.get("category") if entry.get("category") in CATEGORIES else "other"
    haystack = normalise(ticket.text)
    kept: dict[str, Signal] = {}
    dropped = 0
    for raw in entry.get("signals") or ():
        if not isinstance(raw, dict) or raw.get("name") not in SIGNALS:
            continue
        quote = str(raw.get("quote") or "").strip()
        needle = normalise(quote).strip("\"'.,;:!? ")
        unsupported = raw["name"] == "data_loss" and not _LOSS_WORDS.search(quote)
        if len(needle) < MIN_QUOTE_CHARS or len(quote) > QUOTE_MAX_CHARS * 2 or needle not in haystack or unsupported:
            dropped += 1
            continue
        kept.setdefault(raw["name"], Signal(raw["name"], quote[:QUOTE_MAX_CHARS]))
    return category, tuple(kept.values()), dropped


def _validate(data) -> dict | None:
    if not isinstance(data, dict):
        return None
    if data.get("status") == "off_topic":
        return {"status": "off_topic", "tickets": []}
    entries = data.get("tickets")
    if not isinstance(entries, list):
        return None
    return {"status": "ok", "tickets": [e for e in entries if isinstance(e, dict)]}


def _block(ticket: Ticket) -> str:
    return f"Subject: {ticket.subject}\nBody: {ticket.body[:BODY_MAX_CHARS]}".strip()


async def _batch(deps: TurnDeps, keyed: Sequence[tuple[str, Ticket]], policy: Sequence[str],
                 gate: asyncio.Semaphore) -> dict | None:
    async with gate:
        messages = prompts.signals_messages([(k, _block(t)) for k, t in keyed], policy)
        try:
            return await complete_json(deps, messages, prompts.SIGNALS_CONTRACT, _validate)
        except LlmError as e:
            log.warning("signals batch of %d failed: %s", len(keyed), e)
            return None


async def analyse(deps: TurnDeps, tickets: Sequence[Ticket], policy: Sequence[str] = ()) -> Outcome:
    keyed = [(f"t{i}", t) for i, t in enumerate(tickets, start=1)]
    batches = [keyed[i:i + BATCH_SIZE] for i in range(0, len(keyed), BATCH_SIZE)]
    gate = asyncio.Semaphore(CONCURRENCY)
    answers = await asyncio.gather(*(_batch(deps, b, policy, gate) for b in batches))
    if all(a is None for a in answers):
        return Outcome("failed")
    if all(a is None or a["status"] == "off_topic" for a in answers):
        return Outcome("off_topic")
    found: dict[str, dict] = {}
    failed: set[str] = set()
    for batch, answer in zip(batches, answers, strict=True):
        if answer is None:
            failed |= {k for k, _ in batch}
            continue
        found |= {str(e.get("id")): e for e in answer["tickets"] if str(e.get("id")) in dict(batch)}
    skipped = [(k, t) for k, t in keyed if k not in found and k not in failed]
    if skipped:
        again = await _batch(deps, skipped[:BATCH_SIZE], policy, gate)
        if again and again["status"] == "ok":
            found |= {str(e.get("id")): e for e in again["tickets"] if str(e.get("id")) in dict(skipped)}
    return _merge(keyed, found, failed)


def _merge(keyed: list[tuple[str, Ticket]], found: dict[str, dict], failed: set[str]) -> Outcome:
    out, dropped_total = [], 0
    for key, ticket in keyed:
        if key in found:
            category, signals, dropped = check(found[key], ticket)
            dropped_total += dropped
            out.append(replace(ticket, category=category, signals=lexicon.merge(ticket, signals), analysis=MODEL))
        elif key in failed:
            out.append(lexicon_only(ticket))
        else:
            out.append(replace(ticket, category="other", signals=lexicon.scan(ticket), analysis=NOT_ANALYSED))
    return Outcome("ok", tuple(out), dropped_total)


_LEXICON_CATEGORY = (("security_concern", "security"), ("service_down", "outage"), ("data_loss", "data_issue"),
                     ("churn_threat", "cancellation"))


def lexicon_only(ticket: Ticket) -> Ticket:
    """A ticket the model never read: keyword signals only, a category guessed from them, else other."""
    signals = lexicon.scan(ticket)
    names = {s.name for s in signals}
    category = next((c for name, c in _LEXICON_CATEGORY if name in names), "other")
    return replace(ticket, category=category, signals=signals, analysis=LEXICON_ONLY)
