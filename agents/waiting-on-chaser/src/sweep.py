"""The sweep step: a bounded tool loop that finds the user's own outbound asks, post-checked in code.
Also the shared read side (`Reader`) and the tool-less single call (`ask_once`)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import asks as a
import checks
import coverage as coverage_
import prompts
import smalltalk
from deps import TurnDeps
from fence import FencedGateway
from nasiko_mcp import McpTool
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("waiting_on.sweep")

SWEEP_LOOP_CALLS = 6  # plus one JSON repair = 7
_PAUSED = ("approval_pending", "authorization_required")
_NOTHING_CHECKED = checks.CheckedSweep(())


@dataclass(frozen=True)
class SweepOutcome:
    """Either a fixed reply that ends the turn, or the checked asks for the reply step."""

    reply: str | None
    checked: checks.CheckedSweep = _NOTHING_CHECKED
    me: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()  # sources that could not be searched, the step limit, unknown identity


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()


@dataclass(frozen=True)
class Reader:
    """One turn's read side: the read tools offered to the model, the fence adapter that records tool
    output, and the failure tracker in front of it (shared by sweep and the reply check)."""

    tools: tuple[McpTool, ...]
    fenced: FencedGateway
    health: ToolHealth
    source_of: Callable[[McpTool], str | None]
    helpers: frozenset[str] = frozenset()

    async def loop(self, deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_iterations: int) -> LoopResult:
        return await run_tool_loop(deps.llm, deps.model, messages, self.tools, self.health, deps.traceparent,
                                   max_iterations=max_iterations, is_offered=self.health.is_offered)

    def coverage(self) -> coverage_.Coverage:
        return coverage_.assess(self.tools, self.source_of, self.health.dropped, self.helpers)


async def sweep(deps: TurnDeps, reader: Reader, query: str, request: a.Request) -> SweepOutcome:
    if not reader.tools:
        return SweepOutcome(prompts.NO_READ_TOOL)
    system = prompts.sweep_system(deps.now, request.window_days)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query})
    result = await reader.loop(deps, messages, SWEEP_LOOP_CALLS)
    covered = reader.coverage()
    if covered.all_failed:
        return SweepOutcome(coverage_.all_failed_reply(covered))
    notes = coverage_.notes(covered, request.sources)
    if result.status in _PAUSED:
        return SweepOutcome(prompts.READ_NEEDS_APPROVAL)
    hit_limit = result.status == "max_iterations"
    if hit_limit:
        log.info("sweep hit its step limit; salvaging what the tools returned")
        notes = (*notes, prompts.STEP_LIMIT_NOTE)
    parsed = None if hit_limit else a.parse_sweep(result.text)
    if parsed is None and (reader.fenced.seen_text or not hit_limit):
        parsed = await _repair(deps, system, query, result, reader.fenced.seen_text)
    return _checked(deps, parsed, request, reader.fenced.seen_text, notes, hit_limit)


def _checked(deps: TurnDeps, parsed: a.SweepAnswer | None, request: a.Request, seen_text: str,
             notes: tuple[str, ...], hit_limit: bool) -> SweepOutcome:
    if parsed is None:
        return SweepOutcome(_with_notes(prompts.STEP_LIMIT_NOTHING if hit_limit else prompts.UNREADABLE_ANSWER,
                                        notes, hit_limit))
    if parsed.status == "off_topic":
        return SweepOutcome(smalltalk.OFF_TOPIC_REPLY)
    if parsed.status == "no_read_tool":
        return SweepOutcome(_with_notes(prompts.NO_SOURCE_REFUSAL, notes))
    me = checks.verified_me(parsed.me, seen_text)
    if not me:
        notes = (*notes, prompts.NO_IDENTITY_NOTE)
    checked = checks.post_check(parsed.asks, now=deps.now, request=request, seen_text=seen_text, me=me)
    if checked.dropped:
        log.info("post-check dropped %d candidate(s) (unbacked, outside the window, or to yourself)", checked.dropped)
    if not checked.asks:
        nothing = prompts.STEP_LIMIT_NOTHING if hit_limit else prompts.nothing_waiting(request.window_days)
        fresh = (f"{checked.too_fresh} ask(s) younger than {checks.MIN_AGE_DAYS} days not listed (say \"include "
                 "fresh\" to see them).") if checked.too_fresh else ""
        return SweepOutcome(_with_notes(" ".join(p for p in (nothing, fresh) if p), notes, hit_limit))
    return SweepOutcome(None, checked, me, notes)


def _with_notes(reply: str, notes: Sequence[str], hit_limit: bool = False) -> str:
    """The reply plus the coverage notes (the step-limit note is dropped where the reply already says it)."""
    shown = [n for n in notes if not (hit_limit and n == prompts.STEP_LIMIT_NOTE)]
    return "\n\n".join([reply, *shown])


async def _repair(deps: TurnDeps, system: str, query: str, result: LoopResult, seen_text: str) -> a.SweepAnswer | None:
    """One tool-less call that restates the answer as JSON from this turn's (fenced) tool output."""
    log.info("sweep answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen_text) if seen_text else "(no tool results this turn)"
    messages = (
        {"role": "system", "content": system},
        {"role": "user", "content": query},
        {"role": "user", "content": f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n"
                                    f"{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}"},
    )
    return a.parse_sweep(await ask_once(deps, messages))
