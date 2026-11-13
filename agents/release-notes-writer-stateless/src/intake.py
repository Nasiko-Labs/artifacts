"""Pure: what this turn is. Greeting, a follow-up, a new change list, or a short message that needs the scope
call. Nothing is stored between messages and no turn pauses.

Order: empty / greeting -> follow-up command -> a change list of 2+ items -> anything else goes to the scope
call. A follow-up is rebuilt from the newest change list still in the platform's chat history, with the
zero-call edits the user sent after it (drop, move, version, date, audience, tone, include internal) replayed;
when that list has been trimmed out of the history the reply says so and asks for it again.

Former pauses, now decided here and said in the notes:
- over the cap (300 changes / 60,000 characters): the first 300 are written up, the rest is named as left out;
- every change internal: they are all included (the user asked for notes on them), with a note saying so."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import followup
import history
import prompts
import smalltalk
from change import Change
from classify import classify_all
from inputs import Header, Incoming, split_message
from parse_changes import parse

MIN_CHANGES = 2
MAX_CHANGES = 300
MAX_CHARS = 60_000
HEADER_EDITS = ("version", "date", "audience", "tone")
POSITION_EDITS = ("drop", "move")


@dataclass(frozen=True)
class Route:
    kind: str                                   # reply | run | followup | scope
    reply: str = ""
    changes: tuple[Change, ...] = ()
    header: Header = field(default_factory=Header)
    command: followup.Command | None = None
    notes: tuple[str, ...] = ()                 # what was decided instead of asking
    edits: tuple[followup.Command, ...] = ()    # earlier drop / move edits, replayed on a rebuilt release


def plan_turn(incoming: Incoming) -> Route:
    text = incoming.text or ""
    canned = smalltalk.reply_to(text)
    if canned is not None:
        return Route("reply", reply=canned)
    earlier = earlier_list(incoming.history)
    command = followup.parse(text)
    if command is not None and command.kind == "send":
        return Route("reply", reply=prompts.ONLY_DRAFT if earlier or incoming.history else prompts.NO_NOTES)
    if command is not None and earlier is not None:
        return replace(earlier, kind="followup", command=command)
    if command is not None and incoming.history:
        return Route("reply", reply=prompts.LIST_TRIMMED)
    route = plan_list(incoming)
    if route.kind == "scope" and earlier is not None:  # the scope call may be a revision of those notes
        return replace(earlier, kind="scope")
    return route


def plan_list(incoming: Incoming, include_internal: bool = False) -> Route:
    """A fresh change list, capped and classified, with a note for each decision made instead of a question."""
    text = incoming.text or ""
    split = split_message(text[:MAX_CHARS])
    changes = parse(split.body)
    notes: list[str] = []
    total = len(parse(split_message(text).body)) if len(text) > MAX_CHARS else len(changes)
    if total > MAX_CHANGES or len(text) > MAX_CHARS:
        notes.append(prompts.over_cap(max(total, len(changes)), MAX_CHANGES, MAX_CHARS))
        changes = changes[:MAX_CHANGES]
    if len(changes) < MIN_CHANGES:
        return Route("scope")
    include = include_internal or split.include_internal
    classified = tuple(classify_all(changes, include))
    if not include and all(c.internal for c in classified):
        classified = tuple(classify_all(changes, True))
        notes.append(prompts.ALL_INTERNAL_INCLUDED)
    return Route("run", changes=classified, header=split.header, notes=tuple(notes))


def earlier_list(turns: tuple[history.Turn, ...]) -> Route | None:
    """The newest change list in the history as a ready route, with the edits sent after it; None when the
    history no longer holds one."""
    commands: list[followup.Command] = []
    for text in history.user_texts(turns):
        command = followup.parse(text)
        if command is not None:
            commands.append(command)
            continue
        include = any(c.kind == "include" for c in commands)
        route = plan_list(Incoming(text), include_internal=include)
        if route.kind != "run":
            continue
        replayed = tuple(reversed(commands))
        header = route.header
        for c in replayed:
            if c.kind in HEADER_EDITS:
                header = replace(header, **{c.kind: c.value})
        edits = tuple(c for c in replayed if c.kind in POSITION_EDITS)
        return replace(route, header=header, edits=edits)
    return None
