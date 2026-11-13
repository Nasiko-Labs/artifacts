"""The decision-memo writer as a LangChain LCEL runnable (stateless).

    intake ─┬─ run      (analyse -> score -> write -> check -> render)
            ├─ followup (served from the last reply in history, or rebuilt from the brief in history)
            ├─ scope    (one classify call: decline / ask for the options)
            └─ fixed reply (no call)

`RunnableLambda(intake) | RunnableBranch(...)`: each step returns the turn's state with its own changes merged
in. A greeting never reaches the chain (smalltalk.py). There is no memory class and no chat history store:
the brief, its changes and the last memo are re-read from the conversation history the platform sends
(`recall.py`). Any replica can answer any message, and every turn ends the task.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypedDict

from langchain_core.runnables import Runnable, RunnableBranch, RunnableLambda

import recall
import smalltalk
import turns
from inputs import Incoming
from llm import TurnDeps


class MemoState(TypedDict, total=False):
    route: str              # this turn only
    reply: str              # this turn only
    data: dict | None       # this turn only: the envelope to attach as a data part


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class MemoAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:  # a greeting or small talk: no model call
            return TurnResult(canned)
        scope = turns.Scope(deps, incoming, recall.read(incoming.history))
        final = await build_chain(scope).ainvoke({"route": "", "reply": "", "data": None})
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
