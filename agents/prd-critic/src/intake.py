"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                         -> canned intro (no call)
    asks to write or rewrite a PRD                -> decline + intro (no call)
    PRD over 60,000 chars                         -> fixed reply (no call)
    PRD of 150+ words with headings               -> review
    PRD of 150+ words with no headings at all     -> pause Q2: review as one block / cancel
    a review stored, short message ─┬─ follow-up  -> followup (no call)
                                    └─ otherwise  -> scope check (decline or follow-up help)
    short PRD-like text (headings, or 60+ words)  -> fixed "too short" reply (no call)
    no review stored, a review-only view          -> "no review yet" (no call): "only the blockers", "as json"...
    short, asks for a review                      -> pause Q1: paste the PRD (no call)
    short, anything else                          -> scope check (decline, or pause for the PRD)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import pauses
import prompts
import smalltalk
from inputs import Incoming, split_request, word_count
from sections import find_headings, number_lines

PRD_LIKE_MIN_WORDS = 60
# Views that only make sense on a review. Topic words a general question can share ("metrics", "requirements",
# "questions") are left out: those messages still go to the scope check.
REVIEW_ONLY_VIEWS = ("blockers", "breakdown", "json", "sections", "all", "versions")
_ASKS_FOR_REVIEW = re.compile(r"\b(?:review|critique|critic|score|grade|assess|check)\b.*\b(?:prd|spec|requirements?|"
                              r"doc(?:ument)?)\b|\bprd\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                               # reply | pause | review | followup | scope
    reply: str = ""
    pending: pauses.Pending | None = None
    prd: str = ""
    instruction: str = ""
    one_block: bool = False
    command: followup.Command | None = None


def plan_turn(incoming: Incoming, has_result: bool) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    request = split_request(incoming.text)
    body = prompts.strip_markers(request.prd)
    words = word_count(body)
    if len(body) > pauses.MAX_PRD_CHARS:
        return Route("reply", prompts.TOO_LONG)
    if words >= pauses.MIN_PRD_WORDS:
        if not find_headings(number_lines(body)):
            pending = pauses.Pending(pauses.NO_SECTIONS, request.instruction, body, incoming.wants_json)
            return Route("pause", pending=pending)
        return Route("review", prd=body, instruction=request.instruction)
    if smalltalk.asks_to_write(incoming.text):
        return Route("reply", smalltalk.WRITE_REPLY)
    if has_result:
        command = followup.parse(incoming.text)
        return Route("followup", command=command) if command else Route("scope")
    if find_headings(number_lines(body)) or words >= PRD_LIKE_MIN_WORDS:
        return Route("reply", prompts.TOO_SHORT)
    command = followup.parse(incoming.text)
    if command is not None and command.kind in REVIEW_ONLY_VIEWS:
        return Route("reply", prompts.NO_REVIEW_YET)
    if _ASKS_FOR_REVIEW.search(incoming.text):
        return Route("pause", pending=need_prd(incoming, request.instruction))
    return Route("scope")


def need_prd(incoming: Incoming, instruction: str = "") -> pauses.Pending:
    return pauses.Pending(pauses.NEED_PRD, prompts.strip_markers(instruction)[:300], wants_json=incoming.wants_json)


def plan_resume(answer: pauses.Answer) -> Route:
    if answer.action != "run":
        return Route("reply", answer.reply or prompts.CANCELLED)
    if not answer.one_block and not find_headings(number_lines(answer.prd)):
        pending = pauses.Pending(pauses.NO_SECTIONS, answer.instruction, answer.prd)
        return Route("pause", pending=pending)
    return Route("review", prd=answer.prd, instruction=answer.instruction, one_block=answer.one_block)
