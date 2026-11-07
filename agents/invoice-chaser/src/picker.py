"""Pure: the optional "which reminders should I draft?" multi-select after a list, and reading its answer.

Offered: invoices at levels 1-3, not auto-charge, not cooling down, not already sent or denied in this
conversation. Level 4 and cooling-down invoices can still be named ("draft for #2 anyway"). The answer's
lines are mapped back to invoices by their `#n` prefix in code; any other line is an instruction."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import ladder
from invoices import Entry
from nasiko_hitl import MAX_OPTION_LABEL_LEN, question_metadata
from render import View

METADATA_KEY = "invoice_chaser_pick"
MAX_OPTIONS = 10
HEADER = "Which reminders should I draft?"
NOTHING_PICKED = "There is nothing to do for that request."
FINAL_OUTCOMES = frozenset({"sent", "denied"})  # never drafted again in this conversation
_PICKED = re.compile(r"^\s*#(\d+)\b")


@dataclass(frozen=True)
class Pick:
    text: str
    hitl: dict
    metadata: dict


def draftable(entries: Sequence[Entry], view: View) -> list[Entry]:
    return [e for e in entries if not e.invoice.auto_charge and view.outcomes.get(e.key) not in FINAL_OUTCOMES
            and view.state(e).level < 4 and not view.state(e).cooling_down]


def question(entries: Sequence[Entry], view: View) -> Pick | None:
    offered = draftable(entries, view)[:MAX_OPTIONS]
    if not offered:
        return None
    options = [(_label(e, view), view.state(e).label) for e in offered]
    hitl = question_metadata(HEADER, options, multi_select=True, allow_custom_input=True)
    text = (f"{HEADER} Pick any (I'll draft them one at a time for your approval), type an instruction, "
            "or say skip.")
    return Pick(text, hitl, {METADATA_KEY: {"numbers": {e.id: e.key for e in offered}}})


def _label(e: Entry, view: View) -> str:
    inv = e.invoice
    label = (f"#{e.id} {inv.label} {inv.customer} {ladder.money(inv.amount_due_minor, inv.currency)} "
             f"({view.state(e).days_overdue}d)")
    return label.encode("utf-8")[:MAX_OPTION_LABEL_LEN].decode("utf-8", errors="ignore").strip()


def read_answer(answer: str, numbers: Mapping[str, str]) -> tuple[tuple[str, ...], str]:
    """(picked keys in order, the remaining lines as one instruction)."""
    keys: tuple[str, ...] = ()
    rest: tuple[str, ...] = ()
    for line in (answer or "").splitlines():
        match = _PICKED.match(line)
        if match and match.group(1) in numbers:
            keys = keys if numbers[match.group(1)] in keys else (*keys, numbers[match.group(1)])
        elif line.strip():
            rest = (*rest, line.strip())
    return keys, "\n".join(rest)
