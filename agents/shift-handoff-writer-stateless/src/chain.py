"""The shift-handoff-writer-stateless as a LangChain LCEL runnable.

    intake ─┬─ build (extract -> reconcile -> check -> classify -> compose)
            ├─ followup (rebuild from the history, then view / json / mark / add)
            ├─ scope (one scope-check call)
            └─ fixed reply

`RunnableLambda(intake) | RunnableBranch(...)`: each step returns the turn's state with its own changes merged
in. Stateless: no memory class, no chat history store, and the state lives for this turn only.
Earlier work comes from the platform's chat history (`recall.Prior`). No turn ever pauses, so any replica
can serve any message."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypedDict

from langchain_core.runnables import Runnable, RunnableBranch, RunnableLambda

import intake
import prompts
import turns
from inputs import Incoming
from llm import TurnDeps
from recall import Prior, recall


class HandoffState(TypedDict, total=False):
    route: str
    reply: str
    data: dict | None        # the envelope for the data part


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class _Scope:
    """One turn's input, dependencies and recalled history (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming):
        self.deps, self.incoming = deps, incoming
        self.prior: Prior = recall(incoming.history)
        self.plan: intake.Plan | None = None


class ShiftHandoffAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
        final = await build_chain(_Scope(deps, incoming)).ainvoke({"route": "", "reply": "", "data": None})
        return TurnResult(final.get("reply") or "", final.get("data"))


def _intake(scope: _Scope) -> dict:
    plan = intake.plan_turn(scope.incoming, scope.prior)
    scope.plan = plan
    if plan.route == "reply":
        return {"route": "", "reply": turns.with_notes(plan.reply, plan.extra)}
    if plan.route != "followup" and not scope.deps.traceparent:
        return {"route": "", "reply": prompts.NO_TRACE_CONTEXT}
    return {"route": plan.route}


def build_chain(scope: _Scope) -> Runnable:
    def step(body: Callable[[], Awaitable[dict]]) -> Runnable:
        async def run(state: dict) -> dict:
            return {**state, **await body()}
        return RunnableLambda(run)

    def routed(name: str) -> Callable[[dict], bool]:
        return lambda state: state.get("route") == name

    branch = RunnableBranch(
        (routed("build"), step(lambda: turns.build(scope.deps, scope.plan, scope.incoming))),
        (routed("followup"), step(lambda: turns.followup(scope.deps, scope.plan, scope.incoming, scope.prior))),
        (routed("scope"), step(lambda: turns.scope_check(scope.deps, scope.plan, scope.incoming))),
        RunnableLambda(lambda state: state),  # a fixed reply: intake already wrote it
    )
    return RunnableLambda(lambda state: {**state, **_intake(scope)}) | branch
