"""The gather half of a pull: the support desk and chat steps (concurrently), plus any pasted feedback, into one
deduplicated, numbered item pool and the facts the caveats and the confidence rest on."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field

from caveats import RunFacts
from deps import TurnDeps
from parse_paste import Request
from reader import TurnReader
from records import Item
from steps_chat import chat_step
from steps_tickets import Pulled, number, tickets_step
from themes import dedupe
from window import Window


@dataclass(frozen=True)
class Gathered:
    items: tuple[Item, ...] = ()
    facts: RunFacts = field(default_factory=RunFacts)
    fatal: str = ""


async def gather(deps: TurnDeps, turn: TurnReader, request: Request, window: Window) -> Gathered:
    desk, chat = await asyncio.gather(tickets_step(deps, turn, window), chat_step(deps, turn, window, request.channels))
    fatal = desk.run.fatal or chat.run.fatal
    if fatal and not request.pasted:
        return Gathered(fatal=fatal)
    pool = (*number(desk.items, "d"), *number(chat.items, "c"), *request.pasted)
    items, duplicates = dedupe(pool)
    counts = Counter(i.source for i in items)
    read = [(name, counts.get(name, 0)) for name, pulled in (("desk", desk), ("chat", chat)) if pulled.run.read]
    if request.pasted:
        read.append(("pasted", counts.get("pasted", 0)))
    facts = RunFacts(
        read_counts=tuple(read), notes=(*desk.run.notes, *chat.run.notes), duplicates=duplicates,
        unparsed=request.unparsed, capped=desk.capped + chat.capped, dropped=desk.dropped + chat.dropped,
        extracted=_returned(desk) + _returned(chat), default_channel=chat.run.read and not request.channels,
        clamped=window.clamped and window.asked, truncated=request.truncated,
        salvage=desk.run.limited or chat.run.limited)
    return Gathered(items, facts)


def _returned(pulled: Pulled) -> int:
    return len(pulled.items) + pulled.capped + pulled.dropped
