"""Intake for a new request: parse in code (request vs pasted, window, angle, repos), then one scope call
that returns ok / need_topic / refine / off_topic. Off-topic ends with the fixed decline before any fetch."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

import brief
import inputs
import pasted as pasted_mod
import prompts
import smalltalk
from deps import TurnDeps
from hitl import NEED_TOPIC, Pending
from llm import LlmError, call_json

log = logging.getLogger("opportunity_brief.intake")

STATUSES = ("ok", "need_topic", "refine", "off_topic")


@dataclass(frozen=True)
class Decision:
    status: str
    topic: str = ""
    topic_stated: bool = True
    instruction: str = ""


@dataclass(frozen=True)
class Parsed:
    request: inputs.Request
    pasted: pasted_mod.Pasted


def parse(text: str) -> Parsed:
    envelope = pasted_mod.from_envelope(text)
    if envelope is not None:
        ask = f"Opportunity brief on {envelope.topic}" if envelope.topic else "What's the opportunity in these items?"
        return Parsed(inputs.parse(ask), envelope)
    req = inputs.parse(text)
    return Parsed(req, pasted_mod.parse(req.pasted))


async def decide(deps: TurnDeps, parsed: Parsed, prior_topic: str | None) -> Decision:
    titles = [i.title for i in parsed.pasted.items]
    answer = await call_json(deps, prompts.INTAKE_SYSTEM,
                             prompts.intake_user(parsed.request.request, titles, prior_topic), max_tokens=300)
    decision = read_decision(answer, has_paste=bool(titles or parsed.pasted.hn_ids), has_prior=prior_topic is not None)
    hint = inputs.topic_hint(parsed.request.request)
    log.info("intake: model said %r (topic %r); code topic hint %r", answer.get("status"), decision.topic, hint)
    if decision.status == "need_topic" and hint:
        return Decision("ok", hint, True)  # the user named one in plain words; never ask for what was given
    return decision


def read_decision(answer: Mapping, has_paste: bool, has_prior: bool) -> Decision:
    status = str(answer.get("status") or "").casefold()
    if status not in STATUSES:
        status = "off_topic"  # fail closed: never brief on something unreadable
    topic = inputs.clean_topic(answer.get("topic") or "")
    if status == "refine" and not has_prior:
        status = "ok" if topic else "need_topic"
    if status == "ok" and not topic:
        status = "need_topic"
    stated = bool(answer.get("topic_stated", True)) if status == "ok" else True
    if status == "ok" and not stated and not has_paste:
        stated = True  # nothing pasted to brief on: the topic must be the user's own
    return Decision(status, topic, stated, inputs.clean_topic(answer.get("instruction") or ""))


async def new_request(deps: TurnDeps, text: str, wants_json: bool, state: Mapping) -> tuple[brief.Outcome, Pending | None]:
    """(outcome, pause). A pause carries no outcome reply; the executor renders the question."""
    if not deps.traceparent:
        return brief.Outcome(prompts.NO_TRACE_CONTEXT), None
    parsed = parse(text)
    prior = (state.get("envelope") or {}).get("topic") if state else None
    try:
        decision = await decide(deps, parsed, prior)
    except LlmError:
        return brief.Outcome(prompts.FAILED_MODEL), None
    if decision.status == "off_topic":
        return brief.Outcome(smalltalk.OFF_TOPIC_REPLY), None
    if decision.status == "need_topic":
        return brief.Outcome(""), Pending(NEED_TOPIC, text, wants_json=wants_json)
    if decision.status == "refine":
        return await brief.refine(deps, state, decision.instruction or parsed.request.request), None
    spec = brief.spec_from(decision.topic, decision.topic_stated, parsed.request, parsed.pasted)
    return await brief.run(deps, spec), None


async def with_topic(deps: TurnDeps, pending: Pending, topic_text: str) -> tuple[brief.Outcome, Pending | None]:
    """Resume after Q1: the typed topic joins the original message and goes through intake again."""
    first, _, rest = pending.request.strip().partition("\n\n")
    text = f"Opportunity brief on {topic_text}. {first}".strip() + (f"\n\n{rest}" if rest else "")
    return await new_request(deps, text, pending.wants_json, {})
