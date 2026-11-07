"""Follow-up turns on the stored result and the scope check for anything else."""

from __future__ import annotations

from dataclasses import dataclass, replace

import draft as draft_step
import prompts
import render
import smalltalk
from candidates import MIN_RANGE, TOP_RANGE, clamp
from followup import Command
from inputs import Incoming
from llm import LlmError, TurnDeps, complete_json
from report import Stored, envelope

MODEL_FAILED = "I couldn't reach the language model for that. Please try again."
WHICH_PATTERN = "Which pattern should I redraft? Say, for example, \"shorter draft for 2\"."


@dataclass(frozen=True)
class Outcome:
    text: str
    data: dict | None = None
    stored: Stored | None = None


def rendered(stored: Stored, json_view: bool) -> tuple[str, dict]:
    return render.render(stored, json_view)


async def followup(deps: TurnDeps, stored: Stored | None, command: Command, incoming: Incoming) -> Outcome:
    if stored is None:
        return Outcome(prompts.NOT_STORED)
    if command.show is not None:
        shown = render.show_replies(stored, command.show)
        return Outcome(shown or prompts.UNKNOWN_PATTERN.format(n=command.show))
    if command.redraft_needs_number:
        return Outcome(WHICH_PATTERN)
    if command.redraft is not None:
        return await _redraft(deps, stored, command, incoming)
    if command.boilerplate and not command.changes_view:
        return Outcome(render.boilerplate(stored))
    changed = replace(stored, min_freq=clamp(command.min_freq, stored.min_freq, MIN_RANGE),
                      top=clamp(command.top, stored.top, TOP_RANGE))
    text, data = rendered(changed, command.as_json or incoming.wants_json)
    return Outcome(text, data, changed)


async def _redraft(deps: TurnDeps, stored: Stored, command: Command, incoming: Incoming) -> Outcome:
    n = command.redraft
    candidates = stored.selection().candidates
    if not 1 <= n <= len(candidates):
        return Outcome(prompts.UNKNOWN_PATTERN.format(n=n))
    if not deps.traceparent:
        return Outcome(prompts.NO_TRACE_CONTEXT)
    group = candidates[n - 1][0]
    previous = stored.draft_for(group)
    new = await draft_step.redraft(deps, stored.job(group), previous, command.instruction, (), incoming.context)
    if new is None:
        return Outcome(prompts.REDRAFT_FAILED.format(n=n))
    changed = stored.with_draft(group.key, new)
    data = envelope(changed)
    block = render.candidate_block(data["items"][n - 1])
    text = f"{block}\n\n_(Redrafted and re-checked: it states only what at least two of its replies say.)_"
    return Outcome(render.as_json(data) if incoming.wants_json else text, data, changed)


def _valid_scope(data) -> dict | None:
    if isinstance(data, dict) and data.get("status") in ("on_topic", "off_topic"):
        return data
    return None


async def scope_reply(deps: TurnDeps, text: str, has_stored: bool) -> str:
    if not deps.traceparent:
        return prompts.NO_TRACE_CONTEXT
    try:
        answer = await complete_json(deps, prompts.scope_messages(text), prompts.SCOPE_CONTRACT, _valid_scope, 50)
    except LlmError:
        return MODEL_FAILED
    if answer["status"] == "off_topic":
        return smalltalk.OFF_TOPIC_REPLY
    return smalltalk.FOLLOWUP_HELP if has_stored else smalltalk.NO_REPLIES_YET
