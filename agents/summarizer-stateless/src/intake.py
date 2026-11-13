"""Pure: decide what one turn does, before any LLM call. Nothing is stored between messages and nothing
ever pauses: every route ends the task `completed`. Earlier turns come only from the platform's history
block (`recall.py`).

    greeting / small talk                         -> canned intro (no call)
    >= 60 words of source  ─┬─ over the size cap   -> summarize the first ~20k words, say what was left out
                            ├─ refers back ("this one too") -> summarize this one on its own, and say so
                            └─ otherwise           -> summarize
    re-render command ("only the action items")   -> rebuilt from the last summary reply (no call), else
                                                     summarize the source in history, else ask for a re-paste
    "summarize this" with no text                 -> the last summary reply, else the source in history,
                                                     else ask to paste it
    a question                                    -> one quote-checked call over the source in history, else
                                                     over the last summary reply
    otherwise                                     -> "short enough to read as is" (no call)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import prompts
import recall
import smalltalk
from chunking import SINGLE_PASS_MAX_CHARS
from inputs import (
    MAX_SOURCE_CHARS,
    MIN_SOURCE_WORDS,
    Incoming,
    assemble_source,
    split_request,
    truncate_source,
    word_count,
)

SHORT_REQUEST_MAX_WORDS = 12
ON_ITS_OWN = ("I summarized this text on its own: I keep nothing between messages, so to cover it together "
              "with an earlier text, paste both in one message.")
NEED_PASTE = (f"Paste the text you want summarized (at least about {MIN_SOURCE_WORDS} words) in your message and "
              "I'll summarize it.")
REPASTE = ("That text is no longer in the part of our conversation I can see (long earlier inputs drop out), so "
           "paste it again and I'll do that.")
FIRST_PART_ASKED = "That text is long, so I answered from its first part only."
_REFERS_BACK = re.compile(r"\b(?:too|also|as well|together|combined?|along with|plus this|add(?:ed)? to|"
                          r"with the (?:earlier|previous|last|first|other) (?:one|text|summary|thread|call)|"
                          r"and this one)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    route: str                              # reply | summarize | ask
    reply: str = ""
    source: str = ""                        # summarize: the text; ask: the material to answer from
    instruction: str = ""
    view: tuple[str, bool] = ("default", False)
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming) -> Plan:
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, notes=incoming.notes)
    instruction, pasted = split_request(incoming.text)
    source = prompts.strip_markers(assemble_source(pasted, incoming.files))
    if word_count(source) >= MIN_SOURCE_WORDS:
        return _new_source(source, prompts.strip_markers(instruction), incoming.notes)
    command = followup.parse_command(incoming.text)
    if command is not None:
        return _view(incoming, command, REPASTE)
    if followup.is_summarize_request(incoming.text) and word_count(incoming.text) <= SHORT_REQUEST_MAX_WORDS:
        return _view(incoming, ("default", False), NEED_PASTE)
    if followup.is_question_like(incoming.text):
        return _ask(incoming)
    return Plan("reply", smalltalk.TOO_SHORT_REPLY, notes=incoming.notes)


def within_cap(source: str) -> tuple[str, tuple[str, ...]]:
    """The source cut to the size cap (at a paragraph or line break), and a note saying what was left out."""
    if len(source) <= MAX_SOURCE_CHARS:
        return source, ()
    kept = truncate_source(source)
    total, used = word_count(source), word_count(kept)
    note = (f"That was about {total:,} words, over my limit of about 20k, so I summarized only the first "
            f"{used:,} words; the last {total - used:,} words were left out. Paste the rest in a new message to "
            "summarize it separately.")
    return kept, (note,)


def _new_source(source: str, instruction: str, notes: tuple[str, ...]) -> Plan:
    kept, cap_notes = within_cap(source)
    refers_back = (ON_ITS_OWN,) if instruction and _REFERS_BACK.search(instruction) else ()
    return Plan("summarize", source=kept, instruction=instruction, notes=(*cap_notes, *refers_back, *notes))


def _view(incoming: Incoming, view: tuple[str, bool], otherwise: str) -> Plan:
    """`view` rebuilt from the last summary reply, else the source in history summarized, else `otherwise`."""
    shown = recall.view_from_history(incoming.history, *view)
    if shown is not None:
        return Plan("reply", shown, notes=incoming.notes)
    instruction, source = recall.source(incoming.history)
    if not source:
        return Plan("reply", otherwise, notes=incoming.notes)
    kept, cap_notes = within_cap(source)
    return Plan("summarize", source=kept, instruction=instruction, view=view, notes=(*cap_notes, *incoming.notes))


def _ask(incoming: Incoming) -> Plan:
    _instruction, source = recall.source(incoming.history)
    reply = recall.summary_reply(incoming.history)
    if source and (len(source) <= SINGLE_PASS_MAX_CHARS or not reply):
        notes = (FIRST_PART_ASKED,) if len(source) > SINGLE_PASS_MAX_CHARS else ()
        return Plan("ask", source=truncate_source(source, SINGLE_PASS_MAX_CHARS), notes=(*notes, *incoming.notes))
    return Plan("ask", source=reply, notes=incoming.notes)
