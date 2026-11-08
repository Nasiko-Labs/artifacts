"""Pure: the copyable note to attendees, rendered from a template and the stored facts (no model
prose), and which meeting a message refers to. Nothing here sends anything."""

from __future__ import annotations

import re
from collections.abc import Sequence

from records import MeetingRoi
from render import num

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset({"the", "a", "an", "to", "of", "for", "and", "with", "my", "our", "meeting", "meetings", "note",
                   "draft", "attendees", "it", "cancel", "why", "please", "on", "in", "at", "this", "that"})
_DAYS = {"mon": "monday", "tue": "tuesday", "wed": "wednesday", "thu": "thursday", "fri": "friday",
         "sat": "saturday", "sun": "sunday"}
DRAFT_REQUEST = re.compile(r"\b(?:draft|write|compose|prepare)\b.{0,40}\b(?:note|message|email|mail)\b",
                           re.IGNORECASE)
WRITE_REQUEST = re.compile(
    r"^\s*(?:ok(?:ay)?[,.]?\s+)?(?:please\s+|can you\s+|could you\s+|would you\s+|go ahead and\s+|just\s+|now\s+)*"
    r"(?:cancel|delete|remove|decline|reschedule|move|send|email|post|edit|shorten|shrink)\b(?!\s+(?:me|us)\b)",
    re.IGNORECASE)
NOT_SENT = "Nothing was sent: copy it and send it yourself if you agree."


def is_write_request(text: str) -> bool:
    """An imperative asking the agent to change or send something ("cancel the Tuesday sync")."""
    return bool(WRITE_REQUEST.search(text or "")) and "?" not in (text or "")


def is_draft_request(text: str) -> bool:
    return bool(DRAFT_REQUEST.search(text or ""))


def pick(meetings: Sequence[MeetingRoi], text: str, focus: str | None = None) -> MeetingRoi | None:
    """The meeting the message names (title words, or its weekday), else the focus, else the first proposal."""
    words = set(_WORD.findall((text or "").casefold())) - _STOP
    best, best_score = None, 0
    for m in meetings:
        title_words = set(_WORD.findall(m.series_title.casefold())) - _STOP
        score = 2 * len(words & title_words)
        day = _DAYS.get(m.weekday.casefold()[:3], "")
        score += 1 if day and (day in words or m.weekday.casefold() in words) else 0
        if score > best_score:
            best, best_score = m, score
    if best is not None:
        return best
    by_focus = next((m for m in meetings if focus and m.series_title == focus), None)
    return by_focus or next((m for m in meetings if m.proposal != "keep"), meetings[0] if meetings else None)


def _period(window_days: int) -> str:
    return f"{window_days // 7} weeks" if window_days % 7 == 0 else f"{window_days} days"


def _proposal(m: MeetingRoi) -> str:
    if m.proposal in ("cancel", "ask the organizer"):
        return f"cancel {m.series_title}"
    if m.proposal == "shorten":
        return f"shorten {m.series_title} from {m.duration_min} to {m.proposed_minutes} minutes"
    if m.proposal == "shrink":
        return f"keep {m.series_title} to the people who make its decisions (at most {m.proposed_size})"
    return f"keep {m.series_title} as it is"


def _facts(m: MeetingRoi, window_days: int) -> str:
    parts = [f"it met {m.occurrences} time{'s' if m.occurrences != 1 else ''}"]
    if m.accept_rate is not None:
        parts.append(f"on average {num(m.accepted)} of {m.invited} invitees accepted")
    if m.has_notes is False:
        parts.append("I found no notes doc")
    elif m.has_notes and m.has_decisions == "false":
        parts.append("the notes record no decisions")
    elif m.has_decisions == "true":
        parts.append("the notes record decisions")
    if m.followup_rate is not None:
        parts.append(f"chat follow-up came after {round(m.followup_rate * 100)} % of them")
    parts.append(f"together it takes about {num(m.hours_per_month_person)} person-hours a month")
    return f"In the last {_period(window_days)} " + ", ".join(parts) + "."


def note(m: MeetingRoi, window_days: int) -> str:
    body = f"Proposal: {_proposal(m)}. {_facts(m, window_days)} Objections?".replace("`", "'")
    to = "" if m.user_is_organizer is not False else f" (you are a guest: send it to {m.organizer or 'the organizer'})"
    return f"Here is a note for the {m.series_title} attendees{to}:\n\n```\n{body}\n```\n\n{NOT_SENT}"
