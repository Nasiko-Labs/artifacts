"""The scan step: one bounded tool loop over the email read tools, whose JSON answer is checked in code.
A thread survives only when its ids, sender and subject came from a tool this turn and its latest inbound
message is inside the window; bulk senders and the user's own mail are dropped here."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

import coverage
import prompts
import smalltalk
from deps import TurnDeps
from discovery import Catalog
from fence import FencedGateway
from llmcall import ask_once, load_json_object, text_of
from models import Thread
from textcheck import appears_in, parse_day
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("inbox_to_linear.scan")

SCAN_LOOP_CALLS = 8
MAX_THREADS = 40
_PAUSED = ("approval_pending", "authorization_required")
_BULK_SENDER = re.compile(r"no-?reply|do-?not-?reply|newsletter|notifications?@|mailer-daemon|bounce|digest@"
                          r"|marketing@|news@|updates@|unsubscribe", re.IGNORECASE)


@dataclass(frozen=True)
class ScanOutcome:
    """Either a fixed reply that ends the turn, or the checked threads."""

    reply: str | None
    threads: tuple[Thread, ...] = ()
    notes: tuple[str, ...] = ()
    dropped: int = 0
    seen_text: str = ""


async def scan(deps: TurnDeps, catalog: Catalog, query: str, window_days: int) -> ScanOutcome:
    tools = catalog.email_read_tools()
    if not tools:
        return ScanOutcome(prompts.NO_EMAIL_TOOL)
    fenced = FencedGateway(deps.gateway)
    health = ToolHealth(fenced)
    system = prompts.scan_system(deps.now, window_days, MAX_THREADS)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query})
    result = await run_tool_loop(deps.llm, deps.model, messages, tools, health, deps.traceparent,
                                 max_iterations=SCAN_LOOP_CALLS, is_offered=health.is_offered)
    reason = coverage.all_dropped(tools, health.dropped)
    if reason is not None:
        return ScanOutcome(prompts.email_unavailable(reason))
    if result.status in _PAUSED:
        return ScanOutcome(prompts.READ_NEEDS_APPROVAL)
    hit_limit = result.status == "max_iterations"
    notes = (prompts.STEP_LIMIT_NOTE,) if hit_limit else ()
    parsed = None if hit_limit else load_json_object(result.text)
    if not _is_answer(parsed) and (fenced.seen_text or not hit_limit):
        parsed = await _repair(deps, system, query, result, fenced.seen_text)
    return _checked(deps, parsed, window_days, fenced.seen_text, notes)


def _is_answer(data: Mapping[str, Any] | None) -> bool:
    return isinstance(data, Mapping) and data.get("status") in ("ok", "off_topic", "no_email_tool")


async def _repair(deps: TurnDeps, system: str, query: str, result: LoopResult, seen: str) -> dict | None:
    """One tool-less call that restates the answer as JSON from this turn's (fenced) tool output."""
    log.info("scan answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen) if seen else "(no tool results this turn)"
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query},
                {"role": "user", "content": f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n"
                                            f"{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}"})
    return load_json_object(await ask_once(deps, messages))


def _checked(deps: TurnDeps, parsed: Mapping[str, Any] | None, window_days: int, seen: str,
             notes: tuple[str, ...]) -> ScanOutcome:
    if not _is_answer(parsed):
        return ScanOutcome("\n\n".join([prompts.UNREADABLE_ANSWER, *notes]))
    if parsed["status"] == "off_topic":
        return ScanOutcome(smalltalk.OFF_TOPIC_REPLY)
    if parsed["status"] == "no_email_tool":
        return ScanOutcome(prompts.NO_EMAIL_TOOL)
    raw = parsed.get("threads") if isinstance(parsed.get("threads"), list) else []
    me = text_of(parsed.get("me"))
    me = me if appears_in(me, seen) else ""
    candidates = [_thread(t) for t in raw if isinstance(t, Mapping)]
    threads = check_threads(candidates, seen=seen, me=me, since=deps.today - timedelta(days=window_days),
                            today=deps.today)
    dropped = sum(1 for t in candidates if not _is_backed(t, seen))  # only unbacked ones lower confidence
    log.info("scan kept %d of %d thread(s); %d not backed by tool output", len(threads), len(raw), dropped)
    return ScanOutcome(None, threads, notes, dropped, seen)


def _thread(raw: Mapping[str, Any]) -> Thread:
    return Thread(id=text_of(raw.get("id")), thread_id=text_of(raw.get("thread_id")),
                  message_id=text_of(raw.get("message_id")), sender=text_of(raw.get("from")),
                  sender_name=text_of(raw.get("from_name")), subject=text_of(raw.get("subject")),
                  date=text_of(raw.get("date")), link=text_of(raw.get("link")), text=text_of(raw.get("text")),
                  user_reply=text_of(raw.get("user_reply")), bulk=raw.get("bulk") is True)


def is_bulk(thread: Thread) -> bool:
    return thread.bulk or bool(_BULK_SENDER.search(thread.sender))


def check_threads(threads: Sequence[Thread], *, seen: str, me: str, since, today) -> tuple[Thread, ...]:
    """Backed, inbound, in-window, not bulk; renumbered t1..tn; at most MAX_THREADS."""
    kept: tuple[Thread, ...] = ()
    keys: frozenset[str] = frozenset()
    for thread in threads:
        key = thread.thread_id or thread.message_id
        if key in keys or not _is_backed(thread, seen) or is_bulk(thread):
            continue
        if me and thread.sender.casefold() == me.casefold():
            continue
        sent = parse_day(thread.date)
        if sent is None or not since <= sent <= today:
            continue
        kept = (*kept, _cleaned(thread, seen))
        keys = keys | {key}
    return tuple(replace(t, id=f"t{n}") for n, t in enumerate(kept[:MAX_THREADS], start=1))


def _is_backed(thread: Thread, seen: str) -> bool:
    key = thread.thread_id or thread.message_id
    return (appears_in(key, seen) and appears_in(thread.sender, seen)
            and (not thread.subject or appears_in(thread.subject, seen)))


def _cleaned(thread: Thread, seen: str) -> Thread:
    """Links and the user's reply are kept only when a tool returned them."""
    return replace(thread, link=thread.link if appears_in(thread.link, seen) else "",
                   user_reply=thread.user_reply if appears_in(thread.user_reply, seen) else "")
