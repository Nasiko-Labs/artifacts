"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                         → canned intro (no call)
    >= 80 words of log                            → write the postmortem
    short, a postmortem stored ─┬─ edit ("owner of a2 is Priya", "add an action item: ...") → no call
                                ├─ re-render command ("only the action items", "as json") → no call
                                └─ anything else  → one quote-checked question (or the off-topic decline)
    short, nothing stored ─┬─ "write the postmortem" with no log → pause: paste the log
                           ├─ a question or task  → one scope-checked call (declined, or "no log yet")
                           └─ otherwise           → "too short for a postmortem" (no call)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import hitl
import prompts
import smalltalk
from inputs import (
    INSTRUCTION_MAX_CHARS,
    MAX_SOURCE_CHARS,
    MIN_LOG_WORDS,
    Incoming,
    assemble,
    split_request,
    word_count,
)

SHORT_REQUEST_MAX_WORDS = 15
CUT_NOTE = "Your log was over my size limit, so this draft covers its first {chars:,} characters only."
_POSTMORTEM_WORDS = re.compile(r"post-?mortem|incident review|\bretro(?:spective)?\b|\brca\b", re.IGNORECASE)
_QUESTION_START = re.compile(r"^(?:what|who|whom|whose|when|where|why|how|which|is|are|was|were|do|does|did|can|"
                             r"could|should|would|will|tell|write|translate|explain|give|calculate|compute|list|"
                             r"name|define|describe|compose|draft|generate|create|make)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    route: str                              # reply | pause | write | rerender | edit | ask
    reply: str = ""
    pending: hitl.Pending | None = None
    log: str = ""
    instruction: str = ""
    view: str = "default"
    edit: followup.Edit | None = None
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, has_stored: bool) -> Plan:
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, notes=incoming.notes)
    instruction, pasted = split_request(incoming.text)
    if incoming.files and not instruction and len(pasted) <= INSTRUCTION_MAX_CHARS:
        instruction, pasted = pasted, ""  # a short message sent with a file is the request, not log text
    log, notes = cap(prompts.strip_markers(assemble(pasted, incoming.files)))
    notes = (*incoming.notes, *notes)
    if word_count(log) >= MIN_LOG_WORDS:
        return Plan("write", log=log, instruction=prompts.strip_markers(instruction), notes=notes)
    if has_stored:
        return _on_stored(incoming)
    if _POSTMORTEM_WORDS.search(incoming.text) and word_count(incoming.text) <= SHORT_REQUEST_MAX_WORDS:
        pending = hitl.Pending(instruction=prompts.strip_markers(incoming.text)[:300], wants_json=incoming.wants_json)
        return Plan("pause", pending=pending)
    if is_question_like(incoming.text):
        return Plan("ask", notes=incoming.notes)
    return Plan("reply", smalltalk.TOO_SHORT_REPLY, notes=incoming.notes)


def plan_resume(answer: hitl.Answer, pending: hitl.Pending) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "give_up":
        return Plan("reply", prompts.NO_SOURCE_GIVEN_UP)
    if answer.action == "reask":
        return Plan("pause", pending=answer.pending)
    instruction, pasted = split_request(answer.log)
    log, notes = cap(prompts.strip_markers(pasted))
    return Plan("write", log=log, instruction=prompts.strip_markers(instruction) or pending.instruction, notes=notes)


def cap(log: str) -> tuple[str, tuple[str, ...]]:
    """The log, cut on a line boundary when it is over MAX_SOURCE_CHARS, with a note saying so."""
    if len(log) <= MAX_SOURCE_CHARS:
        return log, ()
    head = log[:MAX_SOURCE_CHARS]
    cut = head.rfind("\n")
    head = head[:cut] if cut > MAX_SOURCE_CHARS // 2 else head
    return head, (CUT_NOTE.format(chars=len(head)),)


def is_question_like(text: str) -> bool:
    stripped = (text or "").strip()
    return stripped.endswith("?") or bool(_QUESTION_START.match(stripped))


def _on_stored(incoming: Incoming) -> Plan:
    edit = followup.parse_edit(incoming.text)
    if edit is not None:
        return Plan("edit", edit=edit, notes=incoming.notes)
    view = followup.parse_command(incoming.text)
    if view is not None:
        return Plan("rerender", view=view, notes=incoming.notes)
    return Plan("ask", notes=incoming.notes)
