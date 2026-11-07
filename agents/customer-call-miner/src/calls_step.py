"""The calls step: a bounded tool loop lists this period's recorded calls; code keeps only the calls a
tool returned, inside the window, with at least one external participant."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import checks
import coverage
import prompts
import smalltalk
from deps import TurnDeps
from reader import Reader, ask_once
from records import Call, load_json_object
from windows import Window

log = logging.getLogger("call_miner.calls")

CALLS_LOOP_CALLS = 4  # plus one JSON repair = 5
_PAUSED = ("approval_pending", "authorization_required")


@dataclass(frozen=True)
class CallsOutcome:
    reply: str | None              # a fixed reply that ends the turn
    calls: tuple[Call, ...] = ()
    dropped: int = 0
    internal_only: int = 0


async def list_calls(deps: TurnDeps, reader: Reader, query: str, window: Window) -> CallsOutcome:
    if not reader.tools:
        return CallsOutcome(prompts.NO_CALLS_TOOL)
    system = prompts.calls_system(window)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query})
    result = await reader.loop(deps, messages, CALLS_LOOP_CALLS)
    status = reader.status()
    if not status.ok:
        return CallsOutcome(coverage.line(status))
    if result.status in _PAUSED:
        return CallsOutcome(prompts.READ_NEEDS_APPROVAL)
    data = load_json_object(result.text) if result.status == "completed" else None
    if data is None or data.get("status") not in ("ok", "off_topic", "no_tool"):
        data = await _repair(deps, system, query, result.text, reader.seen_text)
    if data is None:
        return CallsOutcome(prompts.UNREADABLE_CALLS)
    if data.get("status") == "off_topic":
        return CallsOutcome(smalltalk.OFF_TOPIC_REPLY)
    if data.get("status") == "no_tool":
        return CallsOutcome(prompts.NO_CALLS_TOOL)
    raw = data.get("calls") if isinstance(data.get("calls"), list) else []
    checked = checks.checked_calls(raw, reader.seen_text, window, deps.config)
    if checked.dropped:
        log.info("dropped %d call(s): not in tool output or outside the window", checked.dropped)
    if not checked.calls:
        return CallsOutcome(prompts.no_calls(window))
    return CallsOutcome(None, checked.calls, checked.dropped, checked.internal_only)


async def _repair(deps: TurnDeps, system: str, query: str, answer: str, seen_text: str) -> dict | None:
    if not seen_text:
        return None
    log.info("calls answer was not the agreed JSON; asking once for a rewrite")
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query},
                {"role": "user", "content": f"Tool results from this turn:\n{prompts.fence(seen_text[:40_000])}\n\n"
                                            f"Your previous answer:\n{prompts.fence(answer)}\n\nRewrite it as ONLY "
                                            "the JSON object described in the system prompt, from these tool "
                                            "results only. No prose."})
    return load_json_object(await ask_once(deps, messages))
