"""Map: one extraction call per unit (concurrency MAP_CONCURRENCY). Every claim is checked against THE
SEGMENT of its own source in that unit: the quote must be there verbatim (>= 12 chars, cut to 240), and every
number in the claim's text must be in the quote or the segment. The map window closes MAP_RESERVE_SECS
before the turn deadline; units still running are cancelled and their sources named as not read."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import grounding as g
import llm
import prompts
from chunking import Segment, Unit
from model import KINDS, Claim

log = logging.getLogger("research-synthesizer.extract")

MAP_CONCURRENCY = 4
MAP_RESERVE_SECS = 150.0
QUOTE_MAX_CHARS = 240
TEXT_MAX_WORDS = 30
HINT_MAX_WORDS = 4


@dataclass(frozen=True)
class Extracted:
    claims: tuple[Claim, ...]
    dropped: int = 0
    irrelevant: int = 0
    read_units: tuple[Unit, ...] = ()
    unread_units: tuple[Unit, ...] = ()
    timed_out: bool = False
    failed: int = 0
    off_topic: bool = False


async def extract(deps: llm.TurnDeps, question: str, units: Sequence[Unit], context: Sequence[str] = (),
                  first_claim: int = 1) -> Extracted:
    gate = asyncio.Semaphore(MAP_CONCURRENCY)

    async def one(index: int, unit: Unit) -> tuple[int, str, Any]:
        async with gate:
            try:
                parsed = await llm.complete_json(deps, _messages(question, unit, context), prompts.EXTRACT_CONTRACT,
                                                 parse)
            except llm.LlmError:
                log.warning("extract call for unit %d failed", index + 1)
                return index, "failed", None
            return index, parsed["status"], parsed["claims"]

    tasks = [asyncio.create_task(one(i, u)) for i, u in enumerate(units)]
    done, pending = await asyncio.wait(tasks, timeout=max(0.0, deps.remaining() - MAP_RESERVE_SECS)) if tasks else ((), ())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    results = sorted(task.result() for task in done)
    return _collect(results, units, bool(pending), first_claim)


def _collect(results: list[tuple[int, str, Any]], units: Sequence[Unit], timed_out: bool, first_claim: int) -> Extracted:
    claims: list[Claim] = []
    dropped = irrelevant = 0
    for index, status, raw in results:
        if status != "ok":
            continue
        for entry in raw:
            claim = check_claim(entry, units[index], f"c{first_claim + len(claims)}")
            if claim is None:
                dropped += 1
            elif claim == "irrelevant":
                irrelevant += 1
            elif not any(c.source_id == claim.source_id and c.quote == claim.quote for c in claims):
                claims.append(claim)
    finished = {i for i, status, _ in results if status == "ok"}
    statuses = [status for _, status, _ in results]
    return Extracted(claims=tuple(claims), dropped=dropped, irrelevant=irrelevant,
                     read_units=tuple(u for i, u in enumerate(units) if i in finished),
                     unread_units=tuple(u for i, u in enumerate(units) if i not in finished), timed_out=timed_out,
                     failed=statuses.count("failed"), off_topic=bool(statuses) and all(s == "off_topic" for s in statuses))


def _messages(question: str, unit: Unit, context: Sequence[str]) -> list[dict]:
    blocks = [prompts.source_fence(s.source_id, _label(s), s.text) for s in unit.segments]
    body = ("Extract the claims each source makes that bear on the research question, with the source id from "
            "its marker and a verbatim quote from that same source.")
    return prompts.messages("EXTRACT", body, prompts.EXTRACT_CONTRACT, [prompts.question_block(question), *blocks],
                            context, kinds=True)


def _label(segment: Segment) -> str:
    return segment.label + (f" (part {segment.part} of {segment.parts})" if segment.parts > 1 else "")


def parse(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ("ok", "off_topic"):
        return None
    claims = data.get("claims") or []
    if not isinstance(claims, list):
        return None
    return {"status": data["status"], "claims": [dict(c) for c in claims if isinstance(c, Mapping)]}


def check_claim(entry: Mapping[str, Any], unit: Unit, claim_id: str) -> Claim | str | None:
    """A checked Claim, "irrelevant", or None when it fails a check."""
    segment = _segment_for(entry.get("source"), unit)
    kind = str(entry.get("kind") or "").strip().casefold()
    text = " ".join(str(entry.get("text") or "").split())
    if segment is None or kind not in KINDS or not text:
        return None
    norm = g.normalise(segment.text)
    quote = cap_quote(" ".join(str(entry.get("quote") or "").split()))
    if g.locate(quote, norm) is None or g.ungrounded_tokens(text, norm):
        return None
    if entry.get("relevant") is False:
        return "irrelevant"
    hint = " ".join(str(entry.get("topic_hint") or "").split()[:HINT_MAX_WORDS])
    return Claim(id=claim_id, source_id=segment.source_id, kind=kind, text=cut_words(text, TEXT_MAX_WORDS),
                 quote=quote, topic_hint=hint)


def _segment_for(source_id: Any, unit: Unit) -> Segment | None:
    wanted = str(source_id or "").strip().strip("[]").upper()
    if not wanted and len(unit.source_ids) == 1:
        return unit.segments[0]
    return next((s for s in unit.segments if s.source_id == wanted), None)


def cap_quote(quote: str) -> str:
    if len(quote) <= QUOTE_MAX_CHARS:
        return quote
    cut = quote[:QUOTE_MAX_CHARS]
    space = cut.rfind(" ")
    return cut[:space] if space > QUOTE_MAX_CHARS // 2 else cut


def cut_words(text: str, limit: int) -> str:
    words = text.split()
    return text if len(words) <= limit else " ".join(words[:limit]) + "..."
