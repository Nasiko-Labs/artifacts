"""Turns without a new export: a follow-up on the stored analysis, or (nothing stored) the paste question / the
scope check. Views, "top N", the what-if and "cost of X" cost zero model calls; only a message no rule
recognises costs one (the scope check)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import analysis
import followup
import hitl
import llm
import prompts
import smalltalk
import whatif
from inputs import Incoming
from llm import TurnDeps
from turns import TurnResult, finish, load_frame, reply

RECOMPUTED = "_Recomputed in code with no model call._"
KEPT = ("summary", "recommendations", "rec_source", "recs_dropped")
_USAGE_WORDS = re.compile(r"\b(?:usage|export|csv|json|cost|costs|spend|spending|spent|tokens?|waste|wasting|"
                          r"retr(?:y|ies)|cache|caching|bill|billing|invoice|finops|analy[sz]e)\b", re.IGNORECASE)


def parse_classify(data: Any) -> dict | None:
    if isinstance(data, Mapping) and data.get("status") in ("off_topic", "in_scope"):
        return {"status": data["status"]}
    return None


async def _classify(deps: TurnDeps, incoming: Incoming, has_analysis: bool) -> str | None:
    """off_topic | in_scope, or None when no model call could be made."""
    if not deps.traceparent:
        return None
    try:
        parsed = await llm.complete_json(deps, prompts.classify_messages(incoming.text, has_analysis,
                                                                         incoming.context),
                                         prompts.CLASSIFY_CONTRACT, parse_classify)
    except llm.LlmError:
        return None
    return parsed["status"]


async def _scope_reply(incoming: Incoming, deps: TurnDeps, has_analysis: bool) -> TurnResult | None:
    """None when in scope; else the decline (or the no-trace reply)."""
    status = await _classify(deps, incoming, has_analysis)
    if status is None and not deps.traceparent:
        return reply(prompts.NO_TRACE_CONTEXT)
    return None if status == "in_scope" else reply(smalltalk.OFF_TOPIC_REPLY)


async def nothing_stored(incoming: Incoming, deps: TurnDeps) -> TurnResult:
    """No export pasted and none stored: ask for the paste, unless the message is out of scope."""
    if not _USAGE_WORDS.search(incoming.text):
        declined = await _scope_reply(incoming, deps, False)
        if declined is not None:
            return declined
    return TurnResult("", pause=hitl.Pending(hitl.NEED_SOURCE, instruction=prompts.strip_markers(incoming.text)[:300]))


async def on_stored(incoming: Incoming, deps: TurnDeps, result: dict, canonical: Mapping) -> TurnResult:
    command = followup.parse(incoming.text)
    instruction = str((result.get("options") or {}).get("instruction") or "")
    if command is None:
        frame = load_frame(canonical, instruction)
        answer = followup.cost_query(incoming.text, frame, result["team_label"]) if frame is not None else None
        if answer is not None:
            return reply(answer, incoming.notes)
        declined = await _scope_reply(incoming, deps, True)
        return declined or reply(prompts.FOLLOWUPS, incoming.notes)
    if command.kind == "view":
        return finish(result, incoming, command.view)
    frame = load_frame(canonical, instruction)
    if frame is None:
        return reply(prompts.NEED_REPASTE)
    if command.kind == "whatif":
        computed = whatif.compute(frame, command.ask)
        if incoming.wants_json:
            return finish(result, incoming, what_if=computed)
        return reply(whatif.render(computed), incoming.notes)
    options = analysis.Options(top=command.top or analysis.aggregate.TOP_N, instruction=instruction)
    fresh = {**analysis.run(frame, options), **{k: result[k] for k in KEPT if k in result}}
    return finish(fresh, incoming, command.view, stored={"result": fresh}, notes=(RECOMPUTED,))
