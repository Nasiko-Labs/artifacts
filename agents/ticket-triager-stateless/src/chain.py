"""The ticket triager as a LangChain LCEL runnable (stateless).

    intake ─┬─ triage (signals: model, batched -> check -> rules -> render)
            ├─ followup (cut from the table in history: 0 calls; else triage again)
            ├─ scope (one classify call: decline / help)
            └─ fixed reply (greeting, help, missing tickets: no call)

`RunnableLambda(intake) | RunnableBranch(...)`: each step returns the turn's state with its own changes merged
in. There is no memory class and no chat history store: the state is built fresh each turn; the batch and
triage are re-read from the conversation history the platform sends (`recall.py`). Any replica can answer any
message, and every turn ends the task.
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


class TriageState(TypedDict, total=False):
    config: dict              # this turn only: SLA / routing / view defaults from the instruction
    route: str                # this turn only
    reply: str                # this turn only
    data: dict | None         # this turn only: the envelope to attach as a data part


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class TicketTriagerAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
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
        (routed("triage"), step(turns.triage)),
        (routed("followup"), step(turns.followup_turn)),
        (routed("scope"), step(turns.scope_check)),
        RunnableLambda(lambda state: state),  # a fixed reply: intake already wrote it
    )
    return RunnableLambda(intake) | branch
