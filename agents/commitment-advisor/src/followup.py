"""Turns without a new paste. With a stored result: an option change ("1y discount 30%", "3 years", "min
utilization 90%", "compute only") re-runs the pipeline in code with no model call; a question about the existing
commitments (lapse, renew, expiring) is answered in code from their computed actions; any other question is one
call answered only from the fenced result, its numbers checked against it. With nothing stored: the paste
instructions, or the decline when the message is out of scope."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import envelope
import explain
import fmt
import llm
import params as params_mod
import prompts
import render
import smalltalk
from inputs import Incoming
from llm import TurnDeps
from turns import TurnResult, compute, finish, reply

STATUSES = ("answer", "off_topic", "needs_paste", "rerun")
ANSWER_MAX_CHARS = 900
RERUN_NOTE = "Re-run in code with no model call; the explanation is the code template."
NEED_REPASTE = "That usage paste was too large to keep for follow-ups. Paste it again with the new option."
UNGROUNDED = "I can only answer with the numbers computed in code. Here is the stored result: "
_DOMAIN = re.compile(r"\b(?:savings? plans?|commitments?|reserved|reservations?|\bri\b|ris|usage|discount|"
                     r"renew\w*|lapse|utili[sz]ation|on[- ]demand|cloud (?:cost|spend|bill))\b", re.IGNORECASE)
_EXISTING_Q = re.compile(r"\b(?:lapse|renew\w*|expir\w*|existing|current (?:plans?|commitments?))\b", re.IGNORECASE)
NO_EXISTING = "No existing commitments were pasted. Add them after a line `--- commitments` with the usage."
_JSON_ONLY = re.compile(r"^\s*(?:as json|json only|in json|show (?:it )?as json)\s*[.!?]?\s*$", re.IGNORECASE)


def parse_answer(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in STATUSES:
        return None
    if data["status"] == "answer":
        if not isinstance(data.get("answer"), str) or not data["answer"].strip():
            return None
        return {"status": "answer", "answer": " ".join(data["answer"].split())}
    return {"status": data["status"]}


async def _ask(deps: TurnDeps, incoming: Incoming, facts: Mapping[str, Any] | None) -> dict | None:
    try:
        return await llm.complete_json(deps, prompts.followup_messages(incoming.text, facts, incoming.context),
                                       prompts.FOLLOWUP_CONTRACT, parse_answer)
    except llm.LlmError:
        return None


async def nothing_stored(incoming: Incoming, deps: TurnDeps) -> TurnResult:
    if _DOMAIN.search(incoming.text):
        return reply(prompts.PASTE_INSTRUCTIONS, incoming.notes)
    if not deps.traceparent:
        return reply(prompts.NO_TRACE_CONTEXT)
    answer = await _ask(deps, incoming, None)
    if answer is None or answer["status"] == "off_topic":
        return reply(smalltalk.OFF_TOPIC_REPLY)
    return reply(prompts.PASTE_INSTRUCTIONS, incoming.notes)


def rerun(incoming: Incoming, state: Mapping[str, Any], new: params_mod.Params) -> TurnResult:
    paste = state.get("paste") or {}
    if not paste.get("usage"):
        return reply(NEED_REPASTE)
    merged = params_mod.merge(params_mod.Params.from_dict(state["result"].get("params")), new)
    fresh = compute(paste, merged)
    if isinstance(fresh, str):
        return reply(fresh)
    fresh = explain.with_template(fresh)
    return finish(fresh, incoming, stored={"paste": paste, "result": fresh}, notes=(RERUN_NOTE,))


async def on_stored(incoming: Incoming, deps: TurnDeps, state: Mapping[str, Any]) -> TurnResult:
    result = state["result"]
    new = params_mod.parse(incoming.text)
    if not params_mod.is_empty(new):
        return rerun(incoming, state, new)
    if _JSON_ONLY.match(incoming.text):
        return finish(result, incoming)
    if _EXISTING_Q.search(incoming.text):
        lines = [render.existing_line(e) for e in result["existing"]]
        return reply("**Existing commitments** (computed in code)\n" + "\n".join(lines) if lines else NO_EXISTING)
    if not deps.traceparent:
        return reply(prompts.NO_TRACE_CONTEXT)
    facts = fmt.digest(result)
    answer = await _ask(deps, incoming, facts)
    if answer is None:
        return reply(UNGROUNDED + envelope.summary(result))
    if answer["status"] == "off_topic":
        return reply(smalltalk.OFF_TOPIC_REPLY)
    if answer["status"] == "needs_paste":
        return reply(prompts.PASTE_INSTRUCTIONS)
    if answer["status"] == "rerun":
        return reply(prompts.FOLLOWUPS)
    text = answer["answer"]
    if len(text) > ANSWER_MAX_CHARS or fmt.ungrounded(text, fmt.allowed(facts)) or explain.is_unsafe(text):
        return reply(UNGROUNDED + envelope.summary(result))
    return reply(text, incoming.notes)
