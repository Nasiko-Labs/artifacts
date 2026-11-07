"""The draft step: one tool-less LLM call per requested account (at most five a turn), answered from the
stored list. The outreach type comes from code (outreach.py). The recipient is the customer address read
from the payments data (or one the user typed), and the thread is the account's latest inbound email.
The send tool must be one the gateway lists or its tool search finds this request, and whose name reads
as a write; otherwise the draft is offered to copy."""

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
from drafts import Draft, body_problem, draft_for, parse_draft
from nasiko_mcp import McpTool
from picker import FINAL_OUTCOMES, Pick, email_question
from reader import ask_once
from scoring import Row

log = logging.getLogger("churn.drafting")

MAX_DRAFTS_PER_TURN = 5
_NUMBER = re.compile(r"\d+")


@dataclass(frozen=True)
class DraftTurn:
    reply: str
    drafts: tuple[Draft, ...] = ()
    ask_email: Pick | None = None


def requested_ids(answer: str) -> tuple[str, ...]:
    """`DRAFT:1, #3` → ("1", "3"), de-duplicated in order."""
    return tuple(dict.fromkeys(_NUMBER.findall(answer[len(prompts.DRAFT_PREFIX):])))


async def draft_turn(deps: TurnDeps, instruction: str, ids: Sequence[str], rows: Sequence[Row],
                     meta: render.ListMeta, outcomes: Mapping[str, str], typed_email: str = "") -> DraftTurn:
    """`ids` are list numbers (`#n`). `typed_email` is an address the user gave for a single account."""
    by_id = {r.id: r for r in rows}
    unknown = [i for i in ids if i not in by_id]
    finished = [by_id[i] for i in ids if i in by_id and outcomes.get(by_id[i].key) in FINAL_OUTCOMES]
    wanted = [by_id[i] for i in ids if i in by_id and by_id[i] not in finished][:MAX_DRAFTS_PER_TURN]
    notes = [f"I don't have #{i} in the list." for i in unknown]
    notes += [f"#{r.id} was already handled ({outcomes[r.key]}), so I won't draft it again." for r in finished]
    if not wanted:
        return DraftTurn(" ".join(notes) or prompts.NOTHING_TO_DRAFT)
    if not deps.traceparent:
        return DraftTurn(prompts.NO_TRACE_CONTEXT)
    single = len(wanted) == 1
    if single and not wanted[0].account.email and not typed_email:
        return DraftTurn(" ".join(notes), ask_email=email_question(wanted[0], instruction))
    senders = await _senders(deps)
    drafts: tuple[Draft, ...] = ()
    for row in wanted:
        to = typed_email if single and typed_email else row.account.email
        if not to:
            notes.append(f"#{row.id} {row.account.name}: no email address on record. Say \"draft #{row.id} to "
                         "name@company.com\" and I'll draft it.")
            continue
        draft, note = await _draft_one(deps, instruction, row, to, senders, meta, outcomes)
        drafts = (*drafts, draft) if draft else drafts
        notes = [*notes, note] if note else notes
    return DraftTurn("\n\n".join(notes), drafts)


async def _senders(deps: TurnDeps) -> tuple[McpTool, ...]:
    catalog, failure = await discover(deps, "send")
    if failure is not None:
        log.info("no send tools this turn: %s", failure.split(".")[0])
        return ()
    return catalog.send_tools()


async def _draft_one(deps: TurnDeps, instruction: str, row: Row, to: str, senders: Sequence[McpTool],
                     meta: render.ListMeta, outcomes: Mapping[str, str]) -> tuple[Draft | None, str | None]:
    tool_list = json.dumps([{"name": t.name, "description": t.description} for t in senders], ensure_ascii=False)
    record = (f"{render.render_for_model((row,), meta, outcomes)}\nRecipient: {to}\n"
              f"Reply in thread: {row.account.reply_thread or 'none (new email)'}")
    system = (f"{prompts.draft_system(deps.today, row.outreach)}\n\n{prompts.fence_prior(record)}\n\n"
              f"{prompts.fence_candidates(tool_list)}")
    request = instruction or f"Draft the {row.outreach} email for #{row.id}."
    text = parse_draft(await ask_once(deps, ({"role": "system", "content": system},
                                             {"role": "user", "content": request})))
    if text is None:
        return None, f"I couldn't draft #{row.id}; ask me again."
    problem = body_problem(row, to, text)
    if problem is not None:
        log.info("draft for #%s refused: %s", row.id, problem)
        return None, f"I couldn't draft #{row.id} safely ({problem}); ask me again."
    if text.tool not in {t.name for t in senders}:
        log.info("draft for #%s names no listed send tool", row.id)
        reason = "I have no granted email send tool, so I can't send it."
        return None, render.render_copyable(row.id, row.account.name, text.subject, text.body, reason)
    return draft_for(row, to, text), None
