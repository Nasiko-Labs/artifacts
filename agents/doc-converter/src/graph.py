"""The doc-converter LangGraph.

    START ─┬─ (paging follow-up) → page → END
           ├─ (resume, file known) ───────────────→ fetch → convert → render → END
           ├─ (resume, place known) → locate ─────↗
           └─ intake ─┬─ fixed reply / question → END
                      ├─ content only ─────────────────→ convert
                      └─ named files → locate ─┬─ Q2 → END
                                               └─ fetch → convert → render → END

A greeting never enters the graph (smalltalk.py). State is checkpointed per A2A contextId: only the
last conversion's rendered parts and warnings (capped), so "part 2" and "show warnings" cost no call.
Documents, bytes and the envelope live on the per-turn Scope, never in the checkpoint. The graph is
compiled per turn because its nodes close over that turn's Scope; the checkpointer is shared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import flow
import prompts
import smalltalk
from convert import Converter
from deps import TurnDeps
from flow import Scope
from hitl import Pause
from inputs import Incoming

STORE_MAX_CHARS = 600_000  # the platform's state API takes at most 1 MB per value
_PART = re.compile(r"^(?:show |give me |send )?(?:the )?(?:part|page)\s*#?\s*(\d{1,3})\.?$", re.IGNORECASE)
_NEXT = re.compile(r"^(?:next|next part|next page|more|continue|keep going|the rest)\.?$", re.IGNORECASE)
_WARNINGS = re.compile(r"^(?:show |list )?(?:the )?warnings\??$", re.IGNORECASE)


class ConverterState(TypedDict, total=False):
    route: str
    reply: str
    stored_pages: list[str]
    stored_warnings: list[str]
    shown: int


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None
    pause: Pause | None = None


class ConverterAgent:
    def __init__(self, checkpointer: Any, converter: Converter | None = None):
        self._checkpointer = checkpointer
        self._converter = converter

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        if not incoming.has_content and not incoming.named:
            canned = smalltalk.reply_to(incoming.request)
            if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
                return TurnResult(canned)
        scope = Scope(deps=deps, incoming=incoming)
        entry = "page" if _is_followup(incoming) else "intake"
        return await self._run(scope, entry, context_id)

    async def resume(self, scope: Scope, entry: str, context_id: str) -> TurnResult:
        return await self._run(scope, entry, context_id)

    async def _run(self, scope: Scope, entry: str, context_id: str) -> TurnResult:
        graph = _build(scope, self._converter or scope.deps.converter or Converter()).compile(
            checkpointer=self._checkpointer)
        final = await graph.ainvoke({"route": entry, "reply": ""}, _config(context_id), durability="exit")
        if scope.pause is not None:
            return TurnResult("", pause=scope.pause)
        return TurnResult(final.get("reply") or scope.reply, scope.data)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _is_followup(incoming: Incoming) -> bool:
    text = incoming.request.strip()
    return not incoming.has_content and bool(_PART.match(text) or _NEXT.match(text) or _WARNINGS.match(text))


def _build(scope: Scope, converter: Converter) -> StateGraph:
    async def intake(state: ConverterState) -> dict:
        return {"route": await flow.intake(scope), "reply": scope.reply}

    async def locate(state: ConverterState) -> dict:
        return {"route": await flow.locate(scope)}

    async def fetch(state: ConverterState) -> dict:
        return {"route": await flow.fetch(scope)}

    async def convert(state: ConverterState) -> dict:
        return {"route": await flow.convert(scope, converter)}

    async def render(state: ConverterState) -> dict:
        flow.render(scope)
        return {"route": "", "reply": scope.reply, **_stored(scope)}

    async def page(state: ConverterState) -> dict:
        return {"route": "", **_page(state, scope.incoming.request)}

    graph = StateGraph(ConverterState)
    for name, node in (("intake", intake), ("locate", locate), ("fetch", fetch), ("convert", convert),
                       ("render", render), ("page", page)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _route, ["intake", "locate", "fetch", "page"])
    graph.add_conditional_edges("intake", _route, ["locate", "convert", END])
    graph.add_conditional_edges("locate", _route, ["fetch", "convert", END])
    graph.add_edge("fetch", "convert")
    graph.add_edge("convert", "render")
    graph.add_edge("render", END)
    graph.add_edge("page", END)
    return graph


def _route(state: ConverterState) -> str:
    return state.get("route") or END


def _stored(scope: Scope) -> dict:
    kept: list[str] = []
    size = 0
    for page in scope.pages:
        size += len(page)
        if size > STORE_MAX_CHARS:
            break
        kept.append(page)
    return {"stored_pages": kept, "stored_warnings": list(scope.warnings), "shown": 1}


def _page(state: ConverterState, text: str) -> dict:
    pages = state.get("stored_pages") or []
    if not pages:
        return {"reply": prompts.NO_STORED}
    if _WARNINGS.match(text.strip()):
        warnings = state.get("stored_warnings") or []
        body = "\n".join(f"- {w}" for w in warnings) if warnings else "The last conversion had no warnings."
        return {"reply": body}
    match = _PART.match(text.strip())
    wanted = int(match.group(1)) if match else (state.get("shown") or 1) + 1
    if wanted < 1 or wanted > len(pages):
        return {"reply": f"The last conversion has {len(pages)} part{'s' if len(pages) != 1 else ''} kept here; "
                         f"there is no part {wanted}."}
    return {"reply": pages[wanted - 1], "shown": wanted}
