"""Preparing nudges: one chat DM per owner listing their selected items, approved per owner.

The owner is the ticket's assignee email exactly as the tracker returned it. The chat user must be the
ONE user the lookup returned for that email (read by shape, not from the model). The model writes only
the opening line, which code checks; the item lines are appended by code. The approval binds the
recipient to that user id."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import step_prompts
import table_render
from actions import Action
from assess import Row
from corpus import users_with_email
from deps import TurnDeps
from discovery import discover
from prompts import fence_data, nudge_draft_system
from records import TICKET_KEY, load_json_object, text_of
from send_guard import WriteSpec
from steps import ask_once, run_step
from tool_health import ToolHealth

log = logging.getLogger("postmortem.nudge")

MAX_OWNERS = 5
MAX_OPENING_CHARS = 300
LOOKUP_CALLS = 3
LOOKUP_SECS = 60.0
FINAL = frozenset({"nudged", "denied"})  # never nudged again in this conversation
_LINK = re.compile(r"https?://|www\.", re.IGNORECASE)


@dataclass(frozen=True)
class Prepared:
    actions: tuple[Action, ...] = ()
    notes: tuple[str, ...] = ()


def group_by_owner(rows: Sequence[Row], outcomes: Mapping[str, str]) -> tuple[dict[str, list[Row]], list[str]]:
    """Rows grouped by the owner's email (case-insensitive, first spelling kept), and notes for the rest."""
    groups: dict[str, list[Row]] = {}
    spelled: dict[str, str] = {}
    notes: list[str] = []
    for row in rows:
        if outcomes.get(f"nudge:{row.key}") in FINAL:
            notes.append(f"#{row.n} ({row.key}): its owner was already nudged about it here, so I won't again.")
        elif not row.owner_email:
            why = "it has no owner" if row.owner == "unassigned" else "the tracker didn't return the owner's email"
            notes.append(f"#{row.n} ({row.key}): I can't send a nudge because {why}.")
        else:
            email = spelled.setdefault(row.owner_email.casefold(), row.owner_email)
            groups.setdefault(email, []).append(row)
    if len(groups) > MAX_OWNERS:
        extra = list(groups)[MAX_OWNERS:]
        notes.append(f"I'll nudge {MAX_OWNERS} owners per turn; ask again for {', '.join(extra)}.")
        groups = {k: groups[k] for k in list(groups)[:MAX_OWNERS]}
    return groups, notes


def check_opening(text: str, own_keys: Sequence[str]) -> bool:
    """At most 300 chars, no links, no ticket keys other than this owner's."""
    if not text or len(text) > MAX_OPENING_CHARS or _LINK.search(text):
        return False
    return all(key in own_keys for key in TICKET_KEY.findall(text))


async def prepare(deps: TurnDeps, rows: Sequence[Row], outcomes: Mapping[str, str]) -> Prepared:
    groups, notes = group_by_owner(rows, outcomes)
    if not groups:
        return Prepared((), tuple(notes))
    catalog, failure = await discover(deps, ("lookup", "nudge"))
    if failure is not None:
        return Prepared((), (*notes, failure))
    post, opener = catalog.pick("nudge", "message"), catalog.pick("nudge", "open_dm")
    lookup_tools = catalog.tools_for("lookup")
    openings = await asyncio.gather(*(_opening(deps, owner_rows) for owner_rows in groups.values()))
    if post is None or not lookup_tools:
        missing = "send a chat message" if post is None else "look up chat users by email"
        texts = [table_render.render_copyable(f"For {rows_[0].owner} <{email}>:", table_render.nudge_message(o, rows_),
                                              f"No chat tool can {missing} for this agent, so I can't send it.")
                 for (email, rows_), o in zip(groups.items(), openings, strict=True)]
        return Prepared((), (*notes, *texts))
    resolved = await _resolve(deps, lookup_tools, list(groups))
    actions = []
    for (email, owner_rows), opening in zip(groups.items(), openings, strict=True):
        user_id = resolved.get(email)
        if user_id is None:
            notes.append(f"{owner_rows[0].owner} <{email}>: I couldn't find exactly one chat user with that email, "
                         "so I won't message anyone for them.")
            continue
        actions.append(_action(email, owner_rows, opening, user_id, post.name, opener.name if opener else None))
    return Prepared(tuple(actions), tuple(notes))


async def _resolve(deps: TurnDeps, tools, emails: list[str]) -> dict[str, str]:
    health = ToolHealth(deps.gateway)
    result = await run_step(deps, tools, health, step_prompts.lookup_system(emails), "Look up these users.",
                            LOOKUP_CALLS, LOOKUP_SECS)
    resolved = {}
    for email in emails:
        ids = users_with_email(email, result.fenced.records)
        if len(ids) == 1:
            resolved[email] = ids[0]
        else:
            log.info("lookup for an owner returned %d users", len(ids))
    return resolved


async def _opening(deps: TurnDeps, rows: Sequence[Row]) -> str:
    owner = rows[0].owner
    data = fence_data(f"Owner: {owner}\nItems:\n" + "\n".join(f"{r.key} {r.text} ({r.state})" for r in rows))
    try:
        answer = await ask_once(deps, ({"role": "system", "content": nudge_draft_system()},
                                       {"role": "user", "content": data}))
    except Exception:  # a failed draft call falls back to the fixed opening; nothing is sent without approval
        log.exception("nudge draft call failed")
        answer = ""
    opening = text_of((load_json_object(answer) or {}).get("opening"))
    return opening if check_opening(opening, [r.key for r in rows]) else table_render.default_opening(owner)


def _action(email: str, rows: Sequence[Row], opening: str, user_id: str, post: str, opener: str | None) -> Action:
    writes = ((WriteSpec("open_dm", opener, (user_id,), None, required=False),) if opener else ()) + \
        (WriteSpec("message", post, (user_id,), None),)
    via = post if opener is None else f"{post} (opening the direct message with {opener} if needed)"
    numbers = ", ".join(f"#{r.n}" for r in rows)
    details = f"To: {rows[0].owner} <{email}>, chat user {user_id}\nItems: {numbers}\nVia: {via}"
    return Action(kind="nudge", ref=email, who=rows[0].owner, details=details, editable=opening,
                  fixed="\n".join(table_render.nudge_item_line(r) for r in rows), writes=writes,
                  row_keys=tuple(r.key for r in rows))
