"""Pure: what this turn is. Greeting, follow-up on the stored notes, a new change list (or a pause before
it), or a short message that needs the scope call.

Order: empty / greeting -> follow-up command (only when notes are stored) -> a change list of 2+ items
(over the cap -> Q1; every change internal -> Q2) -> anything else goes to the scope call."""

from __future__ import annotations

from dataclasses import dataclass, field

import followup
import prompts
import smalltalk
from change import Change
from classify import classify_all
from inputs import Header, Incoming, split_message
from parse_changes import parse
from pauses import ALL_INTERNAL, MAX_CHANGES, MAX_CHARS, OVER_CAP, Pending

MIN_CHANGES = 2


@dataclass(frozen=True)
class Route:
    kind: str                                   # reply | pause | run | followup | scope
    reply: str = ""
    pending: Pending | None = None
    changes: tuple[Change, ...] = ()
    header: Header = field(default_factory=Header)
    command: followup.Command | None = None


def plan_turn(incoming: Incoming, has_notes: bool) -> Route:
    text = incoming.text or ""
    canned = smalltalk.reply_to(text)
    if canned is not None:
        return Route("reply", reply=canned)
    command = followup.parse(text)
    if command is not None and command.kind == "send":
        return Route("reply", reply=prompts.ONLY_DRAFT if has_notes else prompts.NO_NOTES)
    if command is not None and has_notes:
        return Route("followup", command=command)
    return plan_list(incoming)


def plan_list(incoming: Incoming, limit: bool = False, include_internal: bool = False) -> Route:
    """A fresh change list. `limit` = the user chose "Use the first 300"; `include_internal` = Q2's yes."""
    text = incoming.text or ""
    split = split_message(text[:MAX_CHARS] if limit else text)
    changes = parse(split.body)
    if not limit and (len(text) > MAX_CHARS or len(changes) > MAX_CHANGES):
        return Route("pause", pending=Pending(OVER_CAP, text[:MAX_CHARS], max(len(changes), MAX_CHANGES + 1),
                                              incoming.wants_json))
    if len(changes) < MIN_CHANGES:
        return Route("scope")
    include = include_internal or split.include_internal
    classified = tuple(classify_all(changes[:MAX_CHANGES], include))
    if not include and all(c.internal for c in classified):
        return Route("pause", pending=Pending(ALL_INTERNAL, text[:MAX_CHARS], len(classified), incoming.wants_json))
    return Route("run", changes=classified, header=split.header)


def plan_resume(pending: Pending, go: bool) -> Route:
    if not go:
        return Route("reply", reply=prompts.CANCELLED)
    incoming = Incoming(pending.text, wants_json=pending.wants_json)
    if pending.kind == OVER_CAP:
        return plan_list(incoming, limit=True)
    return plan_list(incoming, include_internal=True)
