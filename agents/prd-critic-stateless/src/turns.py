"""What each route does with this turn's input and the PRD and review re-read from history (`recall.py`; the
turn's state dict lives for this turn only). A view of a review found in history costs
no call; when only the PRD survived, it is reviewed again first."""

from __future__ import annotations

import intake as intake_mod
import prompts
import render
import replay
import review
import smalltalk
from inputs import Incoming, options
from llm import TurnDeps
from recall import Prior

FAILED_REPLIES = {"failed": prompts.MODEL_FAILED, "unusable": prompts.MODEL_UNUSABLE}


class Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, prior: Prior | None = None):
        self.deps, self.incoming, self.prior = deps, incoming, prior or Prior()
        self.route: intake_mod.Route | None = None


def seed(prior: Prior) -> dict:
    """The starting state for one turn: what history still holds."""
    state: dict = {"review_text": prior.review_text}
    if prior.prd is not None:
        state.update(prd=prior.prd.text, instruction=prior.prd.instruction)
    if prior.envelope is not None:
        state["envelope"] = prior.envelope
    return state


def intake(state: dict, scope: Scope) -> dict:
    route = intake_mod.plan_turn(scope.incoming, scope.prior)
    scope.route = route
    if route.kind == "reply":
        return {"route": "", "reply": route.reply}
    return {"route": route.kind}


async def run_review(state: dict, scope: Scope) -> dict:
    route = scope.route
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    opts = options(route.instruction)
    result = await review.run(scope.deps, route.prd, opts, scope.incoming.policy, route.one_block)
    if result.status in FAILED_REPLIES:
        return {"reply": FAILED_REPLIES[result.status]}
    if result.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    env = result.envelope
    shown = render.view(env, opts.focus) if opts.focus else env
    reply = render.render_json(shown) if scope.incoming.wants_json else render.render(env, opts.focus, route.notes)
    return {"prd": route.prd, "instruction": route.instruction, "envelope": env, "data": shown, "reply": reply}


async def followup(state: dict, scope: Scope) -> dict:
    kind = scope.route.command.kind
    if kind == "versions":
        return {"reply": prompts.NO_VERSIONS}
    if state.get("envelope"):
        return _view(state["envelope"], kind, scope)
    if state.get("review_text") and kind in replay.VIEWABLE:
        return {"reply": replay.view(state["review_text"], kind)}
    if not state.get("prd"):
        return {"reply": prompts.NOT_IN_HISTORY}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    opts = options(state.get("instruction") or "")
    result = await review.run(scope.deps, state["prd"], opts, scope.incoming.policy)
    if result.status != "ok":
        return {"reply": FAILED_REPLIES.get(result.status, smalltalk.OFF_TOPIC_REPLY)}
    return _view(result.envelope, kind, scope, (prompts.RE_REVIEW_NOTE,))


def _view(env: dict, kind: str, scope: Scope, notes: tuple[str, ...] = ()) -> dict:
    """A view of a review in hand, with no call."""
    if kind == "breakdown":
        return {"reply": "\n\n".join([render.render_breakdown(env), *notes]), "data": env}
    if kind == "json":
        return {"reply": render.render_json(env), "data": env}
    focus = "" if kind == "all" else kind
    shown = render.view(env, focus) if focus else env
    reply = render.render_json(shown) if scope.incoming.wants_json else render.render(env, focus, notes)
    return {"reply": reply, "data": shown}


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    status = await review.classify_scope(scope.deps, scope.incoming.text, scope.incoming.policy)
    if status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if state.get("envelope") or state.get("review_text") or state.get("prd"):
        return {"reply": prompts.FOLLOWUP_HELP}
    return {"reply": intake_mod.ask_for_prd(scope.incoming)}
