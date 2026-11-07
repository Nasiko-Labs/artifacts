"""The draft step: one tool-less LLM call per selected ticket (at most `max_drafts` per turn), checked in
code (draft_check); one redraft on failure; still failing → offered to copy, no pause.

Nothing is drafted unless the fix shipped. The ticket id comes from the checked ticket; the write tools a
reply may use are the support-desk write tools the gateway lists or its search finds this request."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import draft_check
import prompts
import records
import render
import replies
import tiers
from approval import Draft
from budget import TurnLimitReached
from deps import TurnDeps
from discovery import discover
from draft_check import Facts
from reader import ask_once
from records import Bug, ShipVerdict, Ticket

log = logging.getLogger("bug_notifier.drafting")

FINAL_OUTCOMES = frozenset({"sent", "solved", "denied"})  # never drafted again: no double post, no retry after a no


@dataclass(frozen=True)
class DraftTurn:
    reply: str
    drafts: tuple[Draft, ...] = ()
    facts: Facts | None = None


def facts_for(bug: Bug, verdict: ShipVerdict) -> Facts:
    return Facts(version=verdict.version, release_date=verdict.release_date, summary=bug.summary, bug_key=bug.key)


async def draft_turn(deps: TurnDeps, bug: Bug, verdict: ShipVerdict, tickets: Sequence[Ticket], ids: Sequence[str],
                     instruction: str, outcomes: Mapping[str, str]) -> DraftTurn:
    if not verdict.shipped:
        return DraftTurn(replies.not_shipped(verdict.verdict, verdict.reason))
    by_id = {t.id: t for t in tickets}
    notes = [f"I don't have ticket #{i} in the list." for i in ids if i not in by_id]
    notes += [f"#{i} was already handled ({outcomes[i]}), so I won't draft it again."
              for i in ids if i in by_id and outcomes.get(i) in FINAL_OUTCOMES]
    wanted = [by_id[i] for i in ids if i in by_id and outcomes.get(i) not in FINAL_OUTCOMES]
    if len(wanted) > deps.config.max_drafts:
        rest = ", ".join(f"#{t.id}" for t in wanted[deps.config.max_drafts:])
        notes.append(f"I drafted the first {deps.config.max_drafts}; ask again for the rest ({rest}).")
        wanted = wanted[:deps.config.max_drafts]
    if not wanted:
        return DraftTurn("\n\n".join(notes) or replies.NOTHING_SELECTED)
    catalog, failure = await discover(deps, "send")
    if failure is not None:
        return DraftTurn(failure)
    writers = tuple(t.name for t in catalog.send_tools())
    facts = facts_for(bug, verdict)
    drafts: tuple[Draft, ...] = ()
    for n, ticket in enumerate(wanted):
        try:
            body, problems = await _draft_one(deps, facts, ticket, instruction)
        except TurnLimitReached:
            notes.append("I ran out of time before drafting " + ", ".join(f"#{t.id}" for t in wanted[n:])
                         + "; ask again for those.")
            break
        draft, note = _settle(ticket, body, problems, writers)
        drafts = (*drafts, draft) if draft else drafts
        notes = [*notes, note] if note else notes
    return DraftTurn("\n\n".join(notes), drafts, facts)


def _settle(ticket: Ticket, body: str, problems: tuple[str, ...], writers: tuple[str, ...]) -> tuple[Draft | None, str]:
    customer = tiers.customer_of(ticket)
    if not body:
        return None, f"I couldn't draft a reply for #{ticket.id}; ask me again."
    if problems:
        log.info("draft for #%s still breaks the reply rules after a redraft", ticket.id)
        reason = f"I couldn't write a reply that keeps to the rules ({'; '.join(problems)}), so I won't post it."
        return None, render.copyable(ticket.id, customer, body, reason)
    if not writers:
        return None, render.copyable(ticket.id, customer, body, replies.NO_WRITE_TOOL)
    return Draft(ticket_id=ticket.id, customer=customer, subject=ticket.subject, body=body, tools=writers,
                 ticket_url=ticket.url), ""


async def _draft_one(deps: TurnDeps, facts: Facts, ticket: Ticket, instruction: str) -> tuple[str, tuple[str, ...]]:
    complaint = f"\nCustomer wrote: {ticket.complaint_quote}" if ticket.complaint_quote else ""
    request = f"{prompts.fence_ticket(f'Subject: {ticket.subject}{complaint}')}\n\n" + (
        f"The user's instruction for this reply: {instruction}" if instruction else "Draft the reply.")
    messages = ({"role": "system", "content": prompts.draft_system(facts.block())}, {"role": "user", "content": request})
    answer = await ask_once(deps, messages)
    body = records.parse_body(answer)
    problems = draft_check.problems(body, facts, deps.config.max_reply_chars) if body else ("it was empty",)
    if not problems:
        return body, ()
    log.info("draft for #%s broke the reply rules; asking for one redraft", ticket.id)
    retry = {"role": "user", "content": f"That draft breaks these rules: {'; '.join(problems)}. Rewrite it so it "
                                        "states only the facts given. Reply with ONLY the JSON object."}
    body = records.parse_body(await ask_once(deps, (*messages, {"role": "assistant", "content": answer}, retry)))
    return body, draft_check.problems(body, facts, deps.config.max_reply_chars) if body else ()
