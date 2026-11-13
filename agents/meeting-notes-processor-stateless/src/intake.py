"""Pure: decide what one turn does, before any LLM call. Nothing is stored between messages and nothing
ever pauses: every route ends the task `completed`. Earlier turns come only from the platform's history
block (`recall.py`).

    greeting / small talk                            -> canned intro (no call)
    >= 80 words of transcript ─┬─ over the size cap   -> process the first ~20k words, say what was left out
                               └─ otherwise           -> process
    "I'm Raj"                                        -> "mine" from the last notes reply, else process the
                                                        transcript in history, else "noted, paste it"
    view command ("only decisions", "as a table")    -> cut from the last notes reply (no call), else process
                                                        the transcript in history, else ask for a re-paste
    "process this" with no text                      -> the transcript in history, else ask to paste it
    a question                                       -> one quote-checked call over the transcript in
                                                        history, else over the last notes reply
    otherwise                                        -> "too short" (no call)
"""

from __future__ import annotations

from dataclasses import dataclass

import followup
import prompts
import recall
import smalltalk
from inputs import (
    MAX_SOURCE_CHARS,
    MIN_SOURCE_WORDS,
    Incoming,
    cut,
    my_name_in,
    name_only,
    word_count,
)

SHORT_REQUEST_MAX_WORDS = 12
ASK_NAME = 'Tell me who you are first (for example "I\'m Raj"), then ask for only mine.'
NEED_PASTE = (f"Paste the meeting transcript or notes (at least about {MIN_SOURCE_WORDS} words) in your message "
              "and I'll process it.")
REPASTE = ("That meeting is no longer in the part of our conversation I can see (long earlier inputs drop out), "
           "so paste the transcript again and I'll do that.")


@dataclass(frozen=True)
class Plan:
    route: str                     # reply | process | ask
    reply: str = ""
    source: str = ""               # process: the transcript; ask: the material to answer from
    instruction: str = ""
    my_name: str = ""
    view: str = "default"
    wants_json: bool = False
    notes: tuple[str, ...] = ()
    material: str = ""             # ask: "transcript", "notes" or "" (nothing to answer from)


def plan_turn(incoming: Incoming) -> Plan:
    wants, notes = incoming.wants_json, incoming.notes
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, notes=notes)
    instruction, source = source_of(incoming)
    if word_count(source) >= MIN_SOURCE_WORDS:
        my_name = my_name_in(instruction) or recall.my_name(incoming.history)
        source, cap_notes = within_cap(source)
        return Plan("process", source=source, instruction=instruction, my_name=my_name, wants_json=wants,
                    notes=(*cap_notes, *notes))
    name = "" if incoming.files else name_only(incoming.text)
    if name:
        return _set_name(incoming, name)
    view = followup.parse_command(incoming.text)
    if view is not None:
        return _view(incoming, view, wants or view == "json")
    if followup.is_process_request(incoming.text) and word_count(incoming.text) <= SHORT_REQUEST_MAX_WORDS:
        return _from_history(incoming, "default", wants, NEED_PASTE)
    if followup.is_question_like(incoming.text):
        return _ask(incoming)
    return Plan("reply", smalltalk.TOO_SHORT_REPLY, notes=notes)


def within_cap(source: str) -> tuple[str, tuple[str, ...]]:
    """The source cut to the size cap (from the start, at a line break), and a note saying what was left out."""
    if len(source) <= MAX_SOURCE_CHARS:
        return source, ()
    kept = cut(source, False)
    total, used = word_count(source), word_count(kept)
    note = (f"That was about {total:,} words, over my limit of about 20k, so I processed only the first "
            f"{used:,} words; the last {total - used:,} words were left out. Paste the rest in a new message "
            "to process it separately.")
    return kept, (note,)


def _name_of(incoming: Incoming) -> str:
    return my_name_in(incoming.text) or recall.my_name(incoming.history)


def _set_name(incoming: Incoming, name: str) -> Plan:
    shown = recall.view_from_history(incoming.history, "mine", name)
    if shown is not None:
        return Plan("reply", f"Noted: you're {name}.\n\n{shown}", notes=incoming.notes)
    instruction, source = recall.transcript(incoming.history)
    if source:
        source, cap_notes = within_cap(source)
        return Plan("process", source=source, instruction=instruction, my_name=name,
                    notes=(*cap_notes, *incoming.notes))
    return Plan("reply", f"Noted: you're {name}. Paste a meeting transcript and I'll mark your items.",
                notes=incoming.notes)


def _view(incoming: Incoming, view: str, wants: bool) -> Plan:
    name = _name_of(incoming)
    if view == "mine" and not name:
        return Plan("reply", ASK_NAME, notes=incoming.notes)
    shown = None if wants else recall.view_from_history(incoming.history, view, name)
    if shown is not None:
        return Plan("reply", shown, notes=incoming.notes)
    return _from_history(incoming, view, wants, REPASTE)


def _from_history(incoming: Incoming, view: str, wants: bool, otherwise: str) -> Plan:
    """Process the transcript still in the history and show `view`, or reply `otherwise`."""
    instruction, source = recall.transcript(incoming.history)
    if not source:
        return Plan("reply", otherwise, notes=incoming.notes)
    source, cap_notes = within_cap(source)
    return Plan("process", source=source, instruction=instruction, my_name=_name_of(incoming), view=view,
                wants_json=wants, notes=(*cap_notes, *incoming.notes))


def _ask(incoming: Incoming) -> Plan:
    _instruction, source = recall.transcript(incoming.history)
    if source:
        return Plan("ask", source=within_cap(source)[0], material="transcript", notes=incoming.notes)
    reply = recall.notes_reply(incoming.history)
    if reply:
        return Plan("ask", source=reply, material="notes", notes=incoming.notes)
    return Plan("ask", notes=incoming.notes)


def source_of(incoming: Incoming) -> tuple[str, str]:
    """(instruction, transcript) with fence markers stripped from both."""
    if incoming.files:
        files = "\n\n".join(text.strip() for _, text in incoming.files if text.strip())
        return prompts.strip_markers(incoming.text.strip())[:300], prompts.strip_markers(files)
    return recall.source_of_text(incoming.text)
