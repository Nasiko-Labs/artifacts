"""The account-news LangGraph: research a company, or answer a follow-up from the list it gave.

    START ─┬─ (a list was given in this context) → followup ─┬─ answered → END
           │                                                 └─ NEEDS_SEARCH ─┐
           └─ (no list yet) ─────────────────────────────────→ research ←─────┘ → END

State is checkpointed per A2A contextId (thread_id), so a follow-up sees the list from the turn
before. The graph is compiled per turn because its nodes close over that turn's dependencies
(LLM client, gateway, inbound traceparent); the checkpointer is shared.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import news
import prompts
from fence import FencedGateway
from nasiko_mcp import McpGatewayError, McpTool, NasikoMcpClient
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("account_news.graph")

RESEARCH_MAX_ITERATIONS = 6
_PAUSED = ("approval_pending", "authorization_required")


@dataclass(frozen=True)
class TurnDeps:
    llm: Any
    model: str
    gateway: NasikoMcpClient | None
    traceparent: str | None
    today: date


class NewsState(TypedDict, total=False):
    query: str
    items: list[dict]
    reply: str
    route: str


class _NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


class AccountNewsAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def answer(self, query: str, context_id: str, deps: TurnDeps) -> str:
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": context_id}}
        turn = {"query": query, "reply": "", "route": ""}
        # One checkpoint per turn, as in the template: fewer state calls, and a crash mid-turn
        # resumes from the previous turn rather than half-way through a tool call.
        final = await graph.ainvoke(turn, config, durability="exit")
        return final["reply"]


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def followup(state: NewsState) -> dict:
        return await _followup(state, deps)

    async def research(state: NewsState) -> dict:
        return await _research(state, deps)

    graph = StateGraph(NewsState)
    graph.add_node("followup", followup)
    graph.add_node("research", research)
    graph.add_conditional_edges(START, _entry, ["followup", "research"])
    graph.add_conditional_edges("followup", _after_followup, ["research", END])
    graph.add_edge("research", END)
    return graph


def _entry(state: NewsState) -> str:
    return "followup" if state.get("items") else "research"


def _after_followup(state: NewsState) -> str:
    return "research" if state.get("route") == "research" else END


# --- follow-up ----------------------------------------------------------------

async def _followup(state: NewsState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return _finish(state, prompts.NO_TRACE_CONTEXT)
    prior = news.items_from_state(state.get("items"))
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(news.render_items(prior))}"
    # Only the fenced list, never earlier replies: those carry web-derived text outside a fence.
    messages = ({"role": "system", "content": system}, {"role": "user", "content": state["query"]})
    result = await run_tool_loop(deps.llm, deps.model, messages, (), _NoTools(), deps.traceparent,
                                 max_iterations=1)
    text = result.text.strip()
    if text.startswith(prompts.NEEDS_SEARCH):
        return {"route": "research"}
    return _finish(state, text or prompts.UNREADABLE_ANSWER)


# --- research -----------------------------------------------------------------

async def _research(state: NewsState, deps: TurnDeps) -> dict:
    tools, failure = await _discover_tools(deps)
    if failure is not None:
        return _finish(state, failure)
    fenced = FencedGateway(deps.gateway)
    messages = (
        {"role": "system", "content": prompts.research_system(deps.today)},
        {"role": "user", "content": state["query"]},
    )
    result = await run_tool_loop(deps.llm, deps.model, messages, tools, fenced, deps.traceparent,
                                 max_iterations=RESEARCH_MAX_ITERATIONS)
    if result.status in _PAUSED:
        return _finish(state, prompts.APPROVAL_NOT_SUPPORTED)
    if result.status == "max_iterations":
        return _finish(state, result.text)
    parsed = news.parse_answer(result.text) or await _repair(state, deps, result, fenced.seen_text)
    return _finish_research(state, deps, parsed, fenced.seen_text)


async def _discover_tools(deps: TurnDeps) -> tuple[tuple[McpTool, ...], str | None]:
    """The gateway catalog for this request, or the fixed reply that ends the turn without an LLM call."""
    if deps.gateway is None:
        return (), prompts.NO_SEARCH_REFUSAL
    if not deps.traceparent:
        return (), prompts.NO_TRACE_CONTEXT
    try:
        tools = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return (), prompts.GATEWAY_UNREACHABLE
    if not tools:
        return (), prompts.NO_SEARCH_REFUSAL
    log.info("tools offered to the model: %s", [tool.name for tool in tools])
    return tools, None


async def _repair(state: NewsState, deps: TurnDeps, result: LoopResult, seen_text: str) -> news.ParsedAnswer | None:
    """One tool-less call that restates the answer as JSON from this turn's (fenced) tool output."""
    log.info("answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen_text) if seen_text else "(no tool results this turn)"
    messages = (
        {"role": "system", "content": prompts.research_system(deps.today)},
        {"role": "user", "content": state["query"]},
        {"role": "user", "content": (
            f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n{prompts.fence(result.text)}\n\n"
            f"{prompts.REPAIR_INSTRUCTION}"
        )},
    )
    repaired = await run_tool_loop(deps.llm, deps.model, messages, (), _NoTools(), deps.traceparent,
                                   max_iterations=1)
    return news.parse_answer(repaired.text)


def _finish_research(state: NewsState, deps: TurnDeps, parsed: news.ParsedAnswer | None, seen_text: str) -> dict:
    if parsed is None:
        return _finish(state, prompts.UNREADABLE_ANSWER)
    if parsed.status == "no_search_tool":
        return _finish(state, prompts.NO_SEARCH_REFUSAL)
    kept = news.post_check(parsed.items, today=deps.today, seen_text=seen_text)
    dropped = len(parsed.items) - len(kept)
    if dropped:
        log.info("post-check dropped %d item(s)", dropped)
    if not kept:  # keep the previous list, so a follow-up can still use it
        return _finish(state, prompts.NOTHING_FOUND)
    return _finish(state, news.render_items(kept), items=kept)


# --- shared -------------------------------------------------------------------

def _finish(state: NewsState, reply: str, items: Sequence[news.NewsItem] | None = None) -> dict:
    """The turn's state update: the reply and, after a search that found something, the new list.

    A refusal, an error or an empty search leaves the previous list in place."""
    update: dict = {"reply": reply, "route": ""}
    if items is not None:
        update["items"] = news.items_to_state(items)
    return update
