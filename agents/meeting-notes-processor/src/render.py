"""Pure: the reply, in fixed sections, from checked items and the parse only. Views cost no LLM call."""

from __future__ import annotations

import json
from collections.abc import Sequence

from attendees import NOT_IDENTIFIED
from checks import clean_text
from envelope import Meeting, mine_only
from items import NOT_STATED, Action, Result
from owners import is_mine

EMPTY = "None recorded."
VIEWS = ("default", "decisions", "actions", "followups", "mine", "sources", "table", "notes")


def header(meeting: Meeting) -> str:
    title = clean_text(meeting.title, meeting.title) or "Meeting notes"
    extra = [p for p in (meeting.date, f"{meeting.minutes()} min" if meeting.minutes() else "") if p]
    when = f" ({', '.join(extra)})" if extra else ""
    return f"**{title}**{when} Attendees: {attendee_line(meeting)}"


def attendee_line(meeting: Meeting) -> str:
    if not meeting.attendees:
        return NOT_IDENTIFIED
    return ", ".join(f"{a.name} ({a.share}%)" for a in meeting.attendees)


def cite(turn_ids: Sequence[str], meeting: Meeting) -> str:
    first = meeting.turn(turn_ids[0]) if turn_ids else None
    when = f", {first.time}" if first and first.time else ""
    return f"[{', '.join(turn_ids)}{when}]"


def notes_markdown(result: Result, meeting: Meeting) -> str:
    """The envelope's `notes`: attendees and topics."""
    people = [f"- {a.name}: {a.turns} turns ({a.share}%)" for a in meeting.attendees] or [f"- {NOT_IDENTIFIED}"]
    return "## Attendees\n" + "\n".join(people) + "\n\n## Topics\n" + (_topics(result, meeting) or EMPTY)


def _topics(result: Result, meeting: Meeting) -> str:
    blocks = []
    for topic in result.topics:
        bullets = "\n".join(f"- {b.text} {cite(b.turns, meeting)}" for b in topic.bullets)
        blocks.append(f"### {topic.title}\n{bullets}")
    return "\n".join(blocks)


def render(result: Result, meeting: Meeting, view: str = "default", extra_notes: Sequence[str] = ()) -> str:
    actions = tuple(a for a in result.actions if a.kind == "action")
    follow = tuple(a for a in result.actions if a.kind == "follow_up")
    if view == "decisions":
        body = [_decisions(result, meeting, False)]
    elif view == "actions":
        body = [_actions(actions, meeting)]
    elif view == "followups":
        body = [_follow_ups(follow, meeting)]
    elif view == "mine":
        body = [f"**Yours ({meeting.my_name})**\n" + _list(mine_only(result.actions, meeting), meeting, False)]
    elif view == "sources":
        body = [_decisions(result, meeting, True), "**Action items and follow-ups**\n"
                + _list(result.actions, meeting, True)]
    elif view == "table":
        body = [_table(result, meeting)]
    elif view == "notes":
        body = ["**Notes**\n" + (_topics(result, meeting) or EMPTY)]
    else:
        body = ["**Notes**\n" + (_topics(result, meeting) or EMPTY), _decisions(result, meeting, False),
                _actions(actions, meeting), _follow_ups(follow, meeting)]
    return "\n\n".join(p for p in (header(meeting), *body, footer(result, meeting, extra_notes)) if p)


def render_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"


def footer(result: Result, meeting: Meeting, extra_notes: Sequence[str] = ()) -> str:
    if meeting.has_speakers:
        lines = [f"{len(meeting.turns)} turns, {len(meeting.attendees)} speakers."]
    else:
        lines = [f"{len(meeting.turns)} lines, no speaker labels."]
    if result.dropped:
        lines.append(f"{_count(result.dropped)} left out: no supporting quote.")
    if result.capped:
        lines.append(f"{_count(result.capped)} more left out to keep the lists short.")
    notes = [n for n in (*result.notes, *extra_notes) if n]
    return "\n".join([f"_({' '.join(lines)})_", *notes])


def _count(n: int) -> str:
    return f"{n} item" if n == 1 else f"{n} items"


def _when(turn_id: str, meeting: Meeting) -> tuple[str, str]:
    turn = meeting.turn(turn_id)
    return (turn.speaker, turn.time) if turn else ("", "")


def _quote(text: str) -> str:
    return f'\n   > "{clean_text(text, text)}"'


def _decisions(result: Result, meeting: Meeting, sources: bool) -> str:
    lines = []
    for n, d in enumerate(result.decisions, start=1):
        who = ", ".join(p for p in _when(d.turn, meeting) if p and p != "Unknown")
        lines.append(f"{n}. {d.text}" + (f" ({who})" if who else "") + (_quote(d.quote) if sources else ""))
    return "**Decisions**\n" + ("\n".join(lines) or EMPTY)


def _owner(a: Action, meeting: Meeting) -> str:
    return a.owner + (" *" if is_mine(a.owner, meeting.my_name, meeting.names) else "")


def _actions(actions: Sequence[Action], meeting: Meeting) -> str:
    if not actions:
        return "**Action items**\n" + EMPTY
    rows = [f"| {n} | {_owner(a, meeting)} | {a.task} | {a.due or '-'} | {_when(a.turn, meeting)[1] or '-'} |"
            for n, a in enumerate(actions, start=1)]
    mark = " (yours marked *)" if meeting.my_name else ""
    return (f"**Action items**{mark}\n| # | Owner | Task | Due | Time |\n|---|---|---|---|---|\n" + "\n".join(rows))


def _follow_ups(follow: Sequence[Action], meeting: Meeting) -> str:
    if not follow:
        return "**Follow-ups**\n" + EMPTY
    rows = [f"| {n} | {_owner(a, meeting)} | {a.task} | {a.due or '-'} |" for n, a in enumerate(follow, start=1)]
    return "**Follow-ups**\n| # | Owner | Item | Due |\n|---|---|---|---|\n" + "\n".join(rows)


def _list(actions: Sequence[Action], meeting: Meeting, sources: bool) -> str:
    lines = []
    for n, a in enumerate(actions, start=1):
        kind = "follow-up" if a.kind == "follow_up" else "action"
        lines.append(f"{n}. {a.task} ({kind}; owner: {a.owner or NOT_STATED}; due: {a.due or '-'}) "
                     f"{cite([a.turn], meeting)}" + (_quote(a.quote) if sources else ""))
    return "\n".join(lines) or EMPTY


def _table(result: Result, meeting: Meeting) -> str:
    rows = [f"| Decision | {d.text} | - | - | {_when(d.turn, meeting)[1] or '-'} |" for d in result.decisions]
    rows += [f"| {'Follow-up' if a.kind == 'follow_up' else 'Action'} | {a.task} | {_owner(a, meeting)} | "
             f"{a.due or '-'} | {_when(a.turn, meeting)[1] or '-'} |" for a in result.actions]
    if not rows:
        return "**Decisions and action items**\n" + EMPTY
    return ("**Decisions and action items**\n| Type | Item | Owner | Due | Time |\n|---|---|---|---|---|\n"
            + "\n".join(rows))
