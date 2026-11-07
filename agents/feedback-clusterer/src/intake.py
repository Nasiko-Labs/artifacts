"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                          -> canned intro (no call)
    a paste (several lines, JSON, a file) ─┬─ text column ambiguous      -> pause Q1
                                           ├─ over 2,000 items / 100k     -> pause Q2
                                           ├─ fewer than 3 items          -> fixed reply (no call)
                                           └─ otherwise                   -> cluster (replace, or append on "add")
    one line, a result stored ─┬─ re-render command                       -> render (no call)
                               └─ anything else                           -> answer in code, else one scoped call
    one line, nothing stored ─┬─ asks to cluster but has no data          -> "paste your feedback" (no call)
                              └─ anything else                            -> one scope-checked call
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import filters
import followup
import hitl
import parse_items
import prompts
import smalltalk
from filters import Focus
from inputs import Incoming
from items import FeedbackItem, dedupe, numbered
from report import Stored

INSTRUCTION_MAX_CHARS = 300
_REQUEST = re.compile(r"\b(?:cluster|group|themes?|categori[sz]e|bucket|analy[sz]e|weight|sort|summari[sz]e|find|"
                      r"what|which|here(?:'s| is| are)|add|also|only|focus|as json|json only|as of)\b", re.IGNORECASE)
_APPEND = re.compile(r"\b(?:add|also|too|as well|append|plus these|more rows)\b", re.IGNORECASE)
_CLUSTER_ASK = re.compile(r"\b(?:cluster|group|themes?|categori[sz]e|bucket|feedback|tickets|reviews)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    route: str                                  # reply | pause | cluster | rerender | ask
    reply: str = ""
    pending: hitl.Pending | None = None
    items: tuple[FeedbackItem, ...] = ()
    focus: Focus = field(default_factory=Focus)
    command: followup.Command | None = None
    run: dict = field(default_factory=dict)     # duplicates, unparsed, column_default, notes
    notes: tuple[str, ...] = ()


def split_request(text: str) -> tuple[str, str]:
    """(instruction, data): a short first line that reads as a request, not as a header or an item."""
    stripped = (text or "").strip()
    first, _, rest = stripped.partition("\n")
    head = first.strip()
    if not rest.strip() or len(head) > INSTRUCTION_MAX_CHARS or head.startswith(("[", "{")) or _is_header(head):
        return "", stripped
    if head.endswith(":") or _REQUEST.search(head):
        return head.rstrip(":").strip(), rest.strip()
    return "", stripped


def _is_header(line: str) -> bool:
    delimiter = next((d for d in ("\t", ",", ";", "|") if d in line), None)
    cells = [c.strip() for c in line.split(delimiter)] if delimiter else []
    if len(cells) < 2 or any(len(c) > 40 for c in cells):
        return False
    roles, ambiguous = parse_items.column_roles(cells)
    return len(roles) + bool(ambiguous) >= 2


def plan_turn(incoming: Incoming, stored: Stored | None) -> Plan:
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, notes=incoming.notes)
    instruction, data = split_request(incoming.paste)
    if incoming.files or instruction or "\n" in data or data.startswith(("[", "{")):
        return _paste(prompts.strip_markers(data), prompts.strip_markers(instruction), stored, incoming.notes)
    if stored is not None:
        command = followup.parse_command(incoming.text, stored.focus)
        if command is not None:
            return Plan("rerender", command=command, notes=incoming.notes)
        return Plan("ask", notes=incoming.notes)
    if _CLUSTER_ASK.search(incoming.text) and not followup.is_question_like(incoming.text):
        return Plan("reply", smalltalk.NO_ITEMS_YET, notes=incoming.notes)
    return Plan("ask", notes=incoming.notes)


def _paste(data: str, instruction: str, stored: Stored | None, notes: tuple[str, ...]) -> Plan:
    parsed = parse_items.parse(data)
    append = stored is not None and bool(_APPEND.search(instruction))
    if parsed.ambiguous:
        pending = hitl.Pending(hitl.COLUMN, instruction=instruction, paste=data[:hitl.MAX_CHARS * 2],
                               candidates=parsed.ambiguous, append=append, notes=notes)
        return Plan("pause", pending=pending)
    return plan_items(parsed, instruction, stored if append else None, notes, len(data))


def plan_items(parsed: parse_items.Parsed, instruction: str, base: Stored | None, notes: tuple[str, ...],
               chars: int = 0) -> Plan:
    """Items -> the cluster plan (appended to `base` when given), or a cap question, or a fixed reply."""
    earlier = base.items if base is not None else ()
    items, duplicates = dedupe(numbered((*earlier, *parsed.items)))
    focus = filters.parse_focus(instruction)
    if base is not None and focus.is_empty:
        focus = base.focus
    run = {"duplicates": duplicates + int((base.run if base else {}).get("duplicates") or 0),
           "unparsed": parsed.unparsed, "column_default": parsed.column_default, "notes": list(parsed.notes)}
    if len(items) > hitl.MAX_ITEMS or chars > hitl.MAX_CHARS:
        recent = hitl.most_recent(items)
        pending = hitl.Pending(hitl.TOO_BIG, instruction=instruction, items=tuple(i.to_dict() for i in recent),
                               total=len(items), notes=notes)
        return Plan("pause", pending=pending)
    if len(items) < 3:
        return Plan("reply", prompts.TOO_FEW, notes=notes)
    return Plan("cluster", items=items, focus=focus, run=run, notes=notes)


def plan_resume(answer: hitl.Answer, pending: hitl.Pending, stored: Stored | None) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if pending.kind == hitl.COLUMN:
        parsed = parse_items.parse(pending.paste, text_column=answer.text_column)
        return plan_items(parsed, pending.instruction, stored if pending.append else None, ())
    focus = answer.focus or filters.parse_focus(pending.instruction)
    note = f"Used the most recent {len(answer.items):,} of {pending.total:,} items."
    run = {"duplicates": 0, "unparsed": 0, "column_default": False, "notes": [note]}
    return Plan("cluster", items=answer.items, focus=focus, run=run)
