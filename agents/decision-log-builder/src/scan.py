"""The scan step: one bounded tool loop over the chat read tools, whose JSON answer is checked in code.
A message survives only when its text and timestamp came back from a tool this turn and its date (from the
timestamp, in code) is inside the window. Names, channel names and links are kept only when a tool
returned them; otherwise the visible fallback is the id, or "unknown"."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import prompts
import smalltalk
from deps import TurnDeps
from discovery import Catalog
from fence import FencedGateway
from llmcall import ask_once, load_json_object, text_of
from models import Message
from textcheck import appears_in, parse_day
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("decision_log.scan")

SCAN_LOOP_CALLS = 4  # plus one JSON repair
MAX_MESSAGES = 120
_PAUSED = ("approval_pending", "authorization_required")
_ANSWERS = ("ok", "off_topic", "no_chat_tool")


@dataclass(frozen=True)
class ScanOutcome:
    """Either a fixed reply that ends the turn, or the checked messages plus what the tools returned."""

    reply: str | None
    messages: tuple[Message, ...] = ()
    seen_text: str = ""
    notes: tuple[str, ...] = ()
    dropped: int = 0


async def scan(deps: TurnDeps, catalog: Catalog, window_days: int, channels: Sequence[str]) -> ScanOutcome:
    tools = catalog.chat_read_tools()
    if not tools:
        return ScanOutcome(prompts.NO_CHAT_TOOL)
    fenced = FencedGateway(deps.gateway)
    health = ToolHealth(fenced)
    system = prompts.scan_system(deps.now, window_days, channels, MAX_MESSAGES)
    request = prompts.scan_request(deps.now, window_days, channels)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": request})
    result = await run_tool_loop(deps.llm, deps.model, messages, tools, health, deps.traceparent,
                                 max_iterations=SCAN_LOOP_CALLS, is_offered=health.is_offered)
    reason = all_dropped(tools, health.dropped)
    if reason is not None:
        return ScanOutcome(chat_unavailable(reason))
    if result.status in _PAUSED:
        return ScanOutcome(prompts.READ_NEEDS_APPROVAL)
    hit_limit = result.status == "max_iterations"
    notes = (prompts.STEP_LIMIT_NOTE,) if hit_limit else ()
    notes = (*notes, *_partial_notes(tools, health.dropped))
    parsed = None if hit_limit else load_json_object(result.text)
    if not _is_answer(parsed) and (fenced.seen_text or not hit_limit):
        parsed = await _repair(deps, system, request, result, fenced.seen_text)
    return _checked(deps, parsed, window_days, fenced.seen_text, notes)


def all_dropped(offered: Sequence[Any], dropped: Mapping[str, str]) -> str | None:
    """The reason when every offered tool was dropped as unavailable, else None."""
    if not offered or any(t.name not in dropped for t in offered):
        return None
    return ", ".join(dict.fromkeys(dropped[t.name] for t in offered))


def chat_unavailable(reason: str) -> str:
    return (f"Chat: not available for your connection ({reason}), so I can't read your channels and I won't "
            "guess from memory. Paste the messages here, one per line, and I'll pull out the decisions.")


def _partial_notes(tools: Sequence[Any], dropped: Mapping[str, str]) -> tuple[str, ...]:
    gone = [t.name for t in tools if t.name in dropped]
    if not gone:
        return ()
    reasons = ", ".join(dict.fromkeys(dropped[n] for n in gone))
    note = (f"Chat: {len(gone)} of my chat tools were not available for your connection ({reasons}), so some "
            "channels or threads may be missing.")
    return (note,)


def _is_answer(data: Mapping[str, Any] | None) -> bool:
    return isinstance(data, Mapping) and data.get("status") in _ANSWERS


async def _repair(deps: TurnDeps, system: str, request: str, result: LoopResult, seen: str) -> dict | None:
    """One tool-less call that restates the answer as JSON from this turn's (fenced) tool output."""
    log.info("scan answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen) if seen else "(no tool results this turn)"
    messages = ({"role": "system", "content": system}, {"role": "user", "content": request},
                {"role": "user", "content": f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n"
                                            f"{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}"})
    return load_json_object(await ask_once(deps, messages))


def _checked(deps: TurnDeps, parsed: Mapping[str, Any] | None, window_days: int, seen: str,
             notes: tuple[str, ...]) -> ScanOutcome:
    if not _is_answer(parsed):
        return ScanOutcome("\n\n".join([prompts.UNREADABLE_ANSWER, *notes]))
    if parsed["status"] == "off_topic":
        return ScanOutcome(smalltalk.OFF_TOPIC_REPLY)
    if parsed["status"] == "no_chat_tool":
        return ScanOutcome(prompts.NO_CHAT_TOOL)
    raw = [m for m in parsed.get("messages") or () if isinstance(m, Mapping)]
    since = prompts.since_of(deps.now, window_days)
    kept = check_messages(raw, seen=seen, since=since, today=deps.today)
    unbacked = sum(1 for m in raw if not _is_backed(m, seen))
    log.info("scan kept %d of %d message(s); %d not backed by tool output", len(kept), len(raw), unbacked)
    return ScanOutcome(None, kept, seen, notes, unbacked)


def check_messages(raw: Sequence[Mapping[str, Any]], *, seen: str, since: date, today: date) -> tuple[Message, ...]:
    """Backed and in-window messages, de-duplicated, renumbered m1..mn, at most MAX_MESSAGES."""
    kept: tuple[Message, ...] = ()
    keys: frozenset[tuple[str, str]] = frozenset()
    for item in raw:
        if not _is_backed(item, seen):
            continue
        day = parse_day(text_of(item.get("ts")))
        if day is None or not since <= day <= today:
            continue
        key = (text_of(item.get("channel")), text_of(item.get("ts")))
        if key in keys:
            continue
        kept = (*kept, _message(item, day, seen, len(kept) + 1))
        keys = keys | {key}
    return kept[:MAX_MESSAGES]


def _is_backed(item: Mapping[str, Any], seen: str) -> bool:
    return appears_in(text_of(item.get("text")), seen) and appears_in(text_of(item.get("ts")), seen)


def _backed(value: Any, seen: str) -> str:
    text = text_of(value)
    return text if appears_in(text, seen) else ""


def _message(item: Mapping[str, Any], day: date, seen: str, n: int) -> Message:
    return Message(id=f"m{n}", channel=_backed(item.get("channel"), seen),
                   channel_name=_backed(item.get("channel_name"), seen).lstrip("#"),
                   author=_backed(item.get("author"), seen), author_name=_backed(item.get("author_name"), seen),
                   ts=text_of(item.get("ts")), day=day.isoformat(), text=text_of(item.get("text")),
                   link=_backed(item.get("link"), seen), thread=_backed(item.get("thread"), seen))
