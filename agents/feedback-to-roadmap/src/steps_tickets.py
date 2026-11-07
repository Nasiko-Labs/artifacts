"""The tickets step: support-desk tickets or conversations created in the window, post-checked in code. Kept
only when the id and the customer's words are in this turn's tool output; the url and customer only when
they are too; dropped when dated outside the window; capped newest first (the cap is reported)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import prompts
import records
from checks import Seen, checked_url, seen_day
from deps import TurnDeps
from reader import TurnReader
from records import Item, RawTickets
from sourcing import SourceRun, read_source
from window import Window


@dataclass(frozen=True)
class Pulled:
    items: tuple[Item, ...] = ()
    run: SourceRun = field(default_factory=SourceRun)
    dropped: int = 0
    capped: int = 0


async def tickets_step(deps: TurnDeps, turn: TurnReader, window: Window) -> Pulled:
    start, end = window.start.isoformat(), window.end.isoformat()
    cfg = deps.config
    run = await read_source(deps, turn, "tickets", prompts.tickets_system(start, end),
                            f"List the customer tickets or conversations created from {start} to {end}.",
                            cfg.desk_loop_calls, cfg.desk_tool_calls, records.parser(RawTickets))
    if run.parsed is None:
        return Pulled(run=run)
    items, dropped = check_tickets(run.parsed, turn.seen, window)
    kept, capped = cap(items, cfg.max_desk_items)
    return Pulled(kept, run, dropped, capped)


def check_tickets(parsed: RawTickets, seen: Seen, window: Window) -> tuple[list[Item], int]:
    kept: list[Item] = []
    dropped = 0
    for raw in parsed.items:
        ref, text = str(raw.id).strip(), " ".join((raw.text_excerpt or "").split())
        day = seen_day(raw.created_at, seen)
        if not seen.has_exact(ref) or not seen.has(text) or day is None:
            dropped += 1
            continue
        if not _in_window(day, window):  # outside the window: filtered, not a failed check
            continue
        customer = (raw.customer or "").strip()
        kept.append(Item(id="", source="desk", text=text, customer=customer if seen.has(customer) else "",
                         date=day.isoformat(), url=checked_url(raw.url, seen), ref=ref))
    return kept, dropped


def _in_window(day, window: Window) -> bool:
    return day is not None and window.start <= day <= window.end


def cap(items: Sequence[Item], limit: int) -> tuple[list[Item], int]:
    """Newest first; the number cut is reported."""
    newest = sorted(items, key=lambda i: i.date, reverse=True)
    return newest[:limit], max(0, len(newest) - limit)


def number(items: Sequence[Item], prefix: str) -> list[Item]:
    return [replace(item, id=f"{prefix}{n}") for n, item in enumerate(items, start=1)]
