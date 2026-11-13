"""The graph's node bodies: what each route does with the stored state and this turn's input.

A free-text follow-up on a stored memo is first checked against that memo (FOLLOWUP-SCOPE): a clearly unrelated
message is declined and the stored memo is left alone. A question is answered from the memo without touching it
(`discuss.py`); any other free text re-writes the prose over the stored analysis (scores and recommendation stay,
both decided in code). Only changed inputs (weights, options, data, "recommend X instead") re-score."""

from __future__ import annotations

import json

import analyse
import brief as b
import criteria as crit
import discuss
import envelope as envelope_mod
import followup as fu
import intake as intake_mod
import llm
import memo as memo_mod
import options as opts
import prompts
import recommend
import render
import smalltalk
import write
from inputs import Incoming


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: llm.TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, bool(state.get("memo")))
    scope.route = route
    if route.kind == "pause":
        return {"route": "", "pause": route.pending.to_metadata()}
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": {"new": "run", "followup": "followup", "revise": "followup"}.get(route.kind, "scope")}


async def run(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    return await compose(scope, scope.route.brief, None, notes=scope.route.notes)


async def scope_check(state: dict, scope: Scope) -> dict:
    """No options found: one classify call decides between a decline and asking for the options."""
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    try:
        data = await llm.complete_json(scope.deps, prompts.scope_messages(scope.route.reply, scope.incoming.policy),
                                       prompts.SCOPE_CONTRACT, _status)
    except llm.LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if data["status"] == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return {"pause": scope.route.pending.to_metadata()}


def _status(data: object) -> dict | None:
    return data if isinstance(data, dict) and data.get("status") in ("ok", "off_topic") else None


async def compose(scope: Scope, brief: b.Brief, analysis: dict | None, override: str = "", instruction: str = "",
                  notes: tuple[str, ...] = ()) -> dict:
    deps, policy = scope.deps, scope.incoming.policy
    try:
        if analysis is None:
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
    stored = {"brief": b.to_dict(brief), "analysis": analysis, "memo": memo, "basis": basis, "override": override}
    return {**stored, **_render(brief, analysis, memo, basis, scope.incoming.wants_json, notes)}


def _render(brief: b.Brief, analysis: dict, memo: dict, basis: dict, wants_json: bool, notes: tuple[str, ...],
            shorter: bool = False) -> dict:
    markdown, trim_notes = memo_mod.build(brief, analysis, memo, basis, shorter=shorter)
    items = memo_mod.open_items(markdown)
    env = envelope_mod.build(brief, analysis, memo, basis, markdown, items)
    text = render.render_json(env) if wants_json else render.reply(markdown, items, (*notes, *trim_notes), env)
    return {"envelope": env, "data": env, "reply": text}


async def followup(state: dict, scope: Scope) -> dict:
    command = scope.route.command
    brief = b.from_dict(state.get("brief"))
    analysis, memo, basis = state.get("analysis"), state.get("memo"), state.get("basis")
    override = state.get("override") or ""
    if command.kind == "json":
        return {"reply": render.render_json(state["envelope"]), "data": state["envelope"]}
    if command.kind == "just_rec":
        line = memo_mod.recommendation_line(brief, analysis, memo, basis)
        return {"reply": render.recommendation_only(line), "data": state["envelope"]}
    if command.kind == "shorter":
        return _render(brief, analysis, memo, basis, scope.incoming.wants_json, (), shorter=True)
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    if command.kind == "override":
        key = opts.match(command.text, brief.options)
        if not key:
            return {"reply": f'There\'s no option "{command.text}" in this memo. {_option_list(brief)}'}
        return await compose(scope, brief, analysis, override=key)
    if command.kind == "add_option":
        return await _add_option(scope, brief, command.text)
    if command.kind == "add_data":
        grown = b.replace(brief, extra=(*brief.extra, command.text))
        return await compose(scope, grown, analysis, override=override)
    if command.kind == "weights":
        return await _reweight(scope, brief, analysis, command.text)
    if not await _about_the_memo(scope, state, command.text):
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if discuss.is_question(command.text):
        return await _answer(scope, state, command.text)
    return await compose(scope, brief, analysis, override=override, instruction=command.text)


def _shown(state: dict) -> str:
    return (state.get("envelope") or {}).get("memo_markdown") or ""


async def _about_the_memo(scope: Scope, state: dict, text: str) -> bool:
    """One classify call with the stored memo as context: only a clearly unrelated message is declined, even
    though a memo is stored (it is not a revision). A failed call keeps the revision."""
    messages = prompts.followup_scope_messages(text, _shown(state), scope.incoming.policy)
    try:
        data = await llm.complete_json(scope.deps, messages, prompts.SCOPE_CONTRACT, _status)
    except llm.LlmError:
        return True
    return data["status"] != "off_topic"


async def _answer(scope: Scope, state: dict, question: str) -> dict:
    """A question on the stored memo: answered from it; nothing stored changes (no re-score, no rewrite)."""
    source = json.dumps(state.get("brief") or {}, ensure_ascii=False)
    try:
        return {"reply": await discuss.answer(scope.deps, _shown(state), source, question, scope.incoming.policy)}
    except llm.LlmError:
        return {"reply": prompts.MODEL_FAILED}


async def _add_option(scope: Scope, brief: b.Brief, text: str) -> dict:
    if len(brief.options) >= opts.MAX_OPTIONS:
        return {"reply": f"This memo already compares {opts.MAX_OPTIONS} options, the most one memo holds. "
                         "Start a new brief with the options you want."}
    added = opts.make([text])[0]
    option = opts.Option(opts.next_key(brief.options), added.name, added.text)
    grown = b.replace(brief, options=(*brief.options, option), extra=(*brief.extra, f"Option {option.key}: {text}"))
    return await compose(scope, grown, None)  # a fresh comparison: an earlier override no longer applies


async def _reweight(scope: Scope, brief: b.Brief, analysis: dict, text: str) -> dict:
    if not brief.criteria:
        return {"reply": "This memo has no criteria to reweight. Send a new brief with criteria, for example "
                         "\"Criteria: cost 40%, speed 35%, risk 25%\"."}
    changes, unknown = fu.weight_changes(text, brief.criteria)
    if unknown or not changes or sum(changes.values()) > 100:
        names = ", ".join(c.name for c in brief.criteria)
        return {"reply": f"I couldn't apply those weights. The criteria are: {names}. Say, for example, "
                         f"\"change the weights: {brief.criteria[0].name} 60%\" (new weights add up to 100% at most)."}
    reweighted = b.replace(brief, criteria=crit.reweight(brief.criteria, changes))
    return await compose(scope, reweighted, analysis)  # new weights mean the criteria decide again


def _option_list(brief: b.Brief) -> str:
    return "The options are " + "; ".join(f"{o.key} ({o.name})" for o in brief.options) + "."
