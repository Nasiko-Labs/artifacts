"""Pure: decide what one turn does, before any model call.

    greeting / small talk                         -> canned intro (no call)
    a paste (3+ lines, JSON, a file) ─┬─ reply column unclear      -> pause Q2
                                      ├─ over 2,000 replies        -> pause Q1
                                      ├─ fewer than 10 replies     -> fixed reply (no call)
                                      └─ otherwise                 -> analyse
    one line, a result stored ── a follow-up command               -> followup (code; a redraft is 1 call)
    one line about replies/macros, nothing stored                  -> "paste your replies" (no call)
    anything else                                                  -> one scope-check call
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import followup
import pauses
import prompts
import smalltalk
from candidates import MIN_DEFAULT, MIN_RANGE, TOP_DEFAULT, TOP_RANGE, clamp
from inputs import Incoming
from parse_replies import Reply, column_roles, parse

MIN_REPLIES = 10
INSTRUCTION_MAX_CHARS = 300
PASTE_MIN_LINES = 3
_REQUEST = re.compile(r"\b(?:macros?|repl(?:y|ies)|answers?|recurring|repeated|find|which|analy[sz]e|review|"
                      r"here(?:'s| is| are)|these|min|top)\b|[?:]\s*$", re.IGNORECASE)
_ABOUT_US = re.compile(r"\b(?:macros?|replies|canned|saved repl|recurring answers?|repeated answers?)\b", re.IGNORECASE)
_SEPARATOR_OR_HEADER = re.compile(r"^\s*(?:-{3,}|={3,}|(?:reply|agent|response)\s*#?\d*\s*:)", re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    route: str                                  # reply | pause | analyse | followup | scope
    reply: str = ""
    pending: pauses.Pending | None = None
    replies: tuple[Reply, ...] = ()
    macros: tuple[str, ...] = ()
    min_freq: int = MIN_DEFAULT
    top: int = TOP_DEFAULT
    command: followup.Command | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def split_request(text: str) -> tuple[str, str]:
    """(instruction, data): a short first line that reads as a request, not a CSV header or a reply."""
    stripped = (text or "").strip()
    first, _, rest = stripped.partition("\n")
    head = first.strip()
    if (not rest.strip() or len(head) > INSTRUCTION_MAX_CHARS or head.startswith(("[", "{"))
            or _SEPARATOR_OR_HEADER.match(head) or _is_header(head)):
        return "", stripped
    return (head, rest.strip()) if _REQUEST.search(head) else ("", stripped)


def _is_header(line: str) -> bool:
    delimiter = next((d for d in ("\t", ",", ";", "|") if d in line), None)
    cells = [c.strip() for c in line.split(delimiter)] if delimiter else []
    if len(cells) < 2 or any(len(c) > 40 for c in cells):
        return False
    roles, ambiguous = column_roles(cells, None)
    return bool(roles or ambiguous)


def settings(instruction: str, lines: tuple[tuple[str, int], ...]) -> tuple[int | None, int | None]:
    command = followup.parse(instruction) if instruction else None
    min_freq = command.min_freq if command else None
    top = command.top if command else None
    for name, value in lines:
        if name == "min":
            min_freq = value
        elif name == "top":
            top = value
    return min_freq, top


def plan_turn(incoming: Incoming, has_stored: bool) -> Plan:
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, notes=incoming.notes)
    instruction, data = split_request(incoming.paste)
    lines = [line for line in data.splitlines() if line.strip()]
    if incoming.files or len(lines) >= PASTE_MIN_LINES or data.startswith(("[", "{")):
        return _paste(prompts.strip_markers(data), instruction, incoming.notes)
    if has_stored:
        command = followup.parse(incoming.text)
        if command is not None:
            return Plan("followup", command=command, notes=incoming.notes)
    if _ABOUT_US.search(incoming.text) and not has_stored:
        return Plan("reply", smalltalk.NO_REPLIES_YET, notes=incoming.notes)
    return Plan("scope", notes=incoming.notes)


def _paste(data: str, instruction: str, notes: tuple[str, ...]) -> Plan:
    parsed = parse(data)
    min_freq, top = settings(instruction, parsed.settings)
    if parsed.ambiguous:
        pending = pauses.Pending(pauses.COLUMN, paste=data[:pauses.MAX_KEPT_CHARS], candidates=parsed.ambiguous,
                                 min_freq=min_freq, top=top, notes=notes)
        return Plan("pause", pending=pending)
    return plan_replies(parsed.replies, parsed.macros, min_freq, top, notes)


def plan_replies(replies: tuple[Reply, ...], macros: tuple[str, ...], min_freq: int | None, top: int | None,
                 notes: tuple[str, ...]) -> Plan:
    if len(replies) > pauses.MAX_REPLIES:
        kept, cut = pauses.first_replies(replies)
        extra = (f"(Long replies are cut to {len(kept[0]['text']):,} characters for this run.)",) if cut else ()
        pending = pauses.Pending(pauses.TOO_BIG, replies=kept, macros=macros, total=len(replies), min_freq=min_freq,
                                 top=top, notes=(*notes, *extra))
        return Plan("pause", pending=pending)
    if len(replies) < MIN_REPLIES:
        return Plan("reply", prompts.TOO_FEW, notes=notes)
    return Plan("analyse", replies=replies, macros=macros, min_freq=clamp(min_freq, MIN_DEFAULT, MIN_RANGE),
                top=clamp(top, TOP_DEFAULT, TOP_RANGE), notes=notes)


def plan_resume(answer: pauses.Answer, pending: pauses.Pending) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if pending.kind == pauses.COLUMN:
        parsed = parse(pending.paste, reply_column=answer.column)
        return plan_replies(parsed.replies, parsed.macros, pending.min_freq, pending.top, ())
    note = f"Analysed the first {len(answer.replies):,} of {pending.total:,} replies."
    return plan_replies(answer.replies, pending.macros, pending.min_freq, pending.top, (*pending.notes, note))
