"""The adhoc-request-deflector LangGraph.

    START → turn → END

One node; LangGraph is here for the checkpointer. State per A2A contextId (thread_id): the dashboard
index last read (`index`), the list last shown (`items`), the scan's requests not yet matched
(`pending`) and the scan's channel (`scan`), so "continue" and "post #2" are answered from it. Prior
state never comes from the chat-history prefix.

Before the graph, in code: a greeting gets the canned intro with zero tool and LLM calls. Inside the
turn: no traceparent → fixed reply before any call; arithmetic → fixed decline. The model never
answers the data question: it extracts terms, picks a row and words a reply, and code checks each.
Drafts for the approve → post walk and the index-choice question leave the graph in the turn result.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import coverage
import index as index_
import intent as intent_
import parse
import pipeline
import prompts
import render
import smalltalk
import sources
import toolargs
from deps import TurnDeps
from discovery import Catalog, Step, discover
from models import Index, Item, Request
from nasiko_hitl import question_metadata
from nasiko_mcp import McpTool
from walk import Draft

log = logging.getLogger("deflector.graph")

PICK_KEY = "deflector_index_pick"
PICK_HEADER = "Which dashboard index?"


class DeflectorState(TypedDict, total=False):
    query: str
    as_json: bool
    sheet: list | None
    reply: str
    data: dict | None
    drafts: list
    pick: dict | None
    index: dict
    items: list
    pending: list
    scan: dict


@dataclass(frozen=True)
class Pick:
    text: str
    hitl: dict
    metadata: dict


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None
    drafts: tuple[Draft, ...] = ()
    pick: Pick | None = None


class DeflectorAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, as_json: bool = False,
                   sheet: tuple[str, str] | None = None) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "as_json": as_json, "sheet": list(sheet) if sheet else None, "reply": "",
                 "data": None, "drafts": [], "pick": None}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        drafts = tuple(Draft.from_dict(d) for d in final.get("drafts") or ())
        pick = final.get("pick")
        return TurnResult(final.get("reply") or prompts.UNREADABLE_ANSWER, final.get("data"), drafts,
                          Pick(**pick) if pick else None)

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember what happened to each reply, so a later "post #n" neither reposts nor loses it."""
        if not outcomes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        values = dict((await graph.aget_state(_config(context_id))).values)
        items = [dict(i) for i in values.get("items") or []]
        for number, status in outcomes.items():
            n = int(number)
            if 1 <= n <= len(items):
                items[n - 1] = {**items[n - 1], "state": _STATES.get(status, "copyable")}
        await graph.aupdate_state(_config(context_id), {"items": items, "drafts": []}, as_node="turn")


_STATES = {"posted": "posted", "skipped": "skipped", "denied": "skipped"}


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps | None) -> StateGraph:
    async def turn(state: DeflectorState) -> dict:
        return await _turn(state, deps)

    graph = StateGraph(DeflectorState)
    graph.add_node("turn", turn)
    graph.add_edge(START, "turn")
    graph.add_edge("turn", END)
    return graph


def _reply(text: str) -> dict:
    return {"reply": text, "data": None}


async def _turn(state: DeflectorState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return _reply(prompts.NO_TRACE_CONTEXT)
    request_text, pasted = index_.split_paste(state["query"])
    parsed = parse.parse(request_text, deps.now.date(), bool(state.get("as_json")))
    sheet = tuple(state["sheet"]) if state.get("sheet") else None
    if parsed.mode == "continue":
        return await _continue(state, deps, parsed)
    if parsed.mode == "post":
        return await _post(state, deps, parsed)
    if parsed.mode == "scan":
        return await _scan(state, deps, parsed, pasted, sheet)
    return await _single(state, deps, parsed, pasted, sheet)


# --- the index -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _IndexOutcome:
    index: Index | None
    notes: tuple[str, ...] = ()
    stop: dict | None = None  # a reply or a pick that ends the turn


async def _index(state: DeflectorState, deps: TurnDeps, parsed: parse.Parsed, pasted: Index | None,
                 sheet: tuple[str, str] | None, reader: sources.Reader | None) -> _IndexOutcome:
    if pasted is not None:
        return _usable(pasted, ())
    stored = pipeline.index_from_state(state.get("index"))
    if stored is not None and not parsed.index_title and sheet is None:
        return _IndexOutcome(stored)
    if reader is None:
        return _IndexOutcome(None, stop=_reply(prompts.NO_INDEX))
    read = await sources.read_index(reader, parsed.index_title, sheet)
    if read.choices:
        return _IndexOutcome(None, stop={"reply": "", "data": None, "pick": _pick(state, read.choices)})
    if read.index is None:
        return _IndexOutcome(None, stop=_reply("\n\n".join([prompts.NO_INDEX, *(f"_Note: {n}_" for n in read.notes)])))
    return _usable(read.index, read.notes)


def _usable(index: Index, notes: Sequence[str]) -> _IndexOutcome:
    if not index.rows:
        return _IndexOutcome(None, stop=_reply(prompts.EMPTY_INDEX))
    if index.dropped:
        notes = (*notes, (f"{index.dropped} index row{'s' if index.dropped != 1 else ''} without a name or an "
                          "http(s) link left out."))
    if index.source == "paste" and len(index.rows) < pipeline.MIN_RELIABLE_ROWS:
        notes = (*notes, f"Matched against a pasted list of {len(index.rows)} dashboards, so confidence is low.")
    return _IndexOutcome(index, tuple(notes))


def _pick(state: DeflectorState, choices: Sequence[tuple[str, str]]) -> dict:
    labels: dict[str, list[str]] = {}
    for sid, title in choices:
        label = title[:150] if title[:150] not in labels else f"{title[:140]} ({len(labels) + 1})"
        labels[label] = [sid, title]
    hitl = question_metadata(PICK_HEADER, [(label, "") for label in labels], multi_select=False,
                             allow_custom_input=False)
    text = f"I found {len(labels)} spreadsheets that look like a dashboard index. {PICK_HEADER}"
    meta = {PICK_KEY: {"query": state["query"], "as_json": bool(state.get("as_json")), "sheets": labels}}
    return {"text": text, "hitl": hitl, "metadata": meta}


async def _reader(deps: TurnDeps, steps: Sequence[Step]) -> tuple[sources.Reader | None, Catalog | None, str | None]:
    if deps.gateway is None:
        return None, None, None
    catalog, failure = await discover(deps, steps)
    if failure is not None:
        return None, None, failure
    return sources.Reader(deps, catalog), catalog, None


# --- one request -----------------------------------------------------------------------------------

async def _single(state: DeflectorState, deps: TurnDeps, parsed: parse.Parsed, pasted: Index | None,
                  sheet: tuple[str, str] | None) -> dict:
    if not parsed.text:
        return _reply(prompts.NOT_A_REQUEST if pasted is None else "Tell me the request to match against that list.")
    if intent_.is_arithmetic(parsed.text):
        return _reply(smalltalk.OFF_TOPIC_REPLY)
    found = await intent_.read_intents(deps, [("M1", parsed.text)])
    if found.status == "off_topic":
        return _reply(smalltalk.OFF_TOPIC_REPLY)
    if found.status != "ok":
        return _reply(prompts.UNREADABLE_ANSWER)
    intent = found.intents.get("M1")
    if intent is None or not intent.is_data_request:
        return _reply(prompts.NOT_A_REQUEST)
    reader = None
    if pasted is None and (not state.get("index") or parsed.index_title or sheet):
        reader, _, failure = await _reader(deps, ("index",))
        if failure is not None:
            return _reply(failure)
    got = await _index(state, deps, parsed, pasted, sheet, reader)
    if got.stop is not None:
        return got.stop
    item = await pipeline.match_request(deps, Request(text=parsed.text), intent, got.index, can_post=False,
                                        dropped=found.dropped_terms)
    env = pipeline.envelope([item], got.index, got.notes)
    text = render.as_json(env) if parsed.as_json else render.single(env, parsed.draft_only)
    return {"reply": text, "data": env, "items": [item.model_dump()], "pending": [], "scan": {},
            "index": pipeline.index_to_state(got.index)}


# --- a channel scan --------------------------------------------------------------------------------

async def _scan(state: DeflectorState, deps: TurnDeps, parsed: parse.Parsed, pasted: Index | None,
                sheet: tuple[str, str] | None) -> dict:
    reader, catalog, failure = await _reader(deps, ("index", "requests", "reply"))
    if failure is not None:
        return _reply(failure)
    if reader is None:
        return _reply(prompts.NO_CHAT_FOR_SCAN)
    got = await _index(state, deps, parsed, pasted, sheet, reader)
    if got.stop is not None:
        return got.stop
    chan = await sources.read_channel(reader, parsed.channel or "", parsed.days)
    if not chan.available:
        return _reply("\n\n".join([prompts.NO_CHAT_FOR_SCAN, *(f"_Note: {n}_" for n in chan.notes)]))
    candidates = [m for m in chan.messages if pipeline.looks_like_request(m.text)]
    requests = [Request(text=m.text, link=m.permalink or None, requester=m.author or None, channel_id=chan.channel_id,
                        channel_label=chan.label, thread_ts=m.ts) for m in candidates]
    scan = {"label": chan.label, "channel_id": chan.channel_id, "days": parsed.days,
            "skipped": len(chan.messages) - len(candidates)}
    return await _process(deps, requests, got.index, scan, [*got.notes, *chan.notes], catalog.role("post"), parsed)


async def _continue(state: DeflectorState, deps: TurnDeps, parsed: parse.Parsed) -> dict:
    pending = [Request(**r) for r in state.get("pending") or []]
    index = pipeline.index_from_state(state.get("index"))
    if not pending or index is None:
        return _reply(prompts.NOTHING_PENDING)
    _, catalog, failure = await _reader(deps, ("reply",))
    if failure is not None:
        return _reply(failure)
    scan = {**(state.get("scan") or {}), "skipped": 0}
    return await _process(deps, pending, index, scan, [], catalog.role("post") if catalog else None, parsed)


async def _process(deps: TurnDeps, requests: Sequence[Request], index: Index, scan: dict, notes: Sequence[str],
                   post_tool: McpTool | None, parsed: parse.Parsed) -> dict:
    batch, rest = list(requests[:pipeline.MAX_PER_TURN]), list(requests[pipeline.MAX_PER_TURN:])
    notes = list(notes)
    found = await intent_.read_intents(deps, [(f"M{n}", r.text) for n, r in enumerate(batch, 1)]) if batch else None
    if found is not None and found.status != "ok":
        notes.append("I couldn't read which messages are data requests this time; ask me to check again.")
    can_post = post_tool is not None and not parsed.draft_only
    items: list[Item] = []
    skipped = int(scan.get("skipped") or 0)
    for n, request in enumerate(batch, 1):
        intent = found.intents.get(f"M{n}") if found else None
        if intent is None or not intent.is_data_request:
            skipped += 1
            continue
        if deps.past_deadline():
            rest = [*batch[n - 1:], *rest]
            notes.append("I stopped at my time limit; the rest are waiting.")
            break
        postable = can_post and toolargs.post_args(post_tool, request.channel_id, request.thread_ts, "x") is not None
        items.append(await pipeline.match_request(deps, request, intent, index, can_post=postable,
                                                  dropped=found.dropped_terms if found else 0))
    if items and post_tool is None and not parsed.draft_only:
        notes.append(coverage.missing("post") + " The replies are here to copy.")
    env = pipeline.envelope(items, index, notes)
    drafts = [_draft(n, item, scan, post_tool) for n, item in enumerate(items, 1) if item.state == "draft"]
    text = render.as_json(env) if parsed.as_json else render.scan(env, scan.get("label", ""), int(scan.get("days") or 0),
                                                                  skipped, len(rest), bool(drafts))
    return {"reply": text, "data": env, "items": [i.model_dump() for i in items],
            "pending": [asdict(r) for r in rest], "scan": scan, "index": pipeline.index_to_state(index),
            "drafts": [d.to_dict() for d in drafts]}


def _draft(number: int, item: Item, scan: Mapping, tool: McpTool) -> Draft:
    return Draft(number=number, requester=item.requester or "", channel_id=item.channel_id,
                 channel_label=str(scan.get("label") or item.channel_id), thread_ts=item.thread_ts,
                 request_link=item.request_link or "", body=item.reply, tool=tool.name)


# --- "post #n" -------------------------------------------------------------------------------------

async def _post(state: DeflectorState, deps: TurnDeps, parsed: parse.Parsed) -> dict:
    items = [Item(**i) for i in state.get("items") or []]
    if not items:
        return _reply(prompts.NO_PRIOR_LIST)
    number = len(items) if parsed.post_number == -1 else parsed.post_number or _first_open(items)
    if not 1 <= number <= len(items):
        return _reply(f"There is no reply #{number} in the last list. Pick a number from it.")
    item = items[number - 1]
    if item.state == "posted":
        return _reply(f"#{number} was already posted, so I won't post it again.")
    if not item.thread_ts:
        return _reply(render.copyable(number, "There is no chat thread to post this in.", item.reply))
    _, catalog, failure = await _reader(deps, ("reply",))
    tool = catalog.role("post") if catalog else None
    if tool is None:
        return _reply(render.copyable(number, failure or coverage.missing("post"), item.reply))
    draft = _draft(number, item, state.get("scan") or {}, tool)
    return {"reply": "", "data": None, "drafts": [draft.to_dict()]}


def _first_open(items: Sequence[Item]) -> int:
    return next((n for n, i in enumerate(items, 1) if i.state in ("draft", "skipped")), 1)
