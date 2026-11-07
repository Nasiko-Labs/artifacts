"""The graph's node bodies: what each route does with the stored state and this turn's input."""

from __future__ import annotations

from dataclasses import replace

import coverage
import draft as draft_mod
import envelope as envelope_mod
import followup as followup_mod
import intake as intake_mod
import pauses
import pipeline
import prompts
import render
import smalltalk
from envelope import Review
from evidence import parse
from inputs import Incoming
from llm import TurnDeps
from scan import scan

REMOVED = "removed by the manager"


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake_mod.Route | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.route: intake_mod.Route | None = None


def intake(state: dict, scope: Scope) -> dict:
    route = scope.forced or intake_mod.plan_turn(scope.incoming, bool(state.get("review")), scope.deps.today.year)
    scope.route = route
    if route.kind == "pause":
        return {"route": "", "pause": route.pending.to_metadata()}
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": route.kind}


async def run(state: dict, scope: Scope) -> dict:
    route = scope.route
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    outcome = await pipeline.run(scope.deps, route.items, route.header or {}, scope.incoming.policy, route.notes,
                                 route.left_out)
    header = route.header or {}
    return {"header": header, **_finish(outcome, scope, header)}


def _finish(outcome: pipeline.Outcome, scope: Scope, header: dict) -> dict:
    if outcome.status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    if outcome.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return _result(outcome.review, scope, header)


def _markdown(review: Review, names: list[str]) -> str:
    if review.fallback:
        return render.render_table(review, names) + "\n\n" + prompts.DEADLINE_NOTE
    body = render.render(review)
    return body if review.statements else prompts.NOTHING_BACKED + "\n\n" + body


def _result(review: Review, scope: Scope, header: dict) -> dict:
    names = list(coverage.competencies(header))
    markdown = _markdown(review, names)
    env = envelope_mod.build(review, markdown)
    reply = render.render_json(env) if scope.incoming.wants_json else markdown
    return {"review": envelope_mod.to_state(review), "data": env, "reply": reply}


async def followup(state: dict, scope: Scope) -> dict:
    command = scope.route.command
    review = envelope_mod.from_state(state.get("review"))
    header = state.get("header") or {}
    if command.kind == "evidence":
        return {"reply": render.render_evidence(review, command.section)}
    if command.kind == "gaps":
        return {"reply": render.render_gaps(review)}
    if command.kind == "json":
        env = envelope_mod.build(review, render.render(review))
        return {"reply": render.render_json(env), "data": env}
    if command.kind == "shorter":
        short = replace(review, statements=tuple(draft_mod.shorter(review.statements)))
        return _result(short, scope, header)
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    if command.kind == "remove":
        return await _remove(review, header, command.ids, scope)
    return await _add(review, header, command.text, scope)


async def _remove(review: Review, header: dict, ids: tuple[str, ...], scope: Scope) -> dict:
    known = {e.id for e in review.evidence}
    missing = [i for i in ids if i not in known]
    if missing:
        return {"reply": f"There's no {', '.join(missing)} in this evidence (it has e1 to e{len(review.evidence)})."}
    evidence = tuple(replace(e, excluded=REMOVED) if e.id in ids else e for e in review.evidence)
    note = f"_Removed {', '.join(ids)} and re-drafted._"
    outcome = await pipeline.compose(scope.deps, evidence, header, scope.incoming.policy, (note,), review.left_out)
    return _finish(outcome, scope, header)


async def _add(review: Review, header: dict, text: str, scope: Scope) -> dict:
    added = parse(text, scope.deps.today.year, find_instruction=False).items
    if not added:
        return {"reply": "I couldn't find an example after \"add:\". Write it as \"add: <what, when, outcome>\"."}
    start = len(review.evidence)
    renumbered = [replace(e, id=f"e{start + n}") for n, e in enumerate(added, start=1)]
    marked, left_out = scan(renumbered)
    marked = [replace(e, text=prompts.strip_markers(e.text)) for e in marked]
    note = f"_Added {', '.join(e.id for e in marked)} and re-drafted._"
    outcome = await pipeline.run(scope.deps, (*review.evidence, *marked), header, scope.incoming.policy, (note,),
                                 (*review.left_out, *left_out))
    return _finish(outcome, scope, header)


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    status = await followup_mod.classify(scope.deps, scope.incoming.text, scope.incoming.policy)
    if status == followup_mod.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if state.get("review"):
        return {"reply": prompts.FOLLOWUP_HELP}
    pending = pauses.Pending(pauses.NEED_EVIDENCE, scope.route.text, scope.incoming.wants_json)
    return {"pause": pending.to_metadata()}
