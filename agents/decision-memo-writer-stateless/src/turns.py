"""The chain's step bodies: what each route does with this turn's input and the brief re-read from the
conversation history (`recall.py`; this agent keeps no state). A follow-up the last reply in history can
answer ("just the recommendation", "shorter", "as json" after a JSON reply) costs no call; every other follow-up
rebuilds the memo from the earlier brief with its earlier changes replayed in code (`amend.py`), then this one.
A free-text message is first classified: an unrelated question is declined, not treated as a revision. A
question or wording change keeps the shown memo's scores and recommendation (`discuss.py`); only changed inputs
(weights, options, data, "recommend X instead") re-analyse and re-score."""

from __future__ import annotations

import amend
import analyse
import brief as b
import condense
import discuss
import envelope as envelope_mod
import intake as intake_mod
import llm
import memo as memo_mod
import prompts
import recall
import recommend
import render
import smalltalk
import write
from history import ASSISTANT
from inputs import Incoming

REBUILT = {"json", "just_rec", "shorter", "revise"}


class Scope:
    """One turn's input, dependencies and what the history holds (never persisted)."""

    def __init__(self, deps: llm.TurnDeps, incoming: Incoming, prior: recall.Prior):
        self.deps, self.incoming, self.prior = deps, incoming, prior
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = intake_mod.plan_turn(scope.incoming, scope.prior)
    scope.route = route
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": {"new": "run"}.get(route.kind, route.kind)}


async def run(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    return await compose(scope, scope.route.brief, notes=scope.route.notes)


async def scope_check(state: dict, scope: Scope) -> dict:
    """No options found: one classify call decides between a decline and asking for the options."""
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    try:
        data = await llm.complete_json(scope.deps, prompts.scope_messages(scope.route.source, scope.incoming.policy),
                                       prompts.SCOPE_CONTRACT, _status)
    except llm.LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if data["status"] == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return {"reply": prompts.ASK_OPTIONS}


def _status(data: object) -> dict | None:
    return data if isinstance(data, dict) and data.get("status") in ("ok", "off_topic") else None


async def compose(scope: Scope, brief: b.Brief, override: str = "", instruction: str = "",
                  notes: tuple[str, ...] = (), view: str = "") -> dict:
    """Analyse, score in code, write, check, render. `view`: "" (the memo), shorter, just_rec or json."""
    deps, policy = scope.deps, scope.incoming.policy
    try:
        analysis = await analyse.run(deps, brief, policy)
        if analysis["status"] == "off_topic":
            return {"reply": smalltalk.OFF_TOPIC_REPLY}
        basis = recommend.decide(brief, analysis, override)
        messages = prompts.write_messages(analyse.view(brief), analyse.model_view(analysis),
                                          recommend.model_view(brief, basis, instruction), policy)
        memo = await write.run(deps, messages, brief, analysis, basis)
    except llm.LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if memo["status"] == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    rendered = _render(brief, analysis, memo, basis, scope.incoming.wants_json or view == "json", notes,
                       shorter=view == "shorter")
    if view == "just_rec":
        line = memo_mod.recommendation_line(brief, analysis, memo, basis)
        return {**rendered, "reply": render.recommendation_only(line)}
    return rendered


def _render(brief: b.Brief, analysis: dict, memo: dict, basis: dict, wants_json: bool, notes: tuple[str, ...],
            shorter: bool = False) -> dict:
    markdown, trim_notes = memo_mod.build(brief, analysis, memo, basis, shorter=shorter)
    items = memo_mod.open_items(markdown)
    env = envelope_mod.build(brief, analysis, memo, basis, markdown, items)
    text = render.render_json(env) if wants_json else render.reply(markdown, items, (*notes, *trim_notes), env)
    return {"data": env, "reply": text}


async def followup(state: dict, scope: Scope) -> dict:
    command, prior = scope.route.command, scope.prior
    served = _from_history(command.kind, prior)
    if served is not None:
        return served
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    base = _rebuild(prior)
    if base is None:
        return {"reply": prompts.NOT_IN_HISTORY}
    if command.kind == "revise":
        if not await _about_the_memo(scope, command.text):
            return {"reply": smalltalk.OFF_TOPIC_REPLY}
        kept = await _without_rescoring(scope, command.text)
        if kept is not None:
            return kept
    notes = ("I rebuilt the memo from the brief in this conversation.",) if command.kind in REBUILT else ()
    if command.kind in amend.KINDS:
        step = amend.apply(base, command)
        if step.error:
            return {"reply": step.error}
        return await compose(scope, step.brief, override=step.override)
    instruction = command.text if command.kind == "revise" else ""
    view = command.kind if command.kind in ("json", "just_rec", "shorter") else ""
    return await compose(scope, base.brief, override=base.override, instruction=instruction, notes=notes, view=view)


async def _about_the_memo(scope: Scope, text: str) -> bool:
    """One classify call with the previous reply as context: only a clearly unrelated question is declined, even
    when a memo is in the history (it is not a follow-up). A failed call keeps the revision."""
    previous = next((t.text for t in reversed(scope.incoming.history) if t.role == ASSISTANT), "")
    messages = prompts.followup_scope_messages(text, previous, scope.incoming.policy)
    try:
        data = await llm.complete_json(scope.deps, messages, prompts.SCOPE_CONTRACT, _status)
    except llm.LlmError:
        return True
    return data["status"] != "off_topic"


async def _without_rescoring(scope: Scope, text: str) -> dict | None:
    """A question or wording change on the memo shown in history: answered or reworded with its scores and
    recommendation fixed (discuss.py). None when that memo is no longer in the history (then it is rebuilt)."""
    prior = scope.prior
    shown = discuss.memo_part(prior.memo_text or (prior.envelope or {}).get("memo_markdown") or "")
    if not recall.recommendation(shown):
        return None
    policy = scope.incoming.policy
    try:
        if discuss.is_question(text):
            return {"reply": await discuss.answer(scope.deps, shown, prior.source, text, policy)}
        return {"reply": await discuss.reword(scope.deps, shown, prior.source, text, policy)}
    except llm.LlmError:
        return {"reply": prompts.MODEL_FAILED}


def _shortened(prior: recall.Prior) -> dict | None:
    """Zero calls: the memo the user saw, cut down in code (same scores and recommendation)."""
    shown = prior.memo_text or (prior.envelope or {}).get("memo_markdown") or ""
    if not recall.recommendation(shown):
        return None
    text, shorter = condense.condense(shown)
    blocks = [text, *(() if shorter else (condense.ALREADY_SHORT,)), f"_{prompts.FOLLOWUP_HELP}_"]
    return {"reply": "\n\n".join(blocks)}


def _from_history(kind: str, prior: recall.Prior) -> dict | None:
    """Zero calls: the last memo in history already answers it."""
    if kind == "shorter":
        return _shortened(prior)
    if kind == "json" and prior.envelope is not None:
        return {"reply": render.render_json(prior.envelope), "data": prior.envelope}
    if kind == "just_rec":
        line = recall.recommendation(prior.memo_text or (prior.envelope or {}).get("memo_markdown") or "")
        if line:
            return {"reply": render.recommendation_only(line), "data": prior.envelope}
    return None


def _rebuild(prior: recall.Prior) -> amend.Amended | None:
    """The brief as the user last saw it: re-read, prepared as on its first turn, earlier changes replayed."""
    route = intake_mod.prepare(prior.source, b.parse(prior.source))
    if route.kind != "new":
        return None
    return amend.replay(route.brief, prior.changes)
