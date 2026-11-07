"""The read steps of turn 1 (find, find_docs, extract, status, repeats): each a bounded step whose
answer is checked in code against that step's own tool output."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import step_prompts
from corpus import change_links_for, comments_for, norm
from dates import Window, parse_date
from deps import TurnDeps
from docs_extract import (
    DOCS_PER_CALL,
    MAX_DOCS,
    Doc,
    action_section,
    check_extracted,
    content_of,
    is_postmortem_title,
)
from nasiko_mcp import McpTool
from prompts import fence_data
from records import (
    ActionItem,
    Incident,
    Postmortem,
    TicketStatus,
    check_incidents,
    check_items,
    check_postmortems,
    check_statuses,
    load_json_object,
    text_of,
)
from steps import ask_once, run_step
from tool_health import ToolHealth

log = logging.getLogger("postmortem.reads")


@dataclass(frozen=True)
class Found:
    postmortems: tuple[Postmortem, ...] = ()
    items: tuple[ActionItem, ...] = ()
    dropped: int = 0
    capped: tuple[bool, bool] = (False, False)
    hit_limit: bool = False
    off_topic: bool = False
    paused: bool = False


async def find(deps: TurnDeps, tools: Sequence[McpTool], health: ToolHealth, window: Window, query: str,
               filter_text: str | None, budget: float) -> Found:
    result = await run_step(deps, tools, health, step_prompts.find_system(window, filter_text), query,
                            deps.limits.find_calls, budget)
    if result.paused:
        return Found(paused=True)
    data = result.data or {}
    if data.get("status") == "off_topic":
        return Found(off_topic=True)
    pms = check_postmortems(data.get("postmortems"), result.corpus, window.start, "ticket")
    items = check_items(data.get("items"), result.corpus, {p.id for p in pms.kept})
    return Found(pms.kept, items.kept, pms.dropped + items.dropped, (pms.capped, items.capped), result.hit_limit)


@dataclass(frozen=True)
class DocsFound:
    docs: tuple[Doc, ...] = ()
    postmortems: tuple[Postmortem, ...] = ()
    unreadable: int = 0
    hit_limit: bool = False


async def find_docs(deps: TurnDeps, tools: Sequence[McpTool], health: ToolHealth, window: Window,
                    budget: float) -> DocsFound:
    result = await run_step(deps, tools, health, step_prompts.find_docs_system(window),
                            "Find the postmortem documents.", deps.limits.docs_calls, budget)
    rows = (result.data or {}).get("docs")
    docs, unreadable = [], 0
    for row in (rows if isinstance(rows, list) else [])[:MAX_DOCS]:
        if not isinstance(row, dict):
            continue
        doc_id, title, created = text_of(row.get("id")), text_of(row.get("title")), text_of(row.get("created_at"))
        day = parse_date(created)
        if not (doc_id and result.corpus.contains(doc_id) and result.corpus.contains(title)
                and is_postmortem_title(title) and result.corpus.contains(created) and day and day >= window.start):
            continue
        content = content_of(doc_id, result.fenced.records)
        if content is None:
            unreadable += 1
            continue
        url = text_of(row.get("url"))
        docs.append(Doc(doc_id, title, url if url and result.corpus.contains(url) else None, created,
                        action_section(content)))
    pms = tuple(Postmortem(id=f"doc:{d.id}", kind="doc", key_or_url=d.url or d.id, title=d.title,
                           created_at=d.created_at) for d in docs)
    return DocsFound(tuple(docs), pms, unreadable, result.hit_limit)


async def extract(deps: TurnDeps, docs: Sequence[Doc], budget: float) -> tuple[list[ActionItem], int]:
    """Tool-less: the model lists the items in each fenced section; code keeps only verbatim quotes."""
    if not docs:
        return [], 0
    batches = [docs[i:i + DOCS_PER_CALL] for i in range(0, len(docs), DOCS_PER_CALL)][:deps.limits.extract_calls]
    try:
        answers = await asyncio.wait_for(asyncio.gather(*(_extract_batch(deps, b) for b in batches)), budget)
    except TimeoutError:
        log.info("extract timed out; using the ticket references code found")
        answers = [[] for _ in batches]
    raw = [row for answer in answers for row in answer]
    return check_extracted(raw, docs, {d.id: f"doc:{d.id}" for d in docs})


async def _extract_batch(deps: TurnDeps, docs: Sequence[Doc]) -> list:
    sections = "\n\n".join(fence_data(f"doc_id: {d.id}\ntitle: {d.title}\n\n{d.section}") for d in docs)
    text = await ask_once(deps, ({"role": "system", "content": step_prompts.extract_system()},
                                 {"role": "user", "content": sections}))
    items = (load_json_object(text) or {}).get("items")
    return items if isinstance(items, list) else []


@dataclass(frozen=True)
class Statuses:
    by_key: dict[str, TicketStatus] = field(default_factory=dict)
    comments: dict = field(default_factory=dict)   # key → (Comment, ...)
    change_links: dict = field(default_factory=dict)  # key → bool
    dropped: int = 0
    hit_limit: bool = False


async def status(deps: TurnDeps, tools: Sequence[McpTool], health: ToolHealth, window: Window,
                 keys: Sequence[str], budget: float) -> Statuses:
    if not keys:
        return Statuses()
    result = await run_step(deps, tools, health, step_prompts.status_system(window, keys),
                            "Fetch the current state of the listed tickets.", deps.limits.status_calls, budget)
    checked = check_statuses((result.data or {}).get("tickets"), result.corpus, set(keys))
    by_key = {s.key: s for s in checked.kept}
    records = result.fenced.records
    known = [k for k in keys if k in by_key]  # comments of every ticket: they count as activity too
    comments = {k: comments_for(k, records, keys) for k in known}
    links = {k: change_links_for(k, records, keys) for k in known}
    return Statuses(by_key, comments, links, checked.dropped, result.hit_limit)


@dataclass(frozen=True)
class Incidents:
    incidents: tuple[Incident, ...] = ()
    named: Incident | None = None
    hit_limit: bool = False


async def repeats(deps: TurnDeps, tools: Sequence[McpTool], health: ToolHealth, window: Window, since: str,
                  incident_ref: str | None, budget: float) -> Incidents:
    if not tools:
        return Incidents()
    result = await run_step(deps, tools, health, step_prompts.repeats_system(window, since, incident_ref),
                            "List the incidents.", deps.limits.repeats_calls, budget)
    checked = check_incidents((result.data or {}).get("incidents"), result.corpus)
    named = next((i for i in checked.kept if incident_ref and norm(i.id) == norm(incident_ref)), None)
    return Incidents(checked.kept, named, result.hit_limit)
