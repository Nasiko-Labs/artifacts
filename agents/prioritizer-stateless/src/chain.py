"""The prioritizer as a LangChain LCEL runnable (stateless).

    intake ─┬─ run (rate 1-2 calls -> score -> rank -> render)
            ├─ followup (re-weight / pin / view: 0 calls; add, rate again)
            ├─ scope (one classify call: decline / ask / follow-up help)
            └─ fixed reply (greeting, help, missing list: no call)

`RunnableLambda(intake) | RunnableBranch(...)`: each step returns the turn's state with its own changes merged
in. There is no memory class and no chat history store: the state is built fresh each turn from the message,
seeded with the ranking (or task list) re-read from the conversation history the platform sends (`recall.py`).
Any replica can answer any message, and every turn ends the task.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypedDict

from langchain_core.runnables import Runnable, RunnableBranch, RunnableLambda

import recall
import turns
from inputs import Incoming
from llm import TurnDeps


class PrioritizerState(TypedDict, total=False):
    tasks: list[dict]         # [{"id": "t1", "text": ...}] in input order
    ratings: dict             # id -> checked rating (impact, impact_why, effort, delegable, delegate_to)
    goals: list[str]
    preset: str
    pins: list[str]           # task ids, most recent last
    as_of: str                # explicit as-of date (ISO) or "" for today
    defaulted: int
    failed: int
    instruction: str
    route: str                # this turn only
    reply: str                # this turn only
    data: dict | None         # this turn only: the envelope to attach as a data part
    rated: bool               # False: a bare list re-read from history, not rated yet


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class PrioritizerAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
        seed = recall.latest(incoming.history) or {}
        final = await build_chain(turns.Scope(deps, incoming)).ainvoke(
            {**seed, "route": "", "reply": "", "data": None})
        return TurnResult(final.get("reply") or "", final.get("data"))


Step = Callable[[dict, turns.Scope], Awaitable[dict]]


def build_chain(scope: turns.Scope) -> Runnable:
    def intake(state: dict) -> dict:
        return {**state, **turns.intake(state, scope)}

    def step(body: Step) -> Runnable:
        async def run(state: dict) -> dict:
            return {**state, **await body(state, scope)}
        return RunnableLambda(run)

    def routed(name: str) -> Callable[[dict], bool]:
        return lambda state: state.get("route") == name

    branch = RunnableBranch(
        (routed("run"), step(turns.run)),
        (routed("followup"), step(turns.followup)),
        (routed("scope"), step(turns.scope_check)),
        RunnableLambda(lambda state: state),  # a fixed reply: intake already wrote it
    )
    return RunnableLambda(intake) | branch
