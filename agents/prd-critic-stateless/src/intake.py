"""Pure: decide what one turn does, before any LLM call. Nothing ever pauses: every route ends the task.

    greeting / small talk                         -> canned intro (no call)
    asks to write or rewrite a PRD                -> decline + intro (no call)
    PRD over 60,000 chars                         -> review the first 60,000, say what was left out
    PRD of 150+ words                             -> review (no headings at all: as one block, and say so)
    a PRD or review in history, short message ─┬─ follow-up      -> followup
                                               ├─ asks to review -> review the PRD from history again
                                               └─ otherwise      -> scope check (decline or follow-up help)
    short PRD-like text (headings, or 60+ words)  -> fixed "too short" reply (no call)
    short, a follow-up view, nothing in history   -> "no review yet, paste 150+ words", or "paste it again" when
                                                     history may have been trimmed (no call)
    short, asks for a review                      -> ask for the PRD (no call)
    short, anything else                          -> scope check (decline, or ask for the PRD)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import prompts
import smalltalk
from history import ASSISTANT
from inputs import Incoming, split_request, word_count
from recall import MIN_PRD_WORDS, Prior, cut
from sections import find_headings, number_lines

PRD_LIKE_MIN_WORDS = 60
REVIEW_ONLY_VIEWS = ("blockers", "breakdown", "json", "sections", "all", "versions")
_AGAIN = re.compile(r"\b(?:review|critique|score|grade|assess|check)\b.*\b(?:again|it|this|that)\b|\bre-?review\b",
                    re.IGNORECASE)
_ASKS_FOR_REVIEW = re.compile(r"\b(?:review|critique|critic|score|grade|assess|check)\b.*\b(?:prd|spec|requirements?|"
                              r"doc(?:ument)?)\b|\bprd\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                               # reply | review | followup | scope
    reply: str = ""
    prd: str = ""
    instruction: str = ""
    one_block: bool = False
    command: followup.Command | None = None
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, prior: Prior) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    request = split_request(incoming.text)
    body, left_out = cut(prompts.strip_markers(request.prd))
    words = word_count(body)
    if words >= MIN_PRD_WORDS:
        notes = (prompts.cut_note(left_out),) if left_out else ()
        return review(body, request.instruction, notes)
    if smalltalk.asks_to_write(incoming.text):
        return Route("reply", smalltalk.WRITE_REPLY)
    if prior.prd or prior.envelope or prior.review_text:
        command = followup.parse(incoming.text)
        if command:
            return Route("followup", command=command)
        if prior.prd and (_ASKS_FOR_REVIEW.search(incoming.text) or _AGAIN.search(incoming.text)):  # "review it again, lenient": the PRD in history
            instruction = request.instruction or incoming.text
            return review(prior.prd.text, instruction[:300], (prompts.FROM_HISTORY_NOTE,))
        return Route("scope")
    if find_headings(number_lines(body)) or words >= PRD_LIKE_MIN_WORDS:
        return Route("reply", prompts.TOO_SHORT)
    if _is_review_view(incoming.text):  # a view of a review that is not in this turn's history
        return Route("reply", prompts.NOT_IN_HISTORY if _review_may_be_trimmed(incoming) else prompts.NO_REVIEW_YET)
    if _ASKS_FOR_REVIEW.search(incoming.text):
        return Route("reply", ask_for_prd(incoming))
    return Route("scope")


def review(prd: str, instruction: str, notes: tuple[str, ...] = ()) -> Route:
    """A review route; a PRD with no headings at all is reviewed as one block (the model locates sections)."""
    if find_headings(number_lines(prd)):
        return Route("review", prd=prd, instruction=instruction, notes=notes)
    return Route("review", prd=prd, instruction=instruction, one_block=True, notes=(*notes, prompts.ONE_BLOCK_NOTE))


def ask_for_prd(incoming: Incoming) -> str:
    """No PRD to work on: with earlier turns it was trimmed from what the platform sent, so say so."""
    return prompts.NOT_IN_HISTORY if incoming.history else prompts.NEED_PRD


def _is_review_view(text: str) -> bool:
    """A follow-up that only makes sense on a review ("only the blockers", "score breakdown", "as json").
    Topic words a general question can share ("metrics", "requirements", "questions") go to the scope check."""
    command = followup.parse(text)
    return command is not None and command.kind in REVIEW_ONLY_VIEWS


def _review_may_be_trimmed(incoming: Incoming) -> bool:
    """True when history may have lost a PRD or review; False when it plainly shows none was made yet (no
    earlier turns, or the newest reply is one of this agent's own no-result replies)."""
    if not incoming.history:
        return False
    replies = [t.text for t in incoming.history if t.role == ASSISTANT]
    return not replies or not any(marker in replies[-1] for marker in prompts.NO_RESULT_MARKERS)
