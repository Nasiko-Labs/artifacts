"""The chat step: feedback messages in the channels the user named (default: a text search for "feedback"),
post-checked in code. Bot messages and messages under 30 characters are dropped; the text and id must be in
this turn's tool output; capped newest first."""

from __future__ import annotations

from collections.abc import Sequence

import prompts
import records
from checks import Seen, checked_url, is_epoch, parse_day, seen_day
from deps import TurnDeps
from reader import TurnReader
from records import Item, RawMessages
from sourcing import read_source
from steps_tickets import Pulled, cap
from window import Window


async def chat_step(deps: TurnDeps, turn: TurnReader, window: Window, channels: Sequence[str]) -> Pulled:
    start, end = window.start.isoformat(), window.end.isoformat()
    cfg = deps.config
    where = ", ".join(f"#{c}" for c in channels) if channels else 'messages matching "feedback"'
    run = await read_source(deps, turn, "chat", prompts.chat_system(start, end, channels),
                            f"Read the customer feedback in {where} posted from {start} to {end}.",
                            cfg.chat_loop_calls, cfg.chat_tool_calls, records.parser(RawMessages))
    if run.parsed is None:
        return Pulled(run=run)
    items, dropped = check_messages(run.parsed, turn.seen, window, cfg.min_chat_chars)
    kept, capped = cap(items, cfg.max_chat_items)
    return Pulled(kept, run, dropped, capped)


def check_messages(parsed: RawMessages, seen: Seen, window: Window, min_chars: int) -> tuple[list[Item], int]:
    kept: list[Item] = []
    dropped = 0
    for raw in parsed.items:
        ref, text = str(raw.id).strip(), " ".join((raw.text or "").split())
        day = parse_day(ref) if is_epoch(ref) else seen_day(raw.posted_at, seen)
        if raw.is_bot or len(text) < min_chars:  # filtered, not a failed check
            continue
        if not seen.has_exact(ref) or not seen.has(text) or day is None:
            dropped += 1
            continue
        if not window.start <= day <= window.end:  # outside the window: filtered
            continue
        author = (raw.author_label or "").strip()
        kept.append(Item(id="", source="chat", text=text, customer=author if seen.has(author) else "",
                         date=day.isoformat(), url=checked_url(raw.url, seen), ref=ref))
    return kept, dropped
