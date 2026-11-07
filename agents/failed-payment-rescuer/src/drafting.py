"""The draft step: one tool-less LLM call per requested failure (at most five a turn), answered from the
stored list. The tone tier and the decline angle come from code (scoring.py, classify.py) as data. The
recipient comes from the checked failure; links the record does not give become a placeholder. The send
tool must be one the gateway lists or its tool search finds, and whose name reads as a write; otherwise the
draft is offered to copy. The chat digest is assembled in code and needs no LLM call."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import prompts
import render
from deps import TurnDeps
from discovery import Catalog, discover
from drafts import (
    Draft,
    body_problem,
    clean_links,
    digest_draft,
    email_draft,
    parse_draft,
)
from failures import Failure
from nasiko_mcp import McpTool
from picker import FINAL_OUTCOMES
from send_guard import name_tokens
from steps import ask_once

log = logging.getLogger("payment_rescuer.drafting")

MAX_DRAFTS_PER_TURN = 5


@dataclass(frozen=True)
class DraftTurn:
    reply: str
    drafts: tuple[Draft, ...] = ()
    drafted: dict[str, str] = field(default_factory=dict)  # failure key → body, for the envelope


async def draft_turn(deps: TurnDeps, instruction: str, numbers: Sequence[str], items: Sequence[Failure],
                     view: render.View) -> DraftTurn:
    """`numbers` are list numbers (`#n`)."""
    by_number = {f.number: f for f in items}
    unknown = [n for n in numbers if n not in by_number]
    picked = [by_number[n] for n in numbers if n in by_number]
    finished = [f for f in picked if view.outcomes.get(f.key) in FINAL_OUTCOMES]
    contacted = [f for f in picked if f.contacted_at and f not in finished]
    wanted = [f for f in picked if f not in finished and f not in contacted][:MAX_DRAFTS_PER_TURN]
    notes = [f"I don't have #{n} in the list." for n in unknown]
    notes += [f"#{f.number} was already handled ({view.outcomes[f.key]}), so I won't draft it again." for f in finished]
    notes += [f"#{f.number}: skipped, you already emailed {f.customer_email} about this on {f.contacted_at}."
              for f in contacted]
    if not wanted:
        return DraftTurn(" ".join(notes) or prompts.NOTHING_TO_DRAFT)
    catalog, failure = await discover(deps, "send")
    if failure is not None:
        return DraftTurn(failure)
    senders = catalog.send_tools("email")
    drafts: tuple[Draft, ...] = ()
    drafted: dict[str, str] = {}
    for item in wanted:
        draft, note = await _draft_one(deps, instruction, item, senders, view)
        drafts = (*drafts, draft) if draft else drafts
        drafted = {**drafted, item.key: draft.body} if draft else drafted
        notes = [*notes, note] if note else notes
    return DraftTurn("\n\n".join(notes), drafts, drafted)


async def _draft_one(deps: TurnDeps, instruction: str, item: Failure, senders: Sequence[McpTool],
                     view: render.View) -> tuple[Draft | None, str | None]:
    scored = view.scored(item)
    tool_list = json.dumps([{"name": t.name, "description": t.description} for t in senders], ensure_ascii=False)
    record = render.model_line(scored, view)
    system = (f"{prompts.draft_system(deps.today, scored.tier, scored.reason.angle)}\n\n{prompts.fence_prior(record)}"
              f"\n\n{prompts.fence_candidates(tool_list)}")
    request = instruction or f"Draft the recovery email for #{item.number}."
    text = parse_draft(await ask_once(deps, ({"role": "system", "content": system},
                                             {"role": "user", "content": request})))
    if text is None:
        return None, f"I couldn't draft #{item.number}; ask me again."
    body = clean_links(text.body, [item.link or ""])
    problem = body_problem(item, body)
    if problem is not None:
        log.info("draft for #%s refused: %s", item.number, problem)
        return None, f"I couldn't draft #{item.number} safely ({problem}); ask me again."
    if text.tool not in {t.name for t in senders}:
        log.info("draft for #%s names no listed send tool", item.number)
        reason = "I have no granted email send tool, so I can't send it."
        return None, render.render_copyable(item.number, item.who, text.subject, body, reason)
    return email_draft(item, text, text.tool, scored.tier, body), None


async def digest_turn(deps: TurnDeps, channel: str, items: Sequence[Failure], view: render.View) -> DraftTurn:
    """The code-assembled digest of high-value failures, as one draft to approve for `channel`."""
    if not any(view.scored(f).high_value for f in items):
        return DraftTurn(prompts.NO_HIGH_VALUE)
    body = render.digest(items, view)
    catalog, failure = await discover(deps, "send")
    if failure is not None:
        return DraftTurn(failure)
    tool = chat_tool(catalog)
    if tool is None:
        return DraftTurn(render.render_copyable("", f"#{channel}", "", body, prompts.NO_CHAT_TOOL))
    return DraftTurn("", (digest_draft(channel, body, tool.name),))


def chat_tool(catalog: Catalog) -> McpTool | None:
    """The chat write tool, chosen in code: the first whose own words place it in chat, preferring one that
    posts a message."""
    tools = catalog.send_tools("chat")
    posting = [t for t in tools if {"post", "message", "send"} & set(name_tokens(t.name))]
    return next(iter(posting or tools), None)
