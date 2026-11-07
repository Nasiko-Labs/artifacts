"""Turns without new alert data: a follow-up on the stored classification (code, no call), or — nothing
stored — the paste question / the scope check. Only an unrecognised message costs a model call (the scope
check); in scope, the answer comes only from the stored assumptions and the follow-up list."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import followup
import hitl
import llm
import prompts
import render
import smalltalk
import tune
from followup import Command
from inputs import Incoming
from llm import TurnDeps
from turns import TurnResult, finish, reply

_ALERT_WORDS = re.compile(r"\b(?:alerts?|alerting|pages?|paging|paged|fired|firing|noise|noisy|actionable|on-?call|"
                          r"pager|classify|incidents?)\b", re.IGNORECASE)


def parse_classify(data: Any) -> dict | None:
    if isinstance(data, Mapping) and data.get("status") in ("off_topic", "in_scope"):
        return {"status": data["status"]}
    return None


async def _classify(deps: TurnDeps, incoming: Incoming, has_result: bool) -> str | None:
    """off_topic | in_scope, or None when no model call could be made."""
    if not deps.traceparent:
        return None
    try:
        parsed = await llm.complete_json(deps, prompts.classify_messages(incoming.text, has_result, incoming.context),
                                         prompts.CLASSIFY_CONTRACT, parse_classify)
    except llm.LlmError:
        return None
    return parsed["status"]


async def nothing_stored(incoming: Incoming, deps: TurnDeps) -> TurnResult:
    """No alerts pasted and none stored: ask for the paste, unless the message is out of scope."""
    if not _ALERT_WORDS.search(incoming.text):
        status = await _classify(deps, incoming, False)
        if status is None and not deps.traceparent:
            return reply(prompts.NO_TRACE_CONTEXT)
        if status != "in_scope":
            return reply(smalltalk.OFF_TOPIC_REPLY)
    instruction = " ".join(prompts.strip_markers(incoming.text).split())[:300]
    return TurnResult("", pause=hitl.Pending(hitl.NEED_SOURCE, instruction=instruction))


def view(result: Mapping[str, Any], command: Command) -> str:
    if command.kind == "only":
        return render.only(result, command.label)
    if command.kind == "group":
        group = next(g for g in result["groups"] if g["key"] == command.group_key)
        return render.detail(result, group)
    if command.kind == "numbers":
        return render.headline(result) + "\n\n" + render.numbers_table(result["groups"])
    if command.kind == "sources":
        return render.sources(result)
    if command.kind == "top":
        return render.top(result, command.top)
    if command.kind == "what_if":
        return render.what_if(tune.what_if_for(result["groups"], command.for_secs), command.for_secs)
    return render.full(result)


def assumptions(result: Mapping[str, Any]) -> str:
    """An in-scope question the follow-ups don't cover: answered only from the stored assumption texts."""
    lines = [f"- {render.clean(s['name'])} ({render.clean(s['text'])}): {render.clean(s['assumption'])}."
             for s in result["tune"]]
    head = ("I can only answer from the stated assumptions of the tuning suggestions:" if lines
            else "There are no tuning suggestions, so there are no assumptions to go by.")
    return "\n".join([head, *lines, "", render.TRADE_OFF, "", prompts.FOLLOWUPS])


async def on_stored(incoming: Incoming, deps: TurnDeps, result: Mapping[str, Any]) -> TurnResult:
    command = followup.parse(incoming.text, result)
    if command is None and incoming.wants_json:
        command = Command("default")
    if command is not None:
        return finish(dict(result), incoming, view(result, command))
    status = await _classify(deps, incoming, True)
    if status is None and not deps.traceparent:
        return reply(prompts.NO_TRACE_CONTEXT)
    if status != "in_scope":
        return reply(smalltalk.OFF_TOPIC_REPLY)
    return reply(assumptions(result), incoming.notes)
