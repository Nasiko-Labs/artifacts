"""Pure: the optional "which failures should I draft for?" multi-select after a list, and reading its
answer. Failures already emailed in the last three days, or already sent/denied in this conversation, are
not offered. The answer's lines are mapped back to failures by their `#n` prefix in code; any other line
is an instruction for the draft step. (Pattern copied from the hardened kit; owned by this agent.)"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import scoring
from failures import Failure
from nasiko_hitl import MAX_OPTION_LABEL_LEN, question_metadata
from render import View

METADATA_KEY = "payment_rescuer_pick"
MAX_OPTIONS = 10
HEADER = "Which failures should I draft recovery emails for?"
NOTHING_PICKED = "There is nothing to do for that request."
FINAL_OUTCOMES = frozenset({"sent", "denied"})  # never drafted again in this conversation
_PICKED = re.compile(r"^\s*#(\d+)\b")


@dataclass(frozen=True)
class Pick:
    text: str
    hitl: dict
    metadata: dict


def draftable(items: Sequence[Failure], view: View) -> list[Failure]:
    return [f for f in items if not f.contacted_at and view.outcomes.get(f.key) not in FINAL_OUTCOMES]


def question(items: Sequence[Failure], view: View) -> Pick | None:
    offered = draftable(items, view)[:MAX_OPTIONS]
    if not offered:
        return None
    options = [(_label(f, view), f"{view.scored(f).priority} priority") for f in offered]
    hitl = question_metadata(HEADER, options, multi_select=True, allow_custom_input=True)
    text = (f"{HEADER} Pick any (I'll draft them one at a time for your approval), type an instruction, "
            "or say skip.")
    return Pick(text, hitl, {METADATA_KEY: {"numbers": {f.number: f.key for f in offered}}})


def _label(f: Failure, view: View) -> str:
    label = f"#{f.number} {f.who} {scoring.money(f.amount_minor, f.currency)} {view.scored(f).reason.klass}"
    return label.encode("utf-8")[:MAX_OPTION_LABEL_LEN].decode("utf-8", errors="ignore").strip()


def read_answer(answer: str, numbers: Mapping[str, str]) -> tuple[tuple[str, ...], str]:
    """(picked failure keys in order, the remaining lines as one instruction)."""
    keys: tuple[str, ...] = ()
    rest: tuple[str, ...] = ()
    for line in (answer or "").splitlines():
        match = _PICKED.match(line)
        if match and match.group(1) in numbers:
            keys = keys if numbers[match.group(1)] in keys else (*keys, numbers[match.group(1)])
        elif line.strip():
            rest = (*rest, line.strip())
    return keys, "\n".join(rest)
