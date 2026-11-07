"""The scan step: one bounded tool loop over the chat read tools, whose JSON answer is checked in code
(candidates.py). The model only fetches and copies; code decides which threads are worth judging."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import candidates
import coverage
import parse
import prompts
import smalltalk
from deps import TurnDeps
from discovery import Catalog
from fence import FencedGateway
from llmcall import ask_once, load_json_object, text_of
from models import ThreadCandidate
from textcheck import appears_in
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("thread_to_issue.scan")

SCAN_LOOP_CALLS = 10
_PAUSED = ("approval_pending", "authorization_required")


@dataclass(frozen=True)
class ScanOutcome:
    """Either a fixed reply that ends the turn, or the checked threads."""

    reply: str | None
    threads: tuple[ThreadCandidate, ...] = ()
    notes: tuple[str, ...] = ()
    dropped: int = 0
    seen_text: str = ""
    channels: tuple[str, ...] = ()
    read: int = 0


async def scan(deps: TurnDeps, catalog: Catalog, query: str, window_days: int,
               channels: Sequence[str] = ()) -> ScanOutcome:
    tools = catalog.chat_read_tools()
    if not tools:
        return ScanOutcome(prompts.NO_CHAT_TOOL)
    fenced = FencedGateway(deps.gateway)
    health = ToolHealth(fenced)
    system = prompts.scan_system(deps.now, window_days, channels, candidates.MAX_THREADS, parse.MAX_CHANNELS)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": prompts.strip_markers(query)})
    result = await run_tool_loop(deps.llm, deps.model, messages, tools, health, deps.traceparent,
                                 max_iterations=SCAN_LOOP_CALLS, is_offered=health.is_offered)
    reason = coverage.all_dropped(tools, health.dropped)
    if reason is not None:
        return ScanOutcome(prompts.chat_unavailable(reason))
    if result.status in _PAUSED:
        return ScanOutcome(prompts.READ_NEEDS_APPROVAL)
    hit_limit = result.status == "max_iterations"
    notes = (prompts.STEP_LIMIT_NOTE,) if hit_limit else ()
    parsed = None if hit_limit else load_json_object(result.text)
    if not _is_answer(parsed) and (fenced.seen_text or not hit_limit):
        parsed = await _repair(deps, system, query, result, fenced.seen_text)
    return checked(deps, parsed, window_days, fenced.seen_text, notes)


def _is_answer(data: Mapping[str, Any] | None) -> bool:
    return isinstance(data, Mapping) and data.get("status") in ("ok", "off_topic", "no_chat_tool")


async def _repair(deps: TurnDeps, system: str, query: str, result: LoopResult, seen: str) -> dict | None:
    """One tool-less call that restates the answer as JSON from this turn's (fenced) tool output."""
    log.info("scan answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen) if seen else "(no tool results this turn)"
    messages = ({"role": "system", "content": system}, {"role": "user", "content": prompts.strip_markers(query)},
                {"role": "user", "content": f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n"
                                            f"{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}"})
    return load_json_object(await ask_once(deps, messages))


def checked(deps: TurnDeps, parsed: Mapping[str, Any] | None, window_days: int, seen: str,
            notes: tuple[str, ...]) -> ScanOutcome:
    if not _is_answer(parsed):
        return ScanOutcome("\n\n".join([prompts.UNREADABLE_ANSWER, *notes]))
    if parsed["status"] == "off_topic":
        return ScanOutcome(smalltalk.OFF_TOPIC_REPLY)
    if parsed["status"] == "no_chat_tool":
        return ScanOutcome(prompts.NO_CHAT_TOOL)
    raw = parsed.get("threads") if isinstance(parsed.get("threads"), list) else []
    result = candidates.check(candidates.from_answer(raw), seen=seen,
                              since=deps.today - timedelta(days=window_days), today=deps.today)
    named = parsed.get("channels_read") if isinstance(parsed.get("channels_read"), list) else []
    read = tuple(c for c in (text_of(x).lstrip("#") for x in named) if c and appears_in(c, seen))
    channels = tuple(dict.fromkeys((*read, *(t.channel for t in result.threads if t.channel))))
    log.info("scan kept %d of %d thread(s); %d not backed by tool output; %d already linked",
             len(result.threads), result.read, result.unbacked, result.already_linked)
    return ScanOutcome(None, result.threads, notes, result.unbacked, seen, channels, result.read)
