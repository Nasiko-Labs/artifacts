"""Follow-ups on the stored ranking (parsed in code) and the scope check for short messages.

    "urgency first" / "impact first" / "quick wins first" / "equal weights"   -> re-rank, zero calls
    "move 3 up" / "pin 2 at top" / "put the audit first" / "unpin"           -> pins, zero calls
    "top 5" / "only drop or delegate" / "quick wins" / "show the numbers"    -> views, zero calls
    "as of 2026-11-12" / "as json"                                           -> re-date / envelope, zero calls
    "add: <task>"                                                            -> rate the new task only (1 call)
    "rate again"                                                             -> re-rate every task
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import dates
import prompts
from llm import LlmError, TurnDeps, call, load

log = logging.getLogger("prioritizer.followup")

OFF_TOPIC, NEEDS_TASKS, FOLLOWUP = "off_topic", "needs_tasks", "followup"

_PRESETS = (
    ("urgency", re.compile(r"\b(?:urgency|deadlines?|due dates?) (?:first|matters? (?:more|most))\b|\bby urgency\b",
                           re.IGNORECASE)),
    ("impact", re.compile(r"\b(?:impact|value) (?:first|matters? (?:more|most))\b|\bby impact\b", re.IGNORECASE)),
    ("quick_wins", re.compile(r"\bquick[- ]wins? first\b", re.IGNORECASE)),
    ("equal", re.compile(r"\bequal(?:ly)?(?: weights?| weighting| weighted)?\b|\bbalanced\b", re.IGNORECASE)),
    ("default", re.compile(r"\b(?:default|normal|original) (?:weights?|weighting|order)\b", re.IGNORECASE)),
)
_UNKNOWN_PRESET = re.compile(r"^\W*\w+(?: \w+)? first\W*$|\b(?:re-?weight|weights?)\b", re.IGNORECASE)
_PIN_NUMBER = re.compile(r"\b(?:move|bump|pin|put)\s+(?:#|number |no\.? |task )?(\d{1,2})\b", re.IGNORECASE)
_PIN_NAME = re.compile(r"\b(?:move|bump|pin|put)\s+(?:the\s+)?(.+?)\s+(?:up|to the top|at (?:the )?top|first|"
                       r"on top)\W*$", re.IGNORECASE)
_UNPIN = re.compile(r"\b(?:unpin|clear (?:the |my )?pins?|remove (?:the |my )?pins?)\b", re.IGNORECASE)
_TOP = re.compile(r"\btop (\d{1,2})\b", re.IGNORECASE)
_ACTIONS = re.compile(r"\bdrops? (?:or|and|/) delegat\w*\b|\bwhat (?:should|can|could) i (?:drop|delegate)\b|"
                      r"\bwhat to (?:drop|delegate)\b", re.IGNORECASE)
_QUICK = re.compile(r"\bquick[- ]wins?\b", re.IGNORECASE)
_NUMBERS = re.compile(r"\bshow (?:me )?(?:the )?(?:numbers|math|maths|scores|factors|formula|working)\b|"
                      r"\bfactor table\b|\bhow (?:did|do) you (?:score|rank)\b", re.IGNORECASE)
_JSON = re.compile(r"^\W*(?:(?:show|give|return|send)(?: me)?(?: it| this| that| the (?:result|ranking|list))? )?"
                   r"(?:as|in) json\W*$|^\W*json(?: only)?\W*$", re.IGNORECASE)
_ADD = re.compile(r"^\W*add(?: (?:a )?task)?\s*[:\-]\s*(.+)$", re.IGNORECASE | re.DOTALL)
_RERATE = re.compile(r"\b(?:rate|score) (?:them |these |it |everything )?again\b|\bre-?rate\b", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str     # preset | unknown_preset | pin | pin_name | unpin | top | actions | quick | numbers | as_of
    #               | json | add | rerate
    number: int = 0
    text: str = ""


def parse(text: str) -> Command | None:
    message = (text or "").strip()
    added = _ADD.match(message)
    if added:
        return Command("add", text=added.group(1).strip())
    if _RERATE.search(message):
        return Command("rerate")
    if _UNPIN.search(message):
        return Command("unpin")
    return _view(message) or _pin(message) or _preset(message)


def _view(message: str) -> Command | None:
    as_of = dates.parse_as_of(message)
    if as_of is not None:
        return Command("as_of", text=as_of.isoformat())
    if _NUMBERS.search(message):
        return Command("numbers")
    if _ACTIONS.search(message):
        return Command("actions")
    top = _TOP.search(message)
    if top:
        return Command("top", number=int(top.group(1)))
    if _JSON.match(message):
        return Command("json")
    return None


def _pin(message: str) -> Command | None:
    number = _PIN_NUMBER.search(message)
    if number:
        return Command("pin", number=int(number.group(1)))
    named = _PIN_NAME.search(message)
    if named and not _preset_name(message):
        return Command("pin_name", text=named.group(1).strip())
    return None


def _preset_name(message: str) -> str | None:
    return next((name for name, pattern in _PRESETS if pattern.search(message)), None)


def _preset(message: str) -> Command | None:
    name = _preset_name(message)
    if name:
        return Command("preset", text=name)
    if _QUICK.search(message):
        return Command("quick")
    if _UNKNOWN_PRESET.search(message):
        return Command("unknown_preset")
    return None


async def classify(deps: TurnDeps, text: str, policy: Sequence[str] = ()) -> str:
    """A short message that is not a command or a list: off topic, a request without tasks, or a question
    about the ranking. A failed call counts as needs_tasks (asking for tasks never answers an off-topic one)."""
    try:
        data: Any = load(await call(deps, prompts.classify_messages(text, policy), max_tokens=50))
    except LlmError as e:
        log.warning("scope check failed: %s", e)
        return NEEDS_TASKS
    status = data.get("status") if isinstance(data, dict) else None
    return status if status in (OFF_TOPIC, NEEDS_TASKS, FOLLOWUP) else NEEDS_TASKS
