"""Pure: decides what a turn is: small talk, a follow-up, a new runbook, a request for the missing source, or one
classify call for a short message that is none of these.

Nothing is stored between messages and no turn ever pauses. A follow-up is rebuilt from the service
description or transcript still in the platform's chat history (plus the `fill in` values the user sent after
it); when that text has been trimmed out of the history the agent says so and asks for it again."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import followup
import history
import mode
import prompts
import smalltalk
from inputs import (
    INSTRUCTION_MAX_CHARS,
    MAX_SOURCE_CHARS,
    MIN_SOURCE_WORDS,
    Incoming,
    split_request,
    word_count,
)

_DOMAIN = re.compile(r"\brun ?books?\b|\bplaybooks?\b|\bon-?call\b|\bincident\b|\boutage\b|\bservice\b|\brollback\b|"
                     r"\bmitigat\w*|\btriage\b|\bpostmortem\b|\bpost-mortem\b|\bsre\b", re.IGNORECASE)
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b", re.IGNORECASE)
CANCEL_MAX_WORDS = 3


@dataclass(frozen=True)
class Route:
    kind: str                                   # reply | run | followup | scope
    reply: str = ""
    source: str = ""
    instruction: str = ""
    mode: str = ""
    command: followup.Command | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    fills: tuple[followup.Command, ...] = ()    # earlier `fill in` commands, replayed on a rebuilt runbook


@dataclass(frozen=True)
class Earlier:
    """The most recent source the user pasted that is still in the history, and what they sent after it."""
    source: str
    instruction: str
    mode: str
    fills: tuple[followup.Command, ...]


def plan_turn(incoming: Incoming) -> Route:
    text, notes = incoming.text, incoming.notes
    asked = _asked_for_source(incoming.history)
    if not incoming.files:
        canned = smalltalk.reply_to(text)
        if canned is not None:
            return Route("reply", reply=_with(canned, notes))
        if asked and _CANCEL.match(text.strip()) and word_count(text) <= CANCEL_MAX_WORDS:
            return Route("reply", reply=_with(prompts.CANCELLED, notes))
        command = followup.parse(text)
        if command is not None:
            routed = _plan_followup(command, incoming)
            if routed is not None:
                return routed
    instruction = _earlier_ask(incoming.history) if asked else ""
    return plan_source(text, incoming, notes, instruction)


def _plan_followup(command: followup.Command, incoming: Incoming) -> Route | None:
    earlier = earlier_source(incoming.history)
    if earlier is not None:
        return Route("followup", command=command, source=earlier.source, instruction=earlier.instruction,
                     mode=earlier.mode, notes=incoming.notes, fills=earlier.fills)
    if command.kind == "fill":  # "fill" may just be prose; let it fall through
        return None
    reply = prompts.SOURCE_TRIMMED if incoming.history else smalltalk.NO_RUNBOOK_YET
    return Route("reply", reply=_with(reply, incoming.notes))


def plan_source(text: str, incoming: Incoming, notes: tuple[str, ...], instruction: str = "") -> Route:
    request = split_request(text, incoming.files)
    source = prompts.strip_markers(request.source)
    if len(source) > MAX_SOURCE_CHARS:
        cut = (f"Your text was {len(source):,} characters; I covered the first {MAX_SOURCE_CHARS:,} and "
               "skipped the rest.")
        notes = (*notes, cut)
        source = source[:MAX_SOURCE_CHARS]
    if word_count(source) >= MIN_SOURCE_WORDS:
        forced = mode.TRANSCRIPT if request.from_envelope else None
        asks = [a for a in (instruction, request.instruction) if a]
        return Route("run", source=source, instruction="\n".join(dict.fromkeys(asks)),
                     mode=mode.detect(source, forced), notes=notes)
    if _DOMAIN.search(text):
        return Route("reply", reply=_with(prompts.ASK_SOURCE, notes))
    return Route("scope", notes=notes)


def earlier_source(turns: tuple[history.Turn, ...]) -> Earlier | None:
    """The newest user turn long enough to write a runbook from, with the fills sent after it; None when the
    history no longer holds one (never seen, or trimmed away)."""
    fills: list[followup.Command] = []
    for text in history.user_texts(turns):
        command = followup.parse(text)
        if command is not None:
            if command.kind == "fill":
                fills.append(command)
            continue
        request = split_request(text)
        source = prompts.strip_markers(request.source)[:MAX_SOURCE_CHARS]
        if word_count(source) >= MIN_SOURCE_WORDS:
            forced = mode.TRANSCRIPT if request.from_envelope else None
            return Earlier(source, request.instruction, mode.detect(source, forced), tuple(reversed(fills)))
    return None


def _asked_for_source(turns: tuple[history.Turn, ...]) -> bool:
    return history.last_assistant(turns).startswith(prompts.TELL_ME_MORE)


def _earlier_ask(turns: tuple[history.Turn, ...]) -> str:
    """The short request this agent last asked a source for, kept as the instruction for the source that
    answers it (the sensible default: the user is completing what they asked)."""
    ask = prompts.strip_markers(history.user_before_last_assistant(turns)).strip()
    return ask[:INSTRUCTION_MAX_CHARS] if word_count(ask) < MIN_SOURCE_WORDS else ""


def _with(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(p for p in (text, *notes) if p)
