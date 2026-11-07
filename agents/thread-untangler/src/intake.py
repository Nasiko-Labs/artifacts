"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                              → canned intro (no call)
    JSON with no thread field                          → "paste the thread text" (no call)
    a thread (3+ messages with headers, or 60+ words) ─┬─ over the size cap → pause: first / last / cancel
                                                       └─ otherwise         → untangle
    short, a thread stored ─┬─ "I'm Priya"              → recompute yours (no call)
                            ├─ re-render command         → render (no call)
                            └─ anything else             → one checked question
    short, nothing stored ─┬─ "untangle this" with nothing → pause: paste the thread
                           ├─ a question or task           → one scope call (declined or "no thread yet")
                           └─ otherwise                    → "short enough to read as is" (no call)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import envelope
import followup
import hitl
import owners
import prompts
import smalltalk
import thread as thread_parser
from inputs import (
    INSTRUCTION_MAX_CHARS,
    MAX_SOURCE_CHARS,
    MIN_SOURCE_WORDS,
    Incoming,
    assemble_source,
    envelope_thread,
    head_part,
    split_request,
    tail_part,
    word_count,
)

SHORT_REQUEST_MAX_WORDS = 12


@dataclass(frozen=True)
class Plan:
    route: str                     # reply | pause | untangle | name | rerender | ask | scope
    reply: str = ""
    pending: hitl.Pending | None = None
    source: str = ""
    instruction: str = ""
    command: followup.Command = field(default_factory=followup.Command)
    my_name: str = ""
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, has_stored: bool, speakers: Sequence[str] = ()) -> Plan:
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, notes=incoming.notes)
    wrapped = None if incoming.files else envelope_thread(incoming.text)
    if wrapped == "":
        return Plan("reply", prompts.NOT_AN_ENVELOPE, notes=incoming.notes)
    instruction, pasted = ("", wrapped) if wrapped else split_instruction(incoming.text)
    source = prompts.strip_markers(assemble_source(pasted, incoming.files))
    instruction = prompts.strip_markers(instruction)
    as_json = incoming.wants_json or envelope.wants_json(instruction)
    if len(source) > MAX_SOURCE_CHARS:
        pending = hitl.Pending(hitl.TOO_BIG, instruction=instruction, head=head_part(source), tail=tail_part(source),
                               words=word_count(source), as_json=as_json, notes=incoming.notes)
        return Plan("pause", pending=pending)
    if is_thread(source):
        return untangle_plan(source, instruction, as_json, incoming.notes)
    if has_stored:
        return _on_stored(incoming, speakers)
    if followup.is_untangle_request(incoming.text) and word_count(incoming.text) <= SHORT_REQUEST_MAX_WORDS:
        pending = hitl.Pending(hitl.NEED_THREAD, instruction=prompts.strip_markers(incoming.text)[:300],
                               as_json=as_json, notes=incoming.notes)
        return Plan("pause", pending=pending)
    if followup.is_question_like(incoming.text):
        return Plan("scope", notes=incoming.notes)
    return Plan("reply", smalltalk.TOO_SHORT_REPLY, notes=incoming.notes)


def plan_resume(answer: hitl.Answer, as_json: bool = False) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "give_up":
        return Plan("reply", prompts.NO_THREAD_GIVEN_UP)
    if answer.action == "reask":
        return Plan("pause", pending=answer.pending)
    source = prompts.strip_markers(answer.source)
    return untangle_plan(head_part(source), prompts.strip_markers(answer.instruction), as_json, ())


def untangle_plan(source: str, instruction: str, as_json: bool, notes: Sequence[str]) -> Plan:
    command = followup.parse_command(instruction) or followup.Command()
    command = followup.Command(command.view, command.sources, command.as_json or as_json)
    return Plan("untangle", source=source, instruction=instruction, command=command,
                my_name=owners.name_in_instruction(instruction), notes=tuple(notes))


def is_thread(source: str) -> bool:
    """At least 3 messages under recognised headers, or at least 60 words."""
    parsed = thread_parser.parse(source)
    headed = parsed.fmt != "paragraphs" and len(parsed.content) >= thread_parser.MIN_MESSAGES
    return headed or word_count(source) >= MIN_SOURCE_WORDS


def split_instruction(text: str) -> tuple[str, str]:
    """(instruction, thread). A short first paragraph is the instruction when it is long enough on its own
    (inputs.split_request), or when the rest parses as a thread and the paragraph does not."""
    instruction, rest = split_request(text)
    if instruction:
        return instruction, rest
    stripped = (text or "").strip()
    for separator in ("\n\n", "\n"):  # a request paragraph, or a request line right above the thread
        head, _, tail = stripped.partition(separator)
        if _head_adds_no_message(stripped, head, tail):
            return head.strip(), tail.strip()
    return "", stripped


def _head_adds_no_message(text: str, head: str, tail: str) -> bool:
    """True when `tail` parses as a headed thread and `head` adds no message to it: `head` is the request."""
    if not tail.strip() or len(head) > INSTRUCTION_MAX_CHARS:
        return False
    rest = thread_parser.parse(tail)
    return rest.fmt != "paragraphs" and len(thread_parser.parse(text).content) == len(rest.content)


def _on_stored(incoming: Incoming, speakers: Sequence[str]) -> Plan:
    name = owners.name_in_followup(incoming.text, speakers)
    if name:
        return Plan("name", my_name=name, notes=incoming.notes)
    command = followup.parse_command(incoming.text)
    if command is not None:
        if incoming.wants_json:
            command = followup.Command(command.view, command.sources, True)
        return Plan("rerender", command=command, notes=incoming.notes)
    return Plan("ask", notes=incoming.notes)
