"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
Times, speakers, attendees, `mine`, `confidence` and `needs_human` are computed here, never taken from
the model."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from attendees import Attendee
from items import NOT_STATED, Action, Decision, Result, Turn
from owners import is_mine

HIGH_MAX_DROP = 0.10
LOW_MIN_DROP = 0.30
LOW_COVERAGE = 0.70
EMPTY_LONG_WORDS = 1_000


@dataclass(frozen=True)
class Meeting:
    turns: tuple[Turn, ...]
    attendees: tuple[Attendee, ...]
    has_speakers: bool
    title: str = ""
    date: str = ""
    words: int = 0
    my_name: str = ""

    def turn(self, turn_id: str) -> Turn | None:
        return next((t for t in self.turns if t.id == turn_id), None)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(a.name for a in self.attendees)

    def minutes(self) -> int:
        times = [t.secs for t in self.turns if t.secs >= 0]
        return round((max(times) - min(times)) / 60) if len(times) >= 2 else 0


def drop_rate(result: Result) -> float:
    total = result.kept_before_caps + result.dropped
    return result.dropped / total if total else 0.0


def confidence(result: Result, meeting: Meeting) -> str:
    rate = drop_rate(result)
    no_items = not result.decisions and not result.actions
    if (rate > LOW_MIN_DROP or result.failed_parts >= 2 or result.coverage < LOW_COVERAGE
            or (no_items and meeting.words > EMPTY_LONG_WORDS)):
        return "low"
    if (meeting.has_speakers and len(meeting.attendees) >= 2 and result.failed_parts == 0 and rate <= HIGH_MAX_DROP
            and result.coverage >= 1.0):
        return "high"
    return "medium"


def needs_human(result: Result, level: str) -> bool:
    return level == "low" or any(a.kind == "action" and a.owner == NOT_STATED for a in result.actions)


def decision_dict(d: Decision, meeting: Meeting) -> dict:
    turn = meeting.turn(d.turn)
    return {"text": d.text, "time": turn.time if turn else "", "speaker": turn.speaker if turn else "",
            "quote": d.quote, "turn": d.turn}


def action_dict(a: Action, meeting: Meeting) -> dict:
    turn = meeting.turn(a.turn)
    return {"owner": a.owner, "task": a.task, "due": a.due, "kind": a.kind,
            "mine": is_mine(a.owner, meeting.my_name, meeting.names), "time": turn.time if turn else "",
            "quote": a.quote, "turn": a.turn}


def summary_text(result: Result) -> str:
    firsts = [t.bullets[0].text for t in result.topics[:2] if t.bullets]
    if firsts:
        return " ".join(f if f.endswith((".", "…", "!", "?")) else f + "." for f in firsts)
    actions = sum(1 for a in result.actions if a.kind == "action")
    return f"{len(result.decisions)} decisions and {actions} actions recorded."


def build(result: Result, meeting: Meeting, notes_markdown: str) -> dict:
    level = confidence(result, meeting)
    items = [action_dict(a, meeting) for a in result.actions]
    return {"summary": summary_text(result), "items": items, "notes": notes_markdown,
            "attendees": [a.to_dict() for a in meeting.attendees],
            "decisions": [decision_dict(d, meeting) for d in result.decisions], "action_items": items,
            "title": meeting.title, "date": meeting.date, "turns": len(meeting.turns),
            "left_out": result.dropped, "confidence": level, "needs_human": needs_human(result, level)}


def mine_only(actions: Sequence[Action], meeting: Meeting) -> tuple[Action, ...]:
    return tuple(a for a in actions if is_mine(a.owner, meeting.my_name, meeting.names))
