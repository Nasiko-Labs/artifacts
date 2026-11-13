"""The exec-brief turn, as plain async steps over LangChain runnables (the model calls are LCEL chains in
llm.py; there is no graph framework).

    small talk ─────────────────────────────────────────────→ canned reply
    intake (code) ─┬─ fixed reply / code-only edit ─────────→ reply
                   └─ compose (model + checks + render) ────→ reply

Stateless: every step works on this turn's message and the platform's history slice only (recall.py), so
any replica can answer any message. Nothing pauses: every turn ends with a reply.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import compose
import inputs
import intake
import prompts
import render
import smalltalk
from brief import Brief, Line
from llm import CallBudgetError, Llm, MissingTraceparentError

log = logging.getLogger("exec_brief.agent")


@dataclass(frozen=True)
class TurnDeps:
    llm: Llm
    org: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnResult:
    reply: str


class BriefAgent:
    """Holds nothing between turns."""

    async def turn(self, incoming: inputs.Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """`context_id` is only for logs and spans; no state is keyed by it."""
        if not incoming.files:
            canned = smalltalk.reply_to(incoming.text)
            if canned is not None:  # a greeting or small talk: no model call
                return TurnResult(canned)
        step = intake.decide(incoming)
        if step.route != "compose" or step.work is None:
            return TurnResult(step.reply)
        return TurnResult(await _compose(step.work, incoming, deps))


async def _compose(work: intake.Work, incoming: inputs.Incoming, deps: TurnDeps) -> str:
    if work.followup and not await _about_the_brief(deps.llm, work):
        return smalltalk.OFF_TOPIC_REPLY
    request = compose.Request(sections=work.sections, instruction=work.instruction, org=deps.org,
                              prior=prior_view(work.prior))
    try:
        outcome = await compose.write(deps.llm, request)
    except MissingTraceparentError:
        return prompts.NO_TRACE_CONTEXT
    except CallBudgetError:
        log.warning("call budget spent before a brief was written")
        outcome = compose.Outcome("unreadable")
    if outcome.status == "off_topic":
        return smalltalk.OFF_TOPIC_REPLY
    if work.scope_only:
        return prompts.TOO_SHORT
    if outcome.brief is None:
        return prompts.UNREADABLE
    notes = [*work.notes, *filter(None, [inputs.skipped_note(incoming.skipped)])]
    return render.render(outcome.brief, work.sections, extra_notes=notes)


async def _about_the_brief(llm: Llm, work: intake.Work) -> bool:
    """One classify call before a free-text revision, with the previous reply as context: only a clearly
    unrelated question ("what's 2+2?") is declined, even though a brief is in the history. Fails open (keeps the revision) when the check can't run, so the
    compose step reports the real problem (no trace context, the budget, the model)."""
    try:
        data = await llm.json(prompts.followup_scope_messages(work.followup, work.previous_reply))
    except (MissingTraceparentError, CallBudgetError):
        return True
    return not (isinstance(data, dict) and str(data.get("status") or "").lower() == "off_topic")


def prior_view(prior: Brief | None) -> dict | None:
    """The earlier brief as the model sees it on a revision: lines and their section ids, no notes."""
    if prior is None:
        return None

    def line(row: Line) -> dict:
        return {"text": row.text, "sections": sorted({c.section for c in row.cites})}

    return {"headline": line(prior.headline) if prior.headline else None,
            "risks": [line(r) | {"severity": r.severity} for r in prior.risks],
            "decisions": [line(d) for d in prior.decisions]}
