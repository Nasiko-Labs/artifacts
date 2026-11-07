"""The spend-spike LangGraph.

    START ─┬─ export in this message ──→ analyse (code) ─┬─ no spike / no trace context → END
           │                                             └─ spike → lookups (repos, deploys, owner) → draft → END
           │                                                         (repo pick pause when no name matched)
           ├─ an export is already in this context → followup ─┬─ "last N days" → re-run in code → END
           │                                                   ├─ answer / OFF_TOPIC → END
           │                                                   ├─ POST → post (target, then the approval walk)
           │                                                   ├─ REDRAFT → redraft → END
           │                                                   └─ NEEDS_REPOS / "repos: x" → repos → END
           └─ nothing yet → intake ─ cost words → paste instructions (no call) | else 1 call: NEEDS_EXPORT/OFF_TOPIC

A greeting never enters the graph (smalltalk.py). State is checkpointed per A2A contextId: only the export text,
the window, the budget and the checked findings are kept; the analysis is re-run from the export in code (ms).
The graph is compiled per turn because its nodes close over that turn's dependencies; the checkpointer is shared.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import drafting
import inputs
import lookups
import picker
import prompts
import render
import sender
import smalltalk
import walk
from analysis import Analysis, analyse
from deps import TurnDeps
from export import ParseError
from inputs import Incoming
from records import Findings
from steps import ask_once

log = logging.getLogger("spend_spike.graph")

_COST_WORDS = re.compile(r"\b(?:cost|costs|bill|billing|spend|spending|spike|budget|invoice|cloud|finops|"
                         r"export|csv)\b", re.IGNORECASE)
_RERUN_LEAD = "Re-ran the analysis over the last {n} days, in code, with the lookups from before."


class SpikeState(TypedDict, total=False):
    table: str                 # the export text last analysed in this context
    window_days: int | None
    budget: int | None
    findings: dict             # records.Findings of the last analysis
    query: str                 # this turn: the user's message
    instruction: str           # this turn: the words before the export
    new_table: str             # this turn: an export in this message
    notes: list[str]           # this turn: input notes (an unreadable file part)
    wants_json: bool
    route: str
    reply: str
    data: dict | None          # this turn: the envelope (completed analysis turns)
    pause: dict | None         # this turn: a picker question
    walk: dict | None          # this turn: the post to put on the approval card
    named_repos: list[str]


class SpikeAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> walk.Outcome:
        if not incoming.table and not incoming.notes:
            canned = smalltalk.reply_to(incoming.text)
            if canned is not None:  # a greeting: no tool call, no LLM call, state untouched
                return walk.Outcome("complete", canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": incoming.instruction if incoming.table else incoming.text, "instruction":
                 incoming.instruction, "new_table": incoming.table, "notes": list(incoming.notes),
                 "wants_json": incoming.wants_json, "route": "", "reply": "", "data": None, "pause": None,
                 "walk": None, "named_repos": []}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        return outcome_of(final)

    async def picked_repos(self, answer: str, offered: list[str], context_id: str, deps: TurnDeps) -> walk.Outcome:
        """The repo-pick answer: run deploys, owner and draft over the picked (or typed) repositories."""
        repos, _verified = picker.read_repo_answer(answer, offered)
        if not repos:
            return walk.Outcome("complete", "No repository picked, so deploy correlation was not checked.")
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        values = dict((await graph.aget_state(_config(context_id))).values)
        update = await _repos(values, deps, repos)
        await graph.aupdate_state(_config(context_id), {k: v for k, v in update.items()
                                                        if k in ("findings",)}, as_node="repos")
        return outcome_of(update)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def outcome_of(final: Mapping[str, Any]) -> walk.Outcome:
    reply = final.get("reply") or ""
    planned = final.get("walk")
    if planned:
        post = sender.Post.from_dict(planned.get("post"))
        if post is not None:
            return walk.start(post, planned.get("facts") or {}, reply)
    pause = final.get("pause")
    if pause:
        text = "\n\n".join(p for p in (reply, pause["text"]) if p)
        return walk.Outcome("input_required", text, pause["metadata"], pause["hitl"])
    return walk.Outcome("complete", reply or prompts.NO_RESULT_YET, data=final.get("data"))


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def intake(state: SpikeState) -> dict:
        return await _intake(state, deps)

    async def analyse_node(state: SpikeState) -> dict:
        return await _analyse(state, deps)

    async def followup(state: SpikeState) -> dict:
        return await _followup(state, deps)

    async def repos(state: SpikeState) -> dict:
        return await _repos(state, deps, state.get("named_repos") or ())

    async def post(state: SpikeState) -> dict:
        return await _post(state, deps)

    async def redraft(state: SpikeState) -> dict:
        return await _redraft(state, deps)

    graph = StateGraph(SpikeState)
    for name, node in (("intake", intake), ("analyse", analyse_node), ("followup", followup), ("repos", repos),
                       ("post", post), ("redraft", redraft)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["intake", "analyse", "followup"])
    graph.add_conditional_edges("followup", _route, ["repos", "post", "redraft", END])
    for terminal in ("intake", "analyse", "repos", "post", "redraft"):
        graph.add_edge(terminal, END)
    return graph


def _entry(state: SpikeState) -> str:
    if state.get("new_table"):
        return "analyse"
    return "followup" if state.get("table") else "intake"


def _route(state: SpikeState) -> str:
    return state.get("route") or END


# --- nodes ---------------------------------------------------------------------------------------------------

async def _intake(state: SpikeState, deps: TurnDeps) -> dict:
    notes = state.get("notes") or []
    if notes:
        return {"reply": "\n\n".join([*notes, prompts.PASTE_INSTRUCTIONS])}
    if _COST_WORDS.search(state.get("query") or ""):
        return {"reply": prompts.PASTE_INSTRUCTIONS}  # about cost, nothing pasted: a fixed reply, zero calls
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_FOLLOWUP}
    text = await ask_once(deps, ({"role": "system", "content": prompts.INTAKE_SYSTEM},
                                 {"role": "user", "content": prompts.fence(state.get("query") or "")}))
    if text.startswith(prompts.NEEDS_EXPORT):
        return {"reply": prompts.PASTE_INSTRUCTIONS}
    return {"reply": smalltalk.OFF_TOPIC_REPLY}


def _reanalyse(state: Mapping[str, Any], window: int | None = None) -> Analysis | ParseError:
    chosen = window if window is not None else state.get("window_days")
    return analyse(state.get("table") or "", chosen, state.get("budget"))


async def _analyse(state: SpikeState, deps: TurnDeps) -> dict:
    h = inputs.hints(state.get("instruction") or "")
    table = state["new_table"]
    a = analyse(table, h.window_days, h.budget_micros)
    if isinstance(a, ParseError):
        return {"reply": a.message}
    kept = {"table": table, "window_days": h.window_days, "budget": h.budget_micros}
    if a.spike is None:
        return {**kept, **_shown(a, Findings(), state)}
    if not deps.traceparent:
        return {**kept, **_shown(a, Findings(notes=(prompts.NO_TRACE_CONTEXT,)), state)}
    looked = await lookups.run(deps, a, h.repos)
    shown = _shown(a, looked.findings, state)
    if looked.pause is None:
        return {**kept, **shown}
    pause = looked.pause
    return {**kept, **shown, "data": None,
            "pause": {"text": pause.text, "hitl": pause.hitl, "metadata": pause.metadata}}


def _shown(a: Analysis, f: Findings, state: Mapping[str, Any], lead: str = "") -> dict:
    text = render.markdown(a, f, tuple(state.get("notes") or ()))
    summary = text.split("\n", 1)[0]
    env = render.envelope(a, f, summary)
    body = render.as_json(env) if state.get("wants_json") else text
    return {"findings": f.to_dict(), "reply": "\n\n".join(p for p in (lead, body) if p), "data": env}


async def _followup(state: SpikeState, deps: TurnDeps) -> dict:
    query = state.get("query") or ""
    window = inputs.window_days(query)
    findings = Findings.from_dict(state.get("findings"))
    if window is not None:  # "only look at the last 3 days": a re-run in code, no call
        a = _reanalyse(state, window)
        if isinstance(a, ParseError):
            return {"reply": a.message}
        f = replace(findings, draft="")
        return {"window_days": window, **_shown(a, f, state, _RERUN_LEAD.format(n=a.window_days))}
    named = inputs.repos(query)
    if named:
        return {"route": "repos", "named_repos": list(named)}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_FOLLOWUP}
    a = _reanalyse(state)
    if isinstance(a, ParseError):
        return {"reply": a.message}
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(render.markdown(a, findings))}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query}))
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if text.startswith(prompts.POST):
        return {"route": "post"}
    if text.startswith(prompts.REDRAFT):
        return {"route": "redraft"}
    if text.startswith(prompts.NEEDS_REPOS):
        names = inputs.repos("repos: " + text[len(prompts.NEEDS_REPOS):].lstrip(":"))
        return {"route": "repos", "named_repos": list(names)} if names else {"reply": prompts.PASTE_REPOS}
    return {"reply": text or prompts.UNREADABLE_ANSWER}


async def _repos(state: Mapping[str, Any], deps: TurnDeps, repos) -> dict:
    a = _reanalyse(state)
    if isinstance(a, ParseError):
        return {"reply": a.message}
    if a.spike is None:
        return {"reply": prompts.NO_SPIKE_TO_POST}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_FOLLOWUP}
    f = await lookups.with_repos(deps, a, tuple(repos), Findings.from_dict(state.get("findings")))
    return _shown(a, f, state)


async def _redraft(state: SpikeState, deps: TurnDeps) -> dict:
    a = _reanalyse(state)
    if isinstance(a, ParseError) or a.spike is None:
        return {"reply": prompts.NO_SPIKE_TO_POST}
    f = Findings.from_dict(state.get("findings"))
    body = await drafting.draft(deps, render.facts(a, f), state.get("query") or "")
    f = replace(f, draft=body)
    return {"findings": f.to_dict(), "reply": render.draft_reply(body)}


async def _post(state: SpikeState, deps: TurnDeps) -> dict:
    a = _reanalyse(state)
    if isinstance(a, ParseError) or a.spike is None:
        return {"reply": prompts.NO_SPIKE_TO_POST}
    f = Findings.from_dict(state.get("findings"))
    facts = render.facts(a, f)
    body = f.draft or await drafting.draft(deps, facts)
    f = replace(f, draft=body)
    kept = {"findings": f.to_dict()}
    options = picker.targets(f)
    target = picker.named_target(state.get("query") or "", f, options)
    if target is None:
        if not options:
            return {**kept, "reply": render.copyable("[owner]", body, prompts.NO_TARGET)}
        pause = picker.target_question(options, body, facts)
        return {**kept, "pause": {"text": pause.text, "hitl": pause.hitl, "metadata": pause.metadata}}
    return {**kept, **(await plan_post(deps, target, body, facts))}


async def plan_post(deps: TurnDeps, target: picker.Target, body: str, facts: Mapping[str, Any]) -> dict:
    """{"walk": ...} when a post tool exists, else the copyable message."""
    tool, why = await sender.find_send_tool(deps)
    if tool is None:
        log.info("no post tool: %s", why)
        return {"reply": render.copyable(target.label, body, prompts.no_send_tool(why))}
    post = sender.Post(target=target.label, recipient=target.recipient, body=body, tool=tool.name)
    return {"walk": {"post": post.to_dict(), "facts": dict(facts)}}
