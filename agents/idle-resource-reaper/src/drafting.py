"""The draft step: one tool-less LLM call per owner (at most five a turn) for the opening of a "delete or
justify" message; the rest of the message is written in code (drafts.compose). Recipient and channel come
from the checked owner (or a contact the user typed), never from the model. The send tool must be a
message-sending tool the gateway lists or finds this request, on the owner's channel; otherwise the
message is offered to copy."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

import prompts
import render
from deps import TurnDeps
from discovery import Catalog, discover
from drafts import (
    Draft,
    compose,
    opening_problem,
    parse_opening,
    resource_line,
    subject_for,
)
from nasiko_mcp import McpTool
from owners import Group, regroup
from picker import FINAL_OUTCOMES, Pick, contact_question
from reader import ask_once

log = logging.getLogger("reaper.drafting")

MAX_DRAFTS_PER_TURN = 5
_NUMBER = re.compile(r"\d+")


@dataclass(frozen=True)
class DraftTurn:
    reply: str
    drafts: tuple[Draft, ...] = ()
    ask_contact: Pick | None = None


@dataclass(frozen=True)
class Typed:
    """A contact the user typed for one owner group: channel "chat" or "email", and the value."""

    key: str
    channel: str
    value: str


def requested_numbers(answer: str) -> tuple[str, ...]:
    """`DRAFT:1, #7` → ("1", "7"), de-duplicated in order."""
    return tuple(dict.fromkeys(_NUMBER.findall(answer[len(prompts.DRAFT_PREFIX):])))


def deadline_for(today: date, grace_days: int) -> date:
    return today + timedelta(days=grace_days)


async def draft_turn(deps: TurnDeps, instruction: str, keys: Sequence[str], rows, outcomes: Mapping[str, str],
                     grace_days: int, typed: Typed | None = None) -> DraftTurn:
    """`keys` are owner keys. A single owner with no contact is asked for one (one question)."""
    by_key = {g.key: g for g in regroup(rows)}
    finished = [by_key[k] for k in keys if k in by_key and outcomes.get(k) in FINAL_OUTCOMES]
    wanted = [_with_typed(by_key[k], typed) for k in keys if k in by_key and by_key[k] not in finished]
    wanted = wanted[:MAX_DRAFTS_PER_TURN]
    notes = [f"{g.label} was already handled ({outcomes[g.key]}), so I won't draft it again." for g in finished]
    if not wanted:
        return DraftTurn(" ".join(notes) or prompts.NOTHING_TO_DRAFT)
    if not deps.traceparent:
        return DraftTurn(prompts.NO_TRACE_CONTEXT)
    if len(wanted) == 1 and wanted[0].delivery == "none":
        return DraftTurn(" ".join(notes), ask_contact=contact_question(wanted[0], instruction))
    catalog = await _senders(deps)
    deadline = deadline_for(deps.today, grace_days)
    drafts: tuple[Draft, ...] = ()
    for g in wanted:
        if g.delivery == "none":
            notes.append(_no_contact_note(g))
            continue
        draft, note = await _draft_one(deps, instruction, g, catalog, deadline)
        drafts = (*drafts, draft) if draft else drafts
        notes = [*notes, note] if note else notes
    return DraftTurn("\n\n".join(notes), drafts)


def _with_typed(g: Group, typed: Typed | None) -> Group:
    if typed is None or typed.key != g.key:
        return g
    owner = g.owner if g.known else typed.value
    if typed.channel == "chat":
        return replace(g, owner=owner, chat_id=typed.value, key=g.key)
    return replace(g, owner=owner, email=typed.value, chat_id="", key=g.key)


def _no_contact_note(g: Group) -> str:
    first = g.rows[0].id
    return (f"{g.label}: no chat id or email, so I didn't draft it. Say \"draft for #{first}\" on its own and I'll "
            "ask where it should go.")


async def _senders(deps: TurnDeps) -> Catalog | None:
    catalog, failure = await discover(deps, "send")
    if failure is not None:
        log.info("no send tools this turn: %s", failure.split(".")[0])
        return None
    return catalog


def _record(g: Group, deadline: date) -> str:
    owner = g.owner or "the owner"
    lines = [f"Owner: {owner}", f"Channel: {g.delivery}", f"Reply by: {deadline.isoformat()}", "Resources:"]
    return "\n".join([*lines, *(resource_line(r) for r in g.rows)])


async def _draft_one(deps: TurnDeps, instruction: str, g: Group, catalog: Catalog | None,
                     deadline: date) -> tuple[Draft | None, str | None]:
    senders: Sequence[McpTool] = catalog.send_tools(g.delivery) if catalog is not None else ()
    tool_list = json.dumps([{"name": t.name, "description": t.description} for t in senders], ensure_ascii=False)
    system = (f"{prompts.draft_system(deadline)}\n\n{prompts.fence_prior(_record(g, deadline))}\n\n"
              f"{prompts.fence_candidates(tool_list)}")
    request = instruction or f"Write the opening for the message to {g.owner or 'the owner'}."
    opening = parse_opening(await ask_once(deps, ({"role": "system", "content": system},
                                                  {"role": "user", "content": request})))
    if opening is None:
        return None, f"I couldn't draft the message for {g.label}; ask me again."
    problem = opening_problem(opening.text)
    if problem is not None:
        log.info("draft for %s refused: %s", g.key, problem)
        return None, f"I couldn't draft the message for {g.label} safely ({problem}); ask me again."
    body = compose(opening.text, g.rows, deadline)
    to = g.chat_id if g.delivery == "chat" else g.email
    subject = subject_for(deadline) if g.delivery == "email" else ""
    if opening.tool not in {t.name for t in senders}:
        reason = f"I have no granted {g.delivery} send tool, so I can't send it."
        return None, render.render_copyable(g.label, to, subject, body, reason)
    return Draft(ref=g.label, key=g.key, channel=g.delivery, to=to, to_name=g.owner or to, subject=subject, body=body,
                 tool=opening.tool, resources=", ".join(r.resource_id for r in g.rows),
                 deadline=deadline.isoformat()), None
