"""Pure: follow-ups on the PRD rebuilt from the chat history, and whether a short message is a revision.

    "only open questions" / "what are the open questions?"   -> questions view
    "show sources"                                         -> every statement with its brief quote
    "shorter" / "one page"                                 -> re-render at 400 words
    "detailed" / "show all"                                -> re-render at 1,500 words
    "drop non-goal 2" / "drop R3" / "remove goal 1"        -> remove it, re-render
    "start over"                                           -> forget the earlier brief and PRD (recall stops there)
    "as json"                                              -> the envelope as a fenced JSON block
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from inputs import DETAILED_CAP, ONE_PAGE_CAP, word_count
from prd import Prd

REVISION_MAX_WORDS = 40
COMMAND_MAX_WORDS = 8

_QUESTIONS = re.compile(r"^(?:(?:show |list |give me )?(?:only|just) (?:the )?open questions|(?:what are )?"
                        r"(?:the )?open questions\??)$")
_SOURCES = re.compile(r"^(?:show|with|cite|give me)? ?(?:the )?sources?$|^where did (?:that|this) come from\??$")
_SHORTER = re.compile(r"^(?:make it |keep it )?(?:shorter|short|one page|one-pager|to one page|condense it)$")
_LONGER = re.compile(r"^(?:make it |show (?:it )?)?(?:detailed|longer|show all|in full|full)$")
_START_OVER = re.compile(r"^(?:start over|start again|reset|clear(?: it)?|new prd)$")
_JSON = re.compile(r"^(?:(?:show|give me|return) )?(?:it |the prd )?(?:as|in) json$|^json(?: only)?$")
_DROP = re.compile(r"^(?:drop|remove|delete) (?:the )?(non-?goal|goal|user|requirement|req|r|metric)\s*#?\s*(\d{1,2})$")
_REVISION_CUE = re.compile(r"^(?:make|add|remove|drop|delete|tighten|change|rewrite|revise|update|edit|rename|move|"
                           r"split|merge|expand|shorten|clarify|reword|replace|turn|mark|set|swap|fix|include|"
                           r"exclude|focus|refine|improve|sharpen)\b|\b(?:r\d+|requirement \d+|non-?goal \d+)\b")
_DROP_SECTIONS = {"non-goal": "non_goals", "nongoal": "non_goals", "goal": "goals", "user": "users",
                  "requirement": "requirements", "req": "requirements", "r": "requirements", "metric": "metrics"}


@dataclass(frozen=True)
class Command:
    kind: str                     # questions | sources | cap | drop | start_over | json
    cap: int = 0
    section: str = ""
    index: int = 0


def _plain(text: str) -> str:
    return " ".join((text or "").casefold().replace("please", "").strip(" .!").split())


def parse_command(text: str) -> Command | None:
    plain = _plain(text)
    if word_count(plain) > COMMAND_MAX_WORDS:
        return None
    if _QUESTIONS.match(plain):
        return Command("questions")
    if _SOURCES.match(plain):
        return Command("sources")
    if _SHORTER.match(plain):
        return Command("cap", cap=ONE_PAGE_CAP)
    if _LONGER.match(plain):
        return Command("cap", cap=DETAILED_CAP)
    if _START_OVER.match(plain):
        return Command("start_over")
    if _JSON.match(plain):
        return Command("json")
    dropped = _DROP.match(plain)
    if dropped:
        return Command("drop", section=_DROP_SECTIONS[dropped.group(1)], index=int(dropped.group(2)))
    return None


def is_revision(text: str) -> bool:
    """With a PRD stored: a short message, or one opening with a revision cue, revises it."""
    return word_count(text) < REVISION_MAX_WORDS or bool(_REVISION_CUE.search(_plain(text)[:80]))


def has_revision_cue(text: str) -> bool:
    """Reads as an edit to an existing PRD ("make R2 testable", "drop non-goal 1")."""
    return bool(_REVISION_CUE.search(_plain(text)[:80]))


def drop(prd: Prd, command: Command) -> Prd | None:
    """The PRD without the named entry (requirements by id, others by position), or None if absent."""
    if command.section == "requirements":
        wanted = f"R{command.index}"
        kept = tuple(r for r in prd.requirements if r.id != wanted)
        return prd.with_(requirements=kept) if len(kept) != len(prd.requirements) else None
    entries = list(getattr(prd, command.section))
    if not 1 <= command.index <= len(entries):
        return None
    del entries[command.index - 1]
    return prd.with_(**{command.section: tuple(entries)})
