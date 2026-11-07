"""One fresh audit: commitments → tracker → chat → classify → derive → render → rollup draft.

The steps share one read side (reader.Reader), so every value is checked against everything a tool
returned this turn. A step that cannot go on ends the turn with its fixed reply; the chat step and the
reported-status classification degrade instead, with a note."""

from __future__ import annotations

import chat_step
import classify
import derive
import drafting
import render
import source_step
import tracker_step
from deps import TurnDeps
from reader import Reader
from request import parse_request


async def run(deps: TurnDeps, query: str) -> dict:
    """The graph-state update: a reply only (state untouched), or the new audit and rollup."""
    request = parse_request(query, deps.today, deps.fiscal_start_month)
    reader = Reader(deps)
    found = await source_step.find(deps, reader, request, query)
    if found.reply is not None:
        return {"reply": found.reply, "route": ""}
    items = found.checked.audited
    tracker = await tracker_step.collect(deps, reader, items, request.quarter)
    if tracker.reply is not None:
        return {"reply": tracker.reply, "route": ""}
    chat = await chat_step.collect(deps, reader, items, request.quarter)
    reported, classify_notes = await classify.reported_statuses(deps, items)
    rows = derive.ranked([
        derive.row(c, tracker.facts.get(c.cid), chat.signals.get(c.cid, ()), reported=reported[c.cid],
                   quarter=request.quarter, today=deps.today, chat_searched=chat.searched)
        for c in items])
    not_audited = list(found.checked.not_audited)
    notes = tuple(dict.fromkeys((*found.notes, *tracker.notes, *chat.notes, *classify_notes)))
    table = render.render_audit(rows, request.quarter, not_audited, notes, found.checked.dropped)
    rollup = await drafting.draft(deps, rows, not_audited, request.quarter)
    reply = f"{table}\n\n**Draft rollup**\n\n{rollup}\n\n{render.SAVE_HINT}"
    return {"reply": reply, "route": "", "quarter": request.quarter.to_dict(), "rows": [r.to_dict() for r in rows],
            "not_audited": not_audited, "rollup": rollup, "written": {}}
