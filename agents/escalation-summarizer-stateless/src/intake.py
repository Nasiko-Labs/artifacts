"""Pure: decide what one turn does, before any LLM call. Nothing is stored between turns: earlier work is
whatever `recall.Prior` finds in the platform's chat history. No turn ever pauses.

    greeting / small talk                           → canned intro (no call)
    a thread (inputs.is_thread) ┬─ over the size cap      → summarize the most recent part, say what was left out
                          └─ otherwise              → summarize (extract → check → compose → check)
    an instruction line + a pasted body too short to be a thread → "too short", with the minimum
    short and plainly out of scope (blame, sums, replying to the customer) → fixed reply (no call)
    short, earlier work in the history ─┬─ view / who said X  → from the recalled thread (or the last reply)
                                        ├─ a question         → one quote-checked call
                                        └─ shorter / for <who> → compose again from the recalled thread
    short, nothing recalled ─┬─ "summarize this" alone → ask for the paste in the reply (task completes)
                             ├─ a follow-up command    → "paste it again" (history trimmed)
                             ├─ a question or task     → one scope-checked call
                             └─ otherwise              → "short enough to read as is" (no call)
"""

from __future__ import annotations

from dataclasses import dataclass

import followup
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
from recall import Prior

SHORT_REQUEST_MAX_WORDS = 12
CUT_TO_CAP = ("That was {chars:,} characters, over my {cap:,} limit, so I summarized the most recent {cap:,} "
              "and left out the earliest {left:,} characters. Paste an earlier part on its own to cover it.")


@dataclass(frozen=True)
class Plan:
    route: str                      # reply | summarize | view | who | ask | restyle
    reply: str = ""
    thread: str = ""
    instruction: str = ""
    view: str = "default"
    kinds: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, prior: Prior) -> Plan:
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, notes=incoming.notes)
    text = envelope_as_thread(without_json_words(incoming.text))
    instruction, pasted = split_request(text)
    thread = prompts.strip_markers(assemble(pasted, incoming.files))
    if is_thread(thread):
        return _new_thread(thread, prompts.strip_markers(instruction) or prior.asked_for, incoming.notes)
    declined = smalltalk.decline(incoming.text)
    if declined is not None:
        return Plan("reply", declined, notes=incoming.notes)
    if has_pasted_body(text):
        return Plan("reply", smalltalk.TOO_SHORT_REPLY, notes=incoming.notes)
    if prior.has_material:
        return _on_prior(incoming)
    if followup.is_summarize_request(incoming.text) and word_count(incoming.text) <= SHORT_REQUEST_MAX_WORDS:
        return Plan("reply", prompts.NEED_THREAD, notes=incoming.notes)
    if incoming.had_history and _is_command(incoming.text):
        return Plan("reply", prompts.NEED_REPASTE, notes=incoming.notes)
    if followup.is_question_like(incoming.text):
        return Plan("ask", notes=incoming.notes)
    return Plan("reply", smalltalk.TOO_SHORT_REPLY, notes=incoming.notes)


def _new_thread(thread: str, instruction: str, notes: tuple[str, ...]) -> Plan:
    if len(thread) > MAX_THREAD_CHARS:
        cut = CUT_TO_CAP.format(chars=len(thread), cap=MAX_THREAD_CHARS, left=len(thread) - MAX_THREAD_CHARS)
        return Plan("summarize", thread=keep_tail(thread), instruction=instruction, notes=(*notes, cut))
    return Plan("summarize", thread=thread, instruction=instruction, notes=notes)


def _is_command(text: str) -> bool:
    """A follow-up that only makes sense on an earlier summary."""
    return (followup.parse_view(text) is not None or followup.who_said(text) is not None
            or (style.changes_style(text) and not followup.is_wh_question(text)))


def _on_prior(incoming: Incoming) -> Plan:
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
