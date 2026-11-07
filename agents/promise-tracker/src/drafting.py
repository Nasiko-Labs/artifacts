"""The draft step: one tool-less LLM call per requested promise, answered from the stored list.

Recipient, channel and thread come from the checked promise. The send tool must be one the gateway
lists or its tool search finds this request, and whose name reads as a write; otherwise the draft is
offered to copy."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import prompts
import render
from deps import TurnDeps
from discovery import discover
from drafts import Draft, draft_for, parse_draft
from nasiko_mcp import McpTool
from promises import Promise
from sweep import ask_once

log = logging.getLogger("promise_tracker.drafting")

MAX_DRAFTS_PER_TURN = 5
_FINAL_OUTCOMES = frozenset({"sent", "denied"})  # never drafted again: no double send, no retry after a no
_NUMBER = re.compile(r"\d+")


@dataclass(frozen=True)
class DraftTurn:
    reply: str
    drafts: tuple[Draft, ...] = ()


def requested_ids(answer: str) -> tuple[str, ...]:
    """`DRAFT:1, #3` → ("1", "3"), de-duplicated in order."""
    found = _NUMBER.findall(answer[len(prompts.DRAFT_PREFIX):])
    return tuple(dict.fromkeys(found))


async def draft_turn(deps: TurnDeps, query: str, ids: Sequence[str], items: Sequence[Promise],
                     outcomes: Mapping[str, str]) -> DraftTurn:
    by_id = {item.id: item for item in items}
    unknown = [i for i in ids if i not in by_id]
    finished = [i for i in ids if i in by_id and outcomes.get(i) in _FINAL_OUTCOMES]
    wanted = [by_id[i] for i in ids if i in by_id and i not in finished][:MAX_DRAFTS_PER_TURN]
    notes = _notes(unknown, finished, outcomes)
    if not wanted:
        return DraftTurn(" ".join(notes) or prompts.NOTHING_TO_DRAFT)
    catalog, failure = await discover(deps, "send")
    if failure is not None:
        return DraftTurn(failure)
    senders = catalog.send_tools()
    drafts: tuple[Draft, ...] = ()
    for promise in wanted:
        draft, note = await _draft_one(deps, query, promise, senders, outcomes)
        drafts = (*drafts, draft) if draft else drafts
        notes = [*notes, note] if note else notes
    return DraftTurn("\n\n".join(notes), drafts)


def _notes(unknown: Sequence[str], finished: Sequence[str], outcomes: Mapping[str, str]) -> list[str]:
    notes = [f"I don't have #{i} in the list." for i in unknown]
    return [*notes, *(f"#{i} was already handled ({outcomes[i]}), so I won't draft it again." for i in finished)]


async def _draft_one(deps: TurnDeps, query: str, promise: Promise, senders: Sequence[McpTool],
                     outcomes: Mapping[str, str]) -> tuple[Draft | None, str | None]:
    tool_list = json.dumps([{"name": t.name, "description": t.description} for t in senders], ensure_ascii=False)
    record = render.render_for_followup((promise,), outcomes, deps.today)
    system = (f"{prompts.draft_system(deps.today)}\n\n{prompts.fence_prior(record)}\n\n"
              f"{prompts.fence_candidates(tool_list)}")
    text = parse_draft(await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query})))
    if text is None:
        return None, f"I couldn't draft #{promise.id}; ask me again."
    if text.tool not in {t.name for t in senders}:
        log.info("draft for #%s names no listed send tool", promise.id)
        reason = "I have no granted send tool for this channel, so I can't send it."
        subject = text.subject if promise.source == "email" else ""
        return None, render.render_copyable(promise.id, promise.who, subject, text.body, reason)
    return draft_for(promise, text, text.tool), None
