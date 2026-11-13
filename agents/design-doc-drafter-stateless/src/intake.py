"""Pure: decide what one turn does, before any LLM call. Nothing is stored between turns: earlier work is
whatever `recall.Prior` finds in the platform's chat history. No turn ever pauses.

    greeting / small talk                         -> canned intro (no call)
    a bare sum ("what's 2+2?")                    -> decline, whatever the history holds (no call)
    our last reply asked what you're designing    -> this message is appended to that request -> draft
    a draft recalled ─┬─ "as json" / "Qn: ..." / "add a requirement: ..."   -> followup (the crew again, then apply)
                      ├─ a new paste of requirements                       -> draft (a new document)
                      └─ anything else                                     -> followup (the crew again with the request)
    nothing recalled ─┬─ enough requirements        -> draft (25+ words, or 12+ in 3+ separate requirements)
                      ├─ a follow-up, history sent  -> "paste the requirements again" (trimmed)
                      ├─ "Qn: ..."                  -> "no design yet" (no call)
                      ├─ short, asks for a design   -> "too short, the minimum" when requirements were pasted,
                      │                                else ask what you're designing (no call)
                      └─ short, anything else       -> scope check (decline, or the same reply)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import prompts
import smalltalk
from inputs import Incoming, Request, enough_to_draft, split_request, word_count
from recall import Prior

NEW_PASTE_MIN_WORDS = 60
NEW_PASTE_MIN_LINES = 3

_ASKS_FOR_DESIGN = re.compile(r"design doc|design document|architecture|\badr\b|decision record|\brfc\b|"
                              r"requirements|system design|design for", re.IGNORECASE)


_ARITHMETIC = re.compile(r"^\W*(?:(?:what(?:'s| is)|how much is|calculate|compute|solve)\s+)?\(?\s*-?\d[\d.,]*"
                         r"(?:\s*(?:[-+*/x×÷^]|plus|minus|times|divided by)\s*\(?\s*-?\d[\d.,]*\s*\)?)+"
                         r"\s*[?=!.]*\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                                  # reply | draft | followup | scope
    reply: str = ""
    request: Request | None = None
    command: followup.Command | None = None


def plan_turn(incoming: Incoming, prior: Prior) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    if is_arithmetic(incoming.text):  # off topic whatever the history holds: never a follow-up on a draft
        return Route("reply", smalltalk.OFF_TOPIC_REPLY)
    if prior.asked_for:
        joined = split_request(f"{prior.asked_for}\n{incoming.text}")
        if enough_to_draft(joined):
            return Route("draft", request=joined)
    request = split_request(incoming.text)
    words = word_count(request.requirements)
    if prior.has_doc:
        command = followup.parse(incoming.text)
        if command.kind != "revise":
            return Route("followup", command=command)
        if _new_paste(request, words):
            return Route("draft", request=request)
        return Route("followup", command=command)
    if enough_to_draft(request):
        return Route("draft", request=request)
    if incoming.had_history and followup.parse(incoming.text).kind in ("json", "answers", "add"):
        return Route("reply", prompts.NEED_REPASTE)
    if followup.parse_answers(incoming.text):
        return Route("reply", prompts.NO_DOC_YET)
    if _ASKS_FOR_DESIGN.search(incoming.text):
        pasted = request.instruction and request.requirements.strip()
        return Route("reply", prompts.too_short(words) if pasted else prompts.ASK_REQUIREMENTS)
    return Route("scope")


def is_arithmetic(text: str) -> bool:
    """A bare sum ("what's 2+2?", "17 * 3"): arithmetic is out of scope, so it is declined without a call."""
    return bool(_ARITHMETIC.match(text or ""))


def _new_paste(request: Request, words: int) -> bool:
    lines = [line for line in request.requirements.splitlines() if line.strip()]
    if not enough_to_draft(request):
        return False
    return words >= NEW_PASTE_MIN_WORDS or len(lines) >= NEW_PASTE_MIN_LINES or bool(request.instruction)
