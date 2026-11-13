"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                           → canned intro (no call)
    a thread (inputs.is_thread) ┬─ over the size cap      → pause: most recent part or cancel
                          └─ otherwise              → summarize (extract → check → compose → check)
    an instruction line + a pasted body too short to be a thread → "too short", with the minimum
    short and plainly out of scope (blame, sums, replying to the customer) → fixed reply (no call)
    short, a result stored ─┬─ view / who said X    → render (no call)
                            ├─ a question           → one quote-checked call
                            └─ shorter / for <who>  → compose again from the stored facts (one call)
    short, nothing stored ─┬─ "summarize this" alone → pause: paste the thread
                           ├─ a question or task     → one scope-checked call (declined or "no thread yet")
                           └─ otherwise              → "short enough to read as is" (no call)
"""

from __future__ import annotations

from dataclasses import dataclass

import followup
import pauses
import prompts
import smalltalk
import style
from inputs import (
    MAX_THREAD_CHARS,
    Incoming,
    assemble,
    envelope_as_thread,
    has_pasted_body,
    is_thread,
    keep_tail,
    split_request,
    without_json_words,
    word_count,
)

SHORT_REQUEST_MAX_WORDS = 12
CUT_TO_CAP = f"That was over my size limit, so I summarized the most recent {MAX_THREAD_CHARS:,} characters."


@dataclass(frozen=True)
class Plan:
    route: str                      # reply | pause | summarize | view | who | ask | restyle
    reply: str = ""
    pending: pauses.Pending | None = None
    thread: str = ""
    instruction: str = ""
    view: str = "default"
    kinds: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, has_result: bool) -> Plan:
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, notes=incoming.notes)
    text = envelope_as_thread(without_json_words(incoming.text))
    instruction, pasted = split_request(text)
    thread = prompts.strip_markers(assemble(pasted, incoming.files))
    if is_thread(thread):
        return _new_thread(thread, prompts.strip_markers(instruction), incoming.notes)
    declined = smalltalk.decline(incoming.text)
    if declined is not None:
        return Plan("reply", declined, notes=incoming.notes)
    if has_pasted_body(text):
        return Plan("reply", smalltalk.TOO_SHORT_REPLY, notes=incoming.notes)
    if has_result:
        return _on_stored(incoming)
    if followup.is_summarize_request(incoming.text) and word_count(incoming.text) <= SHORT_REQUEST_MAX_WORDS:
        pending = pauses.Pending(pauses.NEED_THREAD, instruction=prompts.strip_markers(incoming.text)[:300],
                                 notes=incoming.notes)
        return Plan("pause", pending=pending)
    if followup.is_question_like(incoming.text):
        return Plan("ask", notes=incoming.notes)
    return Plan("reply", smalltalk.TOO_SHORT_REPLY, notes=incoming.notes)


def plan_resume(answer: pauses.Answer) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "give_up":
        return Plan("reply", prompts.NO_THREAD_GIVEN_UP)
    if answer.action == "reask":
        return Plan("pause", pending=answer.pending)
    thread = prompts.strip_markers(answer.thread)
    notes: tuple[str, ...] = ()
    if len(thread) > MAX_THREAD_CHARS:
        thread, notes = keep_tail(thread), (CUT_TO_CAP,)
    return Plan("summarize", thread=thread, instruction=answer.instruction, notes=notes)


def _new_thread(thread: str, instruction: str, notes: tuple[str, ...]) -> Plan:
    if len(thread) > MAX_THREAD_CHARS:
        pending = pauses.Pending(pauses.TOO_BIG, instruction=instruction, thread=keep_tail(thread),
                                 chars=len(thread), notes=notes)
        return Plan("pause", pending=pending)
    return Plan("summarize", thread=thread, instruction=instruction, notes=notes)


def _on_stored(incoming: Incoming) -> Plan:
    text = incoming.text
    view = followup.parse_view(text)
    if view is not None:
        return Plan("view", view=view, notes=incoming.notes)
    if incoming.wants_json and word_count(without_json_words(text)) <= 2:
        return Plan("view", view="json", notes=incoming.notes)
    kinds = followup.who_said(text)
    if kinds is not None:
        return Plan("who", kinds=kinds, notes=incoming.notes)
    if style.changes_style(text) and not followup.is_wh_question(text):
        return Plan("restyle", instruction=prompts.strip_markers(text)[:300], notes=incoming.notes)
    if followup.is_summarize_request(text) and word_count(text) <= 4:
        return Plan("view", notes=incoming.notes)
    return Plan("ask", notes=incoming.notes)
