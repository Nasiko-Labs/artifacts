"""Pure: who owns an action, when it is due, whether it is a follow-up, and whether it is the user's.

Owner rules (the model never decides alone):
- hand-off: the cited turn asks a named attendee ("Mei, can you own the pricing doc?") and that person
  accepts within HANDOFF_WINDOW turns ("On it") -> owned by the accepter; no acceptance -> a follow-up
  with owner "Not stated".
- owner_ref "speaker" -> the speaker of the cited turn; "named" -> the name must be a speaker or be
  written in the quote, the turn or the HANDOFF_WINDOW turns around it; group words -> "Not stated".
Due: the phrase as written, found in the quote or within HANDOFF_WINDOW turns of it, else blank.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import grounding as g
from attendees import is_system
from items import NOT_STATED, UNKNOWN, Turn

HANDOFF_WINDOW = 3
GROUP_WORDS = frozenset({"we", "us", "our", "team", "the team", "everyone", "everybody", "all", "all of us", "they",
                         "them", "someone", "somebody", "anyone", "i", "me", "you", "tbd", "tbc", "n/a", "na", "none",
                         "null", "unknown", "unassigned", "not stated", "nobody", "speaker"})
FOLLOW_UP_PHRASES = re.compile(r"\b(?:revisit|circle back|check (?:in )?with|check (?:if|whether|on)|ask|follow up|look into|think about|"
                               r"come back to|touch base|sync (?:up )?on|discuss)\b", re.IGNORECASE)
DELIVERABLE_VERBS = re.compile(r"\b(?:send|write|draft|update|prepare|share|create|build|fix|ship|review|schedule|"
                               r"book|own|file|set up|publish|deliver|finish|complete|document|test|deploy|sign|order|"
                               r"buy|present|run|migrate|implement|design|merge|launch|submit|post|email|invite|"
                               r"compile|put together|hire|close|refund|approve)\b", re.IGNORECASE)
_REQUEST = re.compile(r"\b(?:can|could|would|will) you\b|\bplease\b|\bare you able to\b|\bmind (?:taking|owning)\b",
                      re.IGNORECASE)
_ACCEPT = re.compile(r"^\W*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|on it|will do|happy to|absolutely|definitely|got it|"
                     r"consider it done|no problem|of course|i'?ll take (?:it|that|this)|i can|i'?ll do (?:it|that)|"
                     r"i'?ll own (?:it|that)|i'?ll handle (?:it|that)|i'?m on it|count me in|can do)\b", re.IGNORECASE)


def first_name(name: str) -> str:
    return (name.split() or [""])[0].casefold()


def match_speaker(name: str, speakers: Sequence[str]) -> str | None:
    """The speaker `name` refers to: the full name (casefold), or a first name that only one speaker has."""
    wanted = " ".join(name.split()).casefold()
    if not wanted:
        return None
    for speaker in speakers:
        if speaker.casefold() == wanted:
            return speaker
    by_first = [s for s in speakers if first_name(s) == wanted]
    return by_first[0] if len(by_first) == 1 else None


def addressee(turn: Turn, speakers: Sequence[str]) -> str | None:
    """The attendee a request in this turn is addressed to, when it names exactly one other speaker."""
    if not _REQUEST.search(turn.text):
        return None
    norm = g.normalise(turn.text)
    named = {s for s in speakers if s != turn.speaker and (g.contains_words(s, norm) or g.contains_words(
        first_name(s), norm))}
    return next(iter(named)) if len(named) == 1 else None


def handoff(index: int, turns: Sequence[Turn], speakers: Sequence[str]) -> tuple[str, str] | None:
    """("accepted", name) / ("unaccepted", name) for a request to a named attendee, else None."""
    person = addressee(turns[index], speakers)
    if person is None:
        return None
    for later in turns[index + 1:index + 1 + HANDOFF_WINDOW]:
        if later.speaker == person and _ACCEPT.match(later.text):
            return "accepted", person
    return "unaccepted", person


def resolve(owner_ref: object, owner_name: str, quote: str, index: int, turns: Sequence[Turn],
            speakers: Sequence[str]) -> tuple[str, bool | None]:
    """(owner, accepted): `accepted` is True/False for a hand-off, None otherwise."""
    hand = handoff(index, turns, speakers)
    if hand is not None:
        return (hand[1], True) if hand[0] == "accepted" else (NOT_STATED, False)
    turn = turns[index]
    if owner_ref == "speaker":
        unknown = turn.speaker == UNKNOWN or is_system(turn.speaker)
        return (NOT_STATED if unknown else turn.speaker), None
    if owner_ref != "named" or not owner_name or owner_name.casefold().strip(" .") in GROUP_WORDS:
        return NOT_STATED, None
    spoken = match_speaker(owner_name, speakers)
    if spoken:
        return spoken, None
    nearby = " ".join(t.text for t in _window(index, turns))
    return (owner_name if g.contains_words(owner_name, g.normalise(f"{quote} {nearby}")) else NOT_STATED), None


def grounded_due(due: str, quote: str, index: int, turns: Sequence[Turn]) -> str:
    if not due or due.casefold().strip() in ("null", "none", "n/a", "not stated", "-"):
        return ""
    nearby = g.normalise(" ".join([quote, *(t.text for t in _window(index, turns))]))
    return due if g.contains_words(due, nearby) else ""


def kind_of(model_kind: object, task: str) -> str:
    """The model's kind, except that an "action" worded as a check-in with no deliverable is a follow-up.
    Code never turns a follow-up into an action."""
    if model_kind == "follow_up":
        return "follow_up"
    if FOLLOW_UP_PHRASES.search(task) and not DELIVERABLE_VERBS.search(task):
        return "follow_up"
    return "action"


def is_mine(owner: str, my_name: str, names: Sequence[str]) -> bool | None:
    """None when the user has not said who they are."""
    if not my_name:
        return None
    if owner == NOT_STATED or not owner:
        return False
    if owner.casefold() == my_name.casefold():
        return True
    if len(my_name.split()) == 1 and first_name(owner) == my_name.casefold():
        return sum(1 for n in {*names, owner} if first_name(n) == my_name.casefold()) == 1
    return False


def _window(index: int, turns: Sequence[Turn]) -> Sequence[Turn]:
    return turns[max(0, index - HANDOFF_WINDOW):index + HANDOFF_WINDOW + 1]
