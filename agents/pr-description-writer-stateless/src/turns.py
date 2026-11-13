"""The turn steps: intake (code only), draft, restyle and classify. Each step returns this turn's
`reply` and `data`; nothing is kept for the next message.

Stateless: a follow-up reads the diff and the last draft back from the platform's chat history
(`recall.py`). With both there, the restyle is one call, as before. With the draft trimmed, a fresh draft is
written from the diff (plus a restyle when the request is more than style words). With the diff trimmed, the
reply says so and asks for it again. The "paste the diff" question is a plain reply; nothing pauses."""

from __future__ import annotations

import logging
import re
from dataclasses import replace

import draft as drafting
import facts as fx
import followup
import prompts
import recall
import smalltalk
import steps
import template
from finish import Finished, Job, finish
from inputs import Incoming, Split, Style, split_input, style_of
from llm import LlmError, TurnDeps

log = logging.getLogger("pr-description-writer-stateless.turns")

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b", re.IGNORECASE)
CANCEL_MAX_WORDS = 3


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming):
        self.deps = deps
        self.incoming = incoming
        self.split = split_input(incoming.text)
        self.earlier = recall.earlier_diff(incoming.history)
        if self.split.parsed.found and recall.answering_ask(incoming.history):
            asked = recall.ask_context(incoming.history)
            self.split = replace(self.split, context=f"{asked}\n\n{self.split.context}".strip())


def _reply(text: str) -> dict:
    return {"reply": text, "route": ""}


def _needs_trace(scope: Scope, route: str) -> dict:
    return {"route": route} if scope.deps.traceparent else _reply(prompts.NO_TRACEPARENT_REPLY)


def intake(state: dict, scope: Scope) -> dict:
    text, turns = scope.incoming.text, scope.incoming.history
    if scope.split.parsed.found:
        return _needs_trace(scope, "draft")
    if recall.answering_ask(turns) and _CANCEL.match(text.strip()) and len(text.split()) <= CANCEL_MAX_WORDS:
        return _reply(prompts.CANCELLED)
    earlier = scope.earlier
    if earlier is not None:
        if followup.asks_split(text):
            return _reply(followup.split_advice(fx.build(earlier.split.parsed, earlier.split.context)))
        return _needs_trace(scope, "restyle")
    drafted = any(t.text.startswith(recall.DRAFT_MARK) for t in turns)
    if drafted or (turns and (followup.is_json_only(text) or followup.asks_split(text))):
        return _reply(prompts.DIFF_TRIMMED)
    if followup.mentions_pr(text):
        return _reply(prompts.NEED_DIFF)
    return _needs_trace(scope, "classify")


def _failed(error: LlmError, scope: Scope) -> dict:
    log.warning("model step failed: %s", error)
    return _reply(prompts.BUDGET_SPENT if not scope.deps.has_time() or scope.deps.budget.calls
                  >= scope.deps.budget.max_calls else prompts.COULD_NOT_DRAFT)


def _out(result: Finished, note: str = "") -> dict:
    reply = f"{result.reply}\n\n{note}" if note and not result.reply.startswith("```json") else result.reply
    return {"reply": reply, "data": result.envelope, "route": ""}


async def _write(scope: Scope, split: Split, style: Style, context: str, wants_json: bool) -> Job | dict:
    """A fresh draft of `split`'s diff: the digest and the draft call. A Job ready to finish, or a reply."""
    policy = scope.incoming.policy
    facts = fx.build(split.parsed, split.context)
    headings = template.parse(split.template)
    digest = await drafting.digest(scope.deps, split.parsed, facts, policy)
    facts_text = drafting.facts_block(facts, headings, style.conventional, style.short, steps.code_steps(facts))
    model = await drafting.write(scope.deps, digest.text, facts_text, context, policy)
    if model.get("status") == "off_topic":
        return _reply(smalltalk.OFF_TOPIC_REPLY)
    return Job(model, facts, split.parsed.raw, context, headings, style, digest.grouped, digest.skipped,
               len(split.parsed.raw), wants_json)


async def draft(state: dict, scope: Scope) -> dict:
    split, incoming = scope.split, scope.incoming
    try:
        job = await _write(scope, split, style_of(incoming.text), split.context, incoming.wants_json)
        return job if isinstance(job, dict) else _out(await finish(scope.deps, job))
    except LlmError as e:
        return _failed(e, scope)


async def restyle(state: dict, scope: Scope) -> dict:
    earlier, text, incoming = scope.earlier, scope.incoming.text, scope.incoming
    style = style_of(text, earlier.style)
    context = f"{earlier.context}\n{text}".strip() if followup.adds_why(text) else earlier.context
    as_json = incoming.wants_json or followup.is_json_only(text)
    try:
        if as_json or not earlier.draft:
            job = await _write(scope, earlier.split, style, context, as_json)
            if isinstance(job, dict):
                return job
            if as_json or recall.style_only(text):
                return _out(await finish(scope.deps, job), prompts.REBUILT)
            current = (await finish(scope.deps, job)).draft
        else:
            current = {"current_draft_markdown": earlier.draft}
            job = Job({}, fx.build(earlier.split.parsed, earlier.split.context), earlier.split.parsed.raw, context,
                      template.parse(earlier.split.template), style, diff_chars=len(earlier.split.parsed.raw))
        return await _restyled(scope, job, current, text)
    except LlmError as e:
        return _failed(e, scope)


async def _restyled(scope: Scope, job: Job, current: dict, request: str) -> dict:
    facts, style = job.facts, job.style
    facts_text = drafting.facts_block(facts, job.headings, style.conventional, style.short, steps.code_steps(facts))
    model = await followup.restyle(scope.deps, current, facts_text, job.context, request, scope.incoming.policy)
    if model.get("status") == "off_topic":
        return _reply(smalltalk.OFF_TOPIC_REPLY)
    return _out(await finish(scope.deps, replace(job, model=model, wants_json=scope.incoming.wants_json)))


async def classify(state: dict, scope: Scope) -> dict:
    try:
        status = await followup.classify(scope.deps, scope.incoming.text, scope.incoming.policy)
    except LlmError as e:
        log.warning("classify failed: %s", e)
        return _reply(prompts.NEED_DIFF)
    if status == "off_topic":
        return _reply(smalltalk.OFF_TOPIC_REPLY)
    return _reply(prompts.NEED_DIFF)
