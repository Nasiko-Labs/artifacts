"""Turns without a new export: a follow-up on the stored analysis, or (nothing stored) the paste question /
the scope check. Only "explain" and an unrecognised message cost a model call."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import analysis
import columns
import followup
import hitl
import llm
import narrate
import prompts
import smalltalk
from frame import Frame, headers_of, load
from inputs import Incoming
from llm import TurnDeps
from turns import TurnResult, finish, reply

RECOMPUTED = "_Recomputed in code with no model call; the narrative is the code template. Say \"explain it\" for a new one._"
_BILLING_WORDS = re.compile(r"\b(?:billing|bill|export|csv|cost|costs|spend|spending|movers?|anomal\w*|untagged|"
                            r"invoice|budget|analy[sz]e)\b", re.IGNORECASE)


def parse_classify(data: Any) -> dict | None:
    if isinstance(data, Mapping) and data.get("status") in ("off_topic", "in_scope"):
        return {"status": data["status"]}
    return None


async def _classify(deps: TurnDeps, incoming: Incoming, has_analysis: bool) -> str | None:
    """off_topic | in_scope, or None when no model call could be made."""
    if not deps.traceparent:
        return None
    try:
        parsed = await llm.complete_json(deps, prompts.classify_messages(incoming.text, has_analysis, incoming.context),
                                         prompts.CLASSIFY_CONTRACT, parse_classify)
    except llm.LlmError:
        return None
    return parsed["status"]


async def nothing_stored(incoming: Incoming, deps: TurnDeps) -> TurnResult:
    """No export pasted and none stored: ask for the paste, unless the message is out of scope."""
    if not _BILLING_WORDS.search(incoming.text):
        status = await _classify(deps, incoming, False)
        if status is None and not deps.traceparent:
            return reply(prompts.NO_TRACE_CONTEXT)
        if status != "in_scope":
            return reply(smalltalk.OFF_TOPIC_REPLY)
    return TurnResult("", pause=hitl.Pending(hitl.NEED_SOURCE, instruction=prompts.strip_markers(incoming.text)[:300]))


def stored_frame(stored_csv: str) -> Frame | None:
    if not stored_csv:
        return None
    cols = columns.detect(headers_of(stored_csv))
    return None if cols.missing else load(stored_csv, cols)


async def on_stored(incoming: Incoming, deps: TurnDeps, result: dict, stored_csv: str) -> TurnResult:
    command = followup.parse(incoming.text)
    if command is None:
        frame = stored_frame(stored_csv)
        answer = followup.cost_query(incoming.text, frame) if frame is not None else None
        if answer is not None:
            return reply(answer, incoming.notes)
        status = await _classify(deps, incoming, True)
        if status is None and not deps.traceparent:
            return reply(prompts.NO_TRACE_CONTEXT)
        if status != "in_scope":
            return reply(smalltalk.OFF_TOPIC_REPLY)
        return reply(prompts.FOLLOWUPS, incoming.notes)
    if command.kind == "view":
        return finish(result, incoming, command.view)
    if command.kind == "explain":
        if not deps.traceparent:
            return reply(prompts.NO_TRACE_CONTEXT)
        narrated = await narrate.narrate(deps, result, incoming.text, incoming.context)
        if narrated.status == "off_topic":
            return reply(smalltalk.OFF_TOPIC_REPLY)
        return finish(narrated.result, incoming, stored={"result": narrated.result})
    frame = stored_frame(stored_csv)
    if frame is None:
        return reply(prompts.NEED_REPASTE)
    old = analysis.Options.from_dict(result.get("options"))
    options = analysis.Options(window_days=command.window_days or old.window_days, top=command.top or old.top,
                               instruction=old.instruction, budget=old.budget)
    fresh = narrate.with_template(analysis.run(frame, options, tuple(result.get("guessed") or ())))
    return finish(fresh, incoming, command.view, stored={"result": fresh}, notes=(RECOMPUTED,))
