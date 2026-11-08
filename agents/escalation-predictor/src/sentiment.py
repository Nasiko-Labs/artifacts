"""The sentiment step: one tool-less call per batch of tickets labels the tone of each customer's latest
messages (fenced). A quote counts only when it appears verbatim in that ticket's own customer messages;
otherwise it is dropped, so a negative label without a verified quote scores nothing."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace

import prompts
import records
import verify
from budget import TurnLimitReached
from deps import TurnDeps
from reader import ask_once
from records import Sentiment, Ticket

log = logging.getLogger("escalation.sentiment")


@dataclass(frozen=True)
class SentimentOutcome:
    by_ticket: dict[str, Sentiment]
    dropped_quotes: int = 0
    failed_batches: int = 0


def customer_lines(ticket: Ticket, most: int) -> tuple[str, ...]:
    return tuple(m.text for m in ticket.comments if m.author_role == "customer" and m.public)[-most:]


async def label(deps: TurnDeps, tickets: Sequence[Ticket]) -> SentimentOutcome:
    cfg = deps.config
    texts = {t.id: customer_lines(t, cfg.customer_msgs_for_sentiment) for t in tickets}
    todo = [t for t in tickets if texts[t.id]]
    batches = [todo[i:i + cfg.sentiment_batch] for i in range(0, len(todo), cfg.sentiment_batch)]
    answers = await asyncio.gather(*(_ask(deps, batch, texts) for batch in batches), return_exceptions=True)
    by_ticket: dict[str, Sentiment] = {}
    dropped = failed = 0
    for batch, answer in zip(batches, answers, strict=True):
        if isinstance(answer, BaseException) or answer is None:
            if isinstance(answer, BaseException) and not isinstance(answer, TurnLimitReached):
                raise answer
            failed += 1
            continue
        allowed = {t.id for t in batch}
        for item in answer:
            if item.ticket_id not in allowed or item.ticket_id in by_ticket:
                continue
            checked, lost = _verified(item, texts[item.ticket_id])
            by_ticket[item.ticket_id] = checked
            dropped += lost
    if dropped:
        log.info("dropped %d unverified quote(s)", dropped)
    return SentimentOutcome(by_ticket, dropped, failed)


async def _ask(deps: TurnDeps, batch: Sequence[Ticket], texts: dict[str, tuple[str, ...]]):
    blocks = "\n\n".join(f"Ticket {t.id}:\n" + "\n".join(f"- {line}" for line in texts[t.id]) for t in batch)
    messages = ({"role": "system", "content": prompts.SENTIMENT_SYSTEM},
                {"role": "user", "content": prompts.fence(blocks)})
    return records.parse_sentiments(await ask_once(deps, messages))


def _verified(item: Sentiment, lines: tuple[str, ...]) -> tuple[Sentiment, int]:
    own = verify.normalise(" ".join(lines))
    lost = 0
    frustration, escalation = item.frustration_quote, item.escalation_quote
    if frustration and not verify.contains(own, frustration):
        frustration, lost = "", lost + 1
    if escalation and not verify.contains(own, escalation):
        escalation, lost = "", lost + 1
    return replace(item, frustration_quote=frustration, escalation_quote=escalation), lost
