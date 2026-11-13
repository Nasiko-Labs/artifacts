"""The escalation-summarizer-stateless as a LangChain LCEL runnable.

    intake ─┬─ summarize (extract → check → compose → check → render)
            ├─ followup (view / who said / restyle / one question)
            └─ fixed reply

`RunnableLambda(intake) | RunnableBranch(...)`: each step returns the turn's state with its own changes merged
in. Stateless: no memory class, no chat history store, and the state lives for this turn only.
Earlier work comes from the platform's chat history (`recall.Prior`): a follow-up on a thread still in the
history is worked from that thread again (extract, and compose when the view needs it); when only our last
reply is left, a view is cut from it or a question is answered from it; otherwise the user is asked to paste
the thread again. No turn ever pauses, so any replica can serve any message.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypedDict

from langchain_core.runnables import Runnable, RunnableBranch, RunnableLambda

import checks
import envelope
import extract as extract_step
import followup
import intake
import messages as msgs
import prompts
import render
import style as st
import timeline as tl
from compose import compose
from inputs import Incoming
from llm import TurnDeps
from recall import Prior, recall
from smalltalk import OFF_TOPIC_REPLY

CUT_VIEWS = ("ask", "impact")  # one line of our last summary; anything else rebuilds from the thread
UNATTRIBUTED = "I couldn't tell the messages apart (no headers I recognise), so speakers aren't attributed."
FACTS_LEFT_OUT = "{n} extracted facts were left out: their quotes aren't in the thread."


class EscalationState(TypedDict, total=False):
    route: str
    reply: str
    data: dict | None    # the envelope for the DataPart


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


class EscalationAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
        final = await build_chain(_Scope(deps, incoming)).ainvoke({"route": "", "reply": "", "data": None})
        return TurnResult(final.get("reply") or "", final.get("data"))


def _intake(scope: _Scope) -> dict:
    scope.plan = intake.plan_turn(scope.incoming, scope.prior)
    if scope.plan.route == "reply":
        return {"route": "", "reply": _with_notes(scope.plan.reply, scope.plan.notes)}
    return {"route": "summarize" if scope.plan.route == "summarize" else "followup"}


def build_chain(scope: _Scope) -> Runnable:
    def step(body: Callable[[_Scope], Awaitable[dict]]) -> Runnable:
        async def run(state: dict) -> dict:
            return {**state, **await body(scope)}
        return RunnableLambda(run)

    def routed(name: str) -> Callable[[dict], bool]:
        return lambda state: state.get("route") == name

    branch = RunnableBranch(
        (routed("summarize"), step(_summarize)),
        (routed("followup"), step(_followup)),
        RunnableLambda(lambda state: state),  # a fixed reply: intake already wrote it
    )
    return RunnableLambda(lambda state: {**state, **_intake(scope)}) | branch


async def _summarize(scope: _Scope) -> dict:
    plan = scope.plan
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    built = await _envelope_for(scope, plan.thread, st.parse(plan.instruction))
    if isinstance(built, str):
        return {"reply": _with_notes(built, plan.notes)}
    return {"reply": _reply(built, scope, "default", plan.notes), "data": built}


async def _extract(scope: _Scope, thread: str):
    """(found, messages), or a fixed reply when there is nothing to build on."""
    messages = msgs.parse(thread)
    found = await extract_step.extract(scope.deps, thread, messages, msgs.labelled(messages), scope.incoming.context)
    fixed = {"off_topic": OFF_TOPIC_REPLY, "not_thread": prompts.NOT_A_THREAD,
             "failed": prompts.MODEL_FAILED}.get(found.status)
    if fixed is not None or not found.facts:
        return fixed or prompts.NOTHING_BACKED
    return found, messages


async def _envelope_for(scope: _Scope, thread: str, style: st.Style) -> dict | str:
    extracted = await _extract(scope, thread)
    if isinstance(extracted, str):
        return extracted
    found, messages = extracted
    notes = [*found.notes(), *([FACTS_LEFT_OUT.format(n=found.dropped)] if found.dropped else [])]
    if len(msgs.own(messages)) == 1 and not messages[0].speaker:
        notes.append(UNATTRIBUTED)
    timeline = tl.describe(messages)
    composed = await compose(scope.deps, found.facts, thread, timeline, style, checks.is_resolved(messages),
                             scope.incoming.context)
    conflicts = [checks.conflict_line(pair) for pair in checks.conflicts(found.facts)]
    return envelope.build(composed, found.facts, timeline, conflicts, notes, found.partial, style.audience)


async def _followup(scope: _Scope) -> dict:
    plan, prior, deps = scope.plan, scope.prior, scope.deps
    if plan.route == "ask":
        return await _ask(scope)
    cut = followup.from_reply(prior.reply, plan.view) if plan.route == "view" and plan.view in CUT_VIEWS else None
    if cut:  # the ask / impact the user already saw: zero calls, and it cannot drift from the summary
        return {"reply": _with_notes(cut, plan.notes)}
    if not prior.thread:  # trimmed out of the history: only our last reply is left
        cut = followup.from_reply(prior.reply, plan.view) if plan.route == "view" else None
        return {"reply": _with_notes(cut or prompts.NEED_REPASTE, plan.notes)}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    if plan.route == "who":
        extracted = await _extract(scope, prior.thread)
        reply = extracted if isinstance(extracted, str) else followup.who_said_reply(plan.kinds, extracted[0].facts)
        return {"reply": _with_notes(reply, plan.notes)}
    base = st.parse(prior.instruction)
    style = st.parse(plan.instruction, base) if plan.route == "restyle" else base
    built = await _envelope_for(scope, prior.thread, style)
    if isinstance(built, str):
        return {"reply": _with_notes(built, plan.notes)}
    view = "default" if plan.route == "restyle" else plan.view
    return {"reply": _reply(built, scope, view, plan.notes), "data": built}


async def _ask(scope: _Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    material = scope.prior.thread or scope.prior.reply
    answer = await followup.ask(scope.deps, scope.incoming.text, material, scope.incoming.context,
                                had_history=scope.incoming.had_history)
    return {"reply": _with_notes(answer, scope.plan.notes)}


def _reply(env: dict, scope: _Scope, view: str, notes: tuple[str, ...]) -> str:
    view = "json" if scope.incoming.wants_json else view
    if view in ("default", "json"):  # json stays parseable: notes ride in the envelope
        return render.render(env, view, extra_notes=notes)
    return _with_notes(render.render(env, view), notes)


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)
