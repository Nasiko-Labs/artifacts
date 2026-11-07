"""The commitments step: one bounded tool loop over the tracker (or the linked plan), its JSON answer
post-checked in code (commitments.check). Ends the turn with a fixed reply when it cannot go on."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import commitments as c
import coverage
import prompts
import smalltalk
from deps import TurnDeps
from discovery import RECOVER_META, discover
from reader import Reader
from request import AuditRequest

log = logging.getLogger("commitment_audit.source")

LOOP_CALLS = 5  # plus one repair = 6


@dataclass(frozen=True)
class SourceOutcome:
    """Either a fixed reply that ends the turn, or the checked commitments."""

    reply: str | None
    checked: c.Checked | None = None
    notes: tuple[str, ...] = ()


async def find(deps: TurnDeps, reader: Reader, request: AuditRequest, query: str) -> SourceOutcome:
    plan = request.source == "plan"
    catalog, failure = await discover(deps, "source_plan" if plan else "source_tracker")
    if failure is not None:
        return SourceOutcome(failure)
    tools = catalog.read_tools(("document", "tracker") if plan else ("tracker",))
    if not tools:
        return SourceOutcome(prompts.NO_PLAN_TOOL if plan else prompts.NO_TRACKER_TOOL)
    log.info("source tools offered: %s", [t.name for t in tools])
    step = await reader.run(deps, prompts.commitments_system(request, deps.today), query, tools, LOOP_CALLS,
                            c.parse_source_answer)
    covered = coverage.assess("document" if plan else "tracker", tools, reader.health.dropped,
                              frozenset({RECOVER_META}))
    if not covered.ok:
        return SourceOutcome(_unavailable(covered, plan))
    if step.paused:
        return SourceOutcome(prompts.READ_NEEDS_APPROVAL)
    notes = (prompts.STEP_LIMIT_NOTE,) if step.hit_limit else ()
    return _outcome(step.parsed, reader.seen_text, request, notes)


def _unavailable(covered: coverage.StepCoverage, plan: bool) -> str:
    if covered.state == "missing":
        return prompts.NO_PLAN_TOOL if plan else prompts.NO_TRACKER_TOOL
    return prompts.tracker_unavailable(covered.reason) if not plan else (
        f"I couldn't read that plan for your connection ({covered.reason}). I won't guess from memory.")


def _outcome(answer: c.SourceAnswer | None, seen_text: str, request: AuditRequest,
             notes: tuple[str, ...]) -> SourceOutcome:
    if answer is None:
        return SourceOutcome(prompts.UNREADABLE_ANSWER)
    if answer.status == "off_topic":
        return SourceOutcome(smalltalk.OFF_TOPIC_REPLY)
    if answer.status == "no_source_tool":
        return SourceOutcome(prompts.NO_PLAN_TOOL if request.source == "plan" else prompts.NO_TRACKER_TOOL)
    checked = c.check(answer.items, seen_text)
    if checked.dropped:
        log.info("post-check dropped %d commitment(s) not backed by tool output", checked.dropped)
    if not checked.audited:
        return SourceOutcome(_nothing(request))
    return SourceOutcome(None, checked, notes)


def _nothing(request: AuditRequest) -> str:
    if request.source == "plan":
        return (f"I couldn't read any commitments I can verify from {request.plan_link}. Check that the link is "
                "a plan document or sheet I can open, or tell me the tracker label that marks them.")
    return prompts.need_source(request)
