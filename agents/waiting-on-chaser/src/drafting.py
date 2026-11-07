"""The draft step: one tool-less LLM call per requested ask (at most five a turn), answered from the
stored list. The tier and relationship come from code (scoring.py) as data. Recipient, channel and
thread come from the checked ask. The send tool must be one the gateway lists or its tool search finds
this request, and whose name reads as a write; otherwise the nudge is offered to copy."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

import prompts
import render
from asks import Ask
from deps import TurnDeps
from discovery import discover
from drafts import Draft, body_problem, draft_for, parse_draft
from nasiko_mcp import McpTool
from picker import FINAL_OUTCOMES
from sweep import ask_once

log = logging.getLogger("waiting_on.drafting")

MAX_DRAFTS_PER_TURN = 5
_NUMBER = re.compile(r"\d+")
_DRAFT_TIER = {"recent": "light"}  # a named "recent" ask still gets the gentlest nudge


@dataclass(frozen=True)
class DraftTurn:
    reply: str
    drafts: tuple[Draft, ...] = ()


def requested_ids(answer: str) -> tuple[str, ...]:
    """`NUDGE:1, #3` → ("1", "3"), de-duplicated in order."""
    return tuple(dict.fromkeys(_NUMBER.findall(answer[len(prompts.NUDGE_PREFIX):])))


async def draft_turn(deps: TurnDeps, instruction: str, ids: Sequence[str], items: Sequence[Ask],
                     view: render.View) -> DraftTurn:
    """`ids` are list numbers (`#n`)."""
    by_id = {item.id: item for item in items}
    unknown = [i for i in ids if i not in by_id]
    finished = [by_id[i] for i in ids if i in by_id and view.outcomes.get(by_id[i].key) in FINAL_OUTCOMES]
    wanted = [by_id[i] for i in ids if i in by_id and by_id[i] not in finished][:MAX_DRAFTS_PER_TURN]
    notes = [f"I don't have #{i} in the list." for i in unknown]
    notes += [f"#{x.id} was already handled ({view.outcomes[x.key]}), so I won't nudge it again." for x in finished]
    if not wanted:
        return DraftTurn(" ".join(notes) or prompts.NOTHING_TO_DRAFT)
    catalog, failure = await discover(deps, "send")
    if failure is not None:
        return DraftTurn(failure)
    senders = catalog.send_tools()
    drafts: tuple[Draft, ...] = ()
    for ask in wanted:
        draft, note = await _draft_one(deps, instruction, ask, senders, view)
        drafts = (*drafts, draft) if draft else drafts
        notes = [*notes, note] if note else notes
    return DraftTurn("\n\n".join(notes), drafts)


async def _draft_one(deps: TurnDeps, instruction: str, ask: Ask, senders: Sequence[McpTool],
                     view: render.View) -> tuple[Draft | None, str | None]:
    tier = _DRAFT_TIER.get(view.scored(ask).tier, view.scored(ask).tier)
    tool_list = json.dumps([{"name": t.name, "description": t.description} for t in senders], ensure_ascii=False)
    record = f"{render.render_for_model((ask,), view)}\nDraft tier: {tier}"
    system = (f"{prompts.draft_system(deps.today)}\n\n{prompts.fence_prior(record)}\n\n"
              f"{prompts.fence_candidates(tool_list)}")
    request = instruction or f"Draft a {tier} nudge for #{ask.id}."
    text = parse_draft(await ask_once(deps, ({"role": "system", "content": system},
                                             {"role": "user", "content": request})))
    if text is None:
        return None, f"I couldn't draft #{ask.id}; ask me again."
    problem = body_problem(ask, text.body)
    if problem is not None:
        log.info("draft for #%s refused: %s", ask.id, problem)
        return None, f"I couldn't draft #{ask.id} safely ({problem}); ask me again."
    subject = text.subject if ask.source == "email" else ""
    if text.tool not in {t.name for t in senders}:
        log.info("draft for #%s names no listed send tool", ask.id)
        reason = "I have no granted send tool for this channel, so I can't send it."
        return None, render.render_copyable(ask.id, ask.who, subject, text.body, reason)
    return draft_for(ask, text, text.tool, tier), None
