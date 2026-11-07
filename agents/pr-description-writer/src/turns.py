"""The graph's node logic: intake (code only), draft, restyle and classify. Each node returns a state update;
`reply`, `data` and `pause` are this turn's output, the rest is checkpointed for follow-ups."""

from __future__ import annotations

import logging

import draft as drafting
import facts as fx
import followup
import hitl
import prompts
import render
import smalltalk
import steps
import template
from finish import Finished, Job, finish
from inputs import Incoming, Style, split_input, style_of
from llm import LlmError, TurnDeps

log = logging.getLogger("pr-description-writer.turns")


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming):
        self.deps = deps
        self.incoming = incoming
        self.split = split_input(incoming.text)


def _reply(text: str) -> dict:
    return {"reply": text, "route": ""}


def _needs_trace(scope: Scope, route: str) -> dict:
    return {"route": route} if scope.deps.traceparent else _reply(prompts.NO_TRACEPARENT_REPLY)


def intake(state: dict, scope: Scope) -> dict:
    text = scope.incoming.text
    if scope.split.parsed.found:
        return _needs_trace(scope, "draft")
    if state.get("envelope"):
        if followup.is_json_only(text):
            return {**_reply(render.reply(state["envelope"], [], as_json=True)), "data": state["envelope"]}
        facts = fx.Facts.from_dict(state.get("facts"))
        if followup.asks_split(text) and facts is not None:
            return _reply(followup.split_advice(facts))
        return _needs_trace(scope, "restyle")
    if followup.mentions_pr(text):
        pending = hitl.Pending(context=scope.split.context, wants_json=scope.incoming.wants_json)
        return {"pause": pending.to_metadata(), "route": ""}
    return _needs_trace(scope, "classify")


def _failed(error: LlmError, scope: Scope) -> dict:
    log.warning("model step failed: %s", error)
    return _reply(prompts.BUDGET_SPENT if not scope.deps.has_time() or scope.deps.budget.calls
                  >= scope.deps.budget.max_calls else prompts.COULD_NOT_DRAFT)


def _stored(result: Finished, job: Job, template_text: str) -> dict:
    return {"facts": job.facts.to_dict(), "draft": result.draft, "context": job.context, "template": template_text,
            "style": job.style.to_dict(), "diff": job.diff, "envelope": result.envelope,
            "run": {"grouped": job.grouped, "skipped": list(job.skipped), "diff_chars": job.diff_chars},
            "reply": result.reply, "data": result.envelope, "route": ""}


async def draft(state: dict, scope: Scope) -> dict:
    split, incoming = scope.split, scope.incoming
    style = style_of(incoming.text)
    facts = fx.build(split.parsed, split.context)
    headings = template.parse(split.template)
    try:
        digest = await drafting.digest(scope.deps, split.parsed, facts, incoming.policy)
        facts_text = drafting.facts_block(facts, headings, style.conventional, style.short, steps.code_steps(facts))
        model = await drafting.write(scope.deps, digest.text, facts_text, split.context, incoming.policy)
        if model.get("status") == "off_topic":
            return _reply(smalltalk.OFF_TOPIC_REPLY)
        job = Job(model, facts, split.parsed.raw, split.context, headings, style, digest.grouped, digest.skipped,
                  len(split.parsed.raw), incoming.wants_json)
        return _stored(await finish(scope.deps, job), job, split.template)
    except LlmError as e:
        return _failed(e, scope)


async def restyle(state: dict, scope: Scope) -> dict:
    facts = fx.Facts.from_dict(state.get("facts"))
    current = state.get("draft")
    if facts is None or not current:
        return _reply(prompts.NO_DRAFT_YET)
    text, incoming = scope.incoming.text, scope.incoming
    style = style_of(text, Style.from_dict(state.get("style")))
    context = state.get("context") or ""
    if followup.adds_why(text):
        context = f"{context}\n{text}".strip()
    headings = template.parse(state.get("template") or "")
    run = state.get("run") or {}
    try:
        facts_text = drafting.facts_block(facts, headings, style.conventional, style.short, steps.code_steps(facts))
        model = await followup.restyle(scope.deps, current, facts_text, context, text, incoming.policy)
        if model.get("status") == "off_topic":
            return _reply(smalltalk.OFF_TOPIC_REPLY)
        job = Job(model, facts, state.get("diff") or "", context, headings, style, bool(run.get("grouped")),
                  tuple(run.get("skipped") or ()), int(run.get("diff_chars") or 0), incoming.wants_json)
        return _stored(await finish(scope.deps, job), job, state.get("template") or "")
    except LlmError as e:
        return _failed(e, scope)


async def classify(state: dict, scope: Scope) -> dict:
    try:
        status = await followup.classify(scope.deps, scope.incoming.text, scope.incoming.policy)
    except LlmError as e:
        log.warning("classify failed: %s", e)
        return _reply(prompts.NEED_DIFF)
    if status == "off_topic":
        return _reply(smalltalk.OFF_TOPIC_REPLY)
    pending = hitl.Pending(context=scope.split.context, wants_json=scope.incoming.wants_json)
    return {"pause": pending.to_metadata(), "route": ""}
