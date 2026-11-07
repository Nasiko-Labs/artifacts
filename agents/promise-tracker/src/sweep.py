"""The sweep and verify steps: bounded tool loops whose answers are post-checked in code."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import checks
import coverage as coverage_
import promises as p
import prompts
import smalltalk
from deps import TurnDeps
from fence import FencedGateway
from nasiko_mcp import McpTool
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("promise_tracker.sweep")

# Sweep: at most 7 calls in the loop plus one JSON repair = 8. Verify: at most 6.
SWEEP_LOOP_CALLS = 7
VERIFY_LOOP_CALLS = 6
_PAUSED = ("approval_pending", "authorization_required")


@dataclass(frozen=True)
class SweepOutcome:
    """Either a fixed reply that ends the turn, or the checked candidates for verify."""

    reply: str | None
    candidates: tuple[p.Promise, ...] = ()
    dropped: int = 0
    notes: tuple[str, ...] = ()  # one line per source that could not be searched, or the step limit


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
    output, and the failure tracker in front of it (shared by sweep and verify)."""

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


async def sweep(deps: TurnDeps, reader: Reader, query: str, window_days: int) -> SweepOutcome:
    if not reader.tools:
        return SweepOutcome(prompts.NO_READ_TOOL)
    system = prompts.sweep_system(deps.today, window_days)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": query})
    result = await reader.loop(deps, messages, SWEEP_LOOP_CALLS)
    covered = reader.coverage()
    if covered.all_failed:
        return SweepOutcome(coverage_.all_failed_reply(covered))
    notes = coverage_.notes(covered)
    if result.status in _PAUSED:
        return SweepOutcome(prompts.READ_NEEDS_APPROVAL)
    hit_limit = result.status == "max_iterations"
    if hit_limit:
        log.info("sweep hit its step limit; salvaging what the tools returned")
        notes = (*notes, prompts.STEP_LIMIT_NOTE)
    parsed = None if hit_limit else p.parse_sweep(result.text)
    if parsed is None and (reader.fenced.seen_text or not hit_limit):
        parsed = await _repair(deps, system, query, result, reader.fenced.seen_text)
    return _checked(deps, parsed, window_days, reader.fenced.seen_text, notes, hit_limit)


def _checked(deps: TurnDeps, parsed: p.SweepAnswer | None, window_days: int, seen_text: str,
             notes: tuple[str, ...], hit_limit: bool) -> SweepOutcome:
    if parsed is None:
        return SweepOutcome(_with_notes(prompts.STEP_LIMIT_NOTHING if hit_limit else prompts.UNREADABLE_ANSWER,
                                        notes, hit_limit))
    if parsed.status == "off_topic":
        return SweepOutcome(smalltalk.OFF_TOPIC_REPLY)
    if parsed.status == "no_mail_tool":
        return SweepOutcome(_with_notes(prompts.NO_MAIL_REFUSAL, notes))
    kept = _unique_ids(checks.post_check(parsed.promises, today=deps.today, window_days=window_days,
                                         seen_text=seen_text))
    dropped = len(parsed.promises) - len(kept)
    if dropped:
        log.info("post-check dropped %d candidate(s) (unbacked, or sent outside the window)", dropped)
    if not kept:
        nothing = prompts.STEP_LIMIT_NOTHING if hit_limit else prompts.nothing_found(window_days)
        return SweepOutcome(_with_notes(nothing, notes, hit_limit))
    return SweepOutcome(None, kept, dropped, notes)


def _with_notes(reply: str, notes: Sequence[str], hit_limit: bool = False) -> str:
    """The reply plus the coverage notes (the step-limit note is dropped where the reply already says it)."""
    shown = [n for n in notes if not (hit_limit and n == prompts.STEP_LIMIT_NOTE)]
    return "\n\n".join([reply, *shown])


async def _repair(deps: TurnDeps, system: str, query: str, result: LoopResult, seen_text: str) -> p.SweepAnswer | None:
    """One tool-less call that restates the answer as JSON from this turn's (fenced) tool output."""
    log.info("sweep answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen_text) if seen_text else "(no tool results this turn)"
    messages = (
        {"role": "system", "content": system},
        {"role": "user", "content": query},
        {"role": "user", "content": f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n"
                                    f"{prompts.fence(result.text)}\n\n{prompts.REPAIR_INSTRUCTION}"},
    )
    return p.parse_sweep(await ask_once(deps, messages))


async def verify(deps: TurnDeps, reader: Reader, candidates: Sequence[p.Promise]) -> tuple[p.Promise, ...]:
    """Candidates judged open/done/unclear; `done` only with evidence a tool returned this turn."""
    listing = json.dumps([_candidate_view(c) for c in candidates], ensure_ascii=False, indent=1)
    messages = (
        {"role": "system", "content": prompts.verify_system(deps.today)},
        {"role": "user", "content": prompts.fence_candidates(listing)},
    )
    result = await reader.loop(deps, messages, VERIFY_LOOP_CALLS)
    verdicts = p.parse_verify(result.text) if result.status == "completed" else {}
    if result.status != "completed":
        log.info("verify ended %s; every candidate stays open", result.status)
    return checks.apply_verdicts(candidates, verdicts, reader.fenced.seen_text)


def _unique_ids(candidates: tuple[p.Promise, ...]) -> tuple[p.Promise, ...]:
    """Verify answers by id, so every candidate needs its own; a blank or repeated one is replaced."""
    ids = [c.id for c in candidates]
    return tuple(c if c.id and ids.count(c.id) == 1 else p.with_id(c, f"auto-{n}") for n, c in enumerate(candidates, 1))


def _candidate_view(c: p.Promise) -> dict:
    return {"id": c.id, "source": c.source, "to": c.to, "to_name": c.to_name, "quote": c.quote,
            "sent_at": c.sent_at, "thread_ref": c.thread_ref, "link": c.link}
