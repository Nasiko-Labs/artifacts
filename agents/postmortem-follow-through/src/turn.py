"""Turn 1, step by step: gather (find ∥ find_docs) → extract → status → repeats → report.

Each function is one graph node. What later turns need (the numbered rows, the postmortems, the open
items per postmortem, the window) goes into graph state; what only this turn needs (the catalog, tool
health, the deadline, comment evidence) stays on `Scope` and is never persisted."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import coverage
import prompts
import reads
import smalltalk
import table_render
from assess import Evidence, assess, open_by_postmortem
from dates import Window, parse_window
from deps import TurnDeps
from discovery import TURN_STEPS, Catalog, discover
from docs_extract import Doc
from intake import intake
from records import MAX_ITEMS, ActionItem, Incident, Postmortem, from_state, to_state
from repeats import best_matches, repeats_by_postmortem
from steps import Deadline
from tool_health import ToolHealth

log = logging.getLogger("postmortem.turn")


@dataclass
class Scope:
    """One turn's read side; never persisted."""

    deps: TurnDeps
    deadline: Deadline
    catalog: Catalog | None = None
    health: ToolHealth | None = None
    docs: tuple[Doc, ...] = ()
    statuses: reads.Statuses = field(default_factory=reads.Statuses)
    incidents: reads.Incidents = field(default_factory=reads.Incidents)


def window_of(state: dict, today) -> Window:
    return parse_window(state.get("query") or "", today)


async def intake_node(state: dict, scope: Scope) -> dict:
    window = window_of(state, scope.deps.today)
    intent = await intake(scope.deps, state["query"], window)
    if intent.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    return {"filter": intent.filter, "match_ref": intent.match_ref, "match_text": intent.match_text,
            "notes": [], "route": "gather"}


async def gather(state: dict, scope: Scope) -> dict:
    deps, window = scope.deps, window_of(state, scope.deps.today)
    catalog, failure = await discover(deps, TURN_STEPS)
    if failure is not None:
        return {"reply": failure, "route": ""}
    tracker = catalog.tools_for("find")
    if not tracker:
        return {"reply": prompts.NO_TRACKER, "route": ""}
    scope.catalog, scope.health = catalog, ToolHealth(deps.gateway)
    budget = scope.deadline.budget(deps.limits.gather_secs)
    has_docs = catalog.has_source("find_docs")
    found, docs = await asyncio.gather(
        reads.find(deps, tracker, scope.health, window, state["query"], state.get("filter"), budget),
        reads.find_docs(deps, catalog.tools_for("find_docs"), scope.health, window, budget) if has_docs
        else _no_docs())
    if found.off_topic:
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if found.paused:
        return {"reply": prompts.READ_NEEDS_APPROVAL, "route": ""}
    notes = [*([] if has_docs else [coverage.NO_DOCS]), *coverage.cap_notes(*found.capped),
             *coverage.unreadable_docs_note(docs.unreadable)]
    notes += [prompts.step_limit_note(s) for s, hit in (("find", found.hit_limit), ("documents", docs.hit_limit)) if hit]
    pms = (*found.postmortems, *docs.postmortems)
    if not pms:
        return {"reply": "\n\n".join([prompts.nothing_found(window.start.isoformat()), *notes]), "route": ""}
    scope.docs = docs.docs
    return {"pms": to_state(pms), "items": to_state(found.items), "dropped": found.dropped, "notes": notes,
            "months": window.months, "route": "extract"}


async def _no_docs() -> reads.DocsFound:
    return reads.DocsFound()


async def extract_node(state: dict, scope: Scope) -> dict:
    budget = scope.deadline.budget(scope.deps.limits.extract_secs)
    doc_items, dropped = await reads.extract(scope.deps, scope.docs, budget)
    items = list(from_state(ActionItem, state.get("items")))
    have = {i.ticket_key for i in items if i.ticket_key}
    merged = items + [i for i in doc_items if not i.ticket_key or i.ticket_key not in have]
    return {"items": to_state(merged[:MAX_ITEMS]), "dropped": (state.get("dropped") or 0) + dropped}


async def status_node(state: dict, scope: Scope) -> dict:
    deps = scope.deps
    items = from_state(ActionItem, state.get("items"))
    keys = list(dict.fromkeys(i.ticket_key for i in items if i.ticket_key))
    scope.statuses = await reads.status(deps, scope.catalog.tools_for("status"), scope.health,
                                        window_of(state, deps.today), keys,
                                        scope.deadline.budget(deps.limits.status_secs))
    notes = [*state.get("notes", []), *([prompts.step_limit_note("status")] if scope.statuses.hit_limit else [])]
    return {"notes": notes, "dropped": (state.get("dropped") or 0) + scope.statuses.dropped}


async def repeats_node(state: dict, scope: Scope) -> dict:
    deps, catalog = scope.deps, scope.catalog
    pms = from_state(Postmortem, state.get("pms"))
    alerting = catalog.has_source("repeats")
    tools = catalog.tools_for("repeats") if alerting else catalog.tools_for("find")
    since = min(p.created for p in pms).isoformat() if pms else window_of(state, deps.today).start.isoformat()
    scope.incidents = await reads.repeats(deps, tools, scope.health, window_of(state, deps.today), since,
                                          state.get("match_ref"), scope.deadline.budget(deps.limits.repeats_secs))
    notes = [*state.get("notes", []), *([] if alerting else [coverage.NO_ALERTING])]
    notes += [prompts.step_limit_note("incidents")] if scope.incidents.hit_limit else []
    return {"notes": notes, "incidents": to_state(scope.incidents.incidents)}


def report(state: dict, scope: Scope) -> dict:
    deps = scope.deps
    window = window_of(state, deps.today)
    pms = from_state(Postmortem, state.get("pms"))
    items = from_state(ActionItem, state.get("items"))
    st = scope.statuses
    evidence = {k: Evidence(st.comments.get(k, ()), bool(st.change_links.get(k))) for k in st.by_key}
    reason = coverage.comments_tool_dropped(scope.catalog.tools_for("status"), scope.health.dropped)
    result = assess(deps.today, pms, items, st.by_key, evidence,
                    repeats_by_postmortem(pms, scope.incidents.incidents), comments_unavailable=reason is not None)
    notes = [*state.get("notes", []), *coverage.dropped_note(state.get("dropped") or 0),
             *coverage.unknown_status_note(result.unknown_status)]
    if reason is not None and result.not_verified:
        notes.append(coverage.comments_note(reason, result.not_verified))
    notes += coverage.unavailable_lines(scope.health.dropped, reason)
    open_items = open_by_postmortem(deps.today, items, st.by_key, evidence)
    reply = table_render.render_report(result, window, notes)
    match = match_text(state, pms, result.rows, open_items, scope.incidents.named)
    return {"reply": f"{match}\n\n{reply}" if match else reply, "rows": [r.__dict__ for r in result.rows],
            "wont_do": list(result.wont_do), "untracked": list(result.untracked), "open_items": open_items,
            "outcomes": {}, "route": ""}


def match_text(state: dict, pms, rows, open_items: dict, named: Incident | None) -> str:
    """The answer to "does this outage match an old postmortem?", built in code."""
    ref, described = state.get("match_ref"), state.get("match_text")
    if not ref and not described:
        return ""
    if ref and named is None:
        return f"I couldn't find incident {ref} with the tools I have, so I can't match it."
    title, service = (named.title, named.service) if named else (described, None)
    label = f"{named.id} ({named.title})" if named else f"\"{described}\""
    pairs = {k: [(key, idle) for key, idle in v] for k, v in open_items.items()}
    return table_render.match_sentences(label, best_matches(pms, title, service), rows, pairs)
