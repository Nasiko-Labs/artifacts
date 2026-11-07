"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                         -> canned intro (no call)
    a draft stored ─┬─ "as json" / "Qn: ..." / "add a requirement: ..."   -> followup
                    ├─ a new paste of requirements                       -> draft (a new document)
                    └─ anything else                                     -> followup (one REVISE call)
    no draft ─┬─ requirements of 25+ words        -> draft
              ├─ "Qn: ..."                        -> "no design yet" (no call)
              ├─ short, asks for a design doc     -> pause: What are you designing? (no call)
              └─ short, anything else             -> scope check (decline, or the same pause)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import hitl
import prompts
import smalltalk
from inputs import Incoming, Request, split_request, word_count

NEW_PASTE_MIN_WORDS = 60
NEW_PASTE_MIN_LINES = 3

_ASKS_FOR_DESIGN = re.compile(r"design doc|design document|architecture|\badr\b|decision record|\brfc\b|"
                              r"requirements|system design|design for", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                                  # reply | pause | draft | followup | scope
    reply: str = ""
    pending: hitl.Pending | None = None
    request: Request | None = None
    command: followup.Command | None = None


def plan_turn(incoming: Incoming, has_doc: bool) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    request = split_request(incoming.text)
    words = word_count(request.requirements)
    if has_doc:
        command = followup.parse(incoming.text)
        if command.kind != "revise":
            return Route("followup", command=command)
        if _new_paste(request, words):
            return Route("draft", request=request)
        return Route("followup", command=command)
    if followup.parse_answers(incoming.text):
        return Route("reply", prompts.NO_DOC_YET)
    if words >= hitl.MIN_WORDS:
        return Route("draft", request=request)
    if _ASKS_FOR_DESIGN.search(incoming.text):
        return Route("pause", pending=need_requirements(incoming))
    return Route("scope")


def _new_paste(request: Request, words: int) -> bool:
    lines = [line for line in request.requirements.splitlines() if line.strip()]
    if words < hitl.MIN_WORDS:
        return False
    return words >= NEW_PASTE_MIN_WORDS or len(lines) >= NEW_PASTE_MIN_LINES or bool(request.instruction)


def need_requirements(incoming: Incoming) -> hitl.Pending:
    return hitl.Pending(hitl.NEED_REQUIREMENTS, _clip(incoming.text), incoming.wants_json)


def _clip(text: str) -> str:
    return (text or "").strip()[:2_000]


def plan_resume(answer: hitl.Answer) -> Route:
    if answer.action != "run":
        return Route("reply", prompts.CANCELLED)
    return Route("draft", request=split_request(answer.text))
