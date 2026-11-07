"""Pure: the optional "which should I nudge?" multi-select after a list, and reading its answer.

Only asks still waiting, checked for a reply, and not touched in the last two days are offered; unclear
and unchecked asks can still be nudged by naming them. The answer's lines are mapped back to asks by
their `#n` prefix in code; any other line is an instruction for the draft step."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from asks import Ask
from nasiko_hitl import MAX_OPTION_LABEL_LEN, question_metadata
from render import View

METADATA_KEY = "waiting_on_pick"
MAX_OPTIONS = 10
HEADER = "Which should I draft nudges for?"
NOTHING_PICKED = "There is nothing to do for that request."
FINAL_OUTCOMES = frozenset({"sent", "denied"})  # never nudged again in this conversation
_PICKED = re.compile(r"^\s*#(\d+)\b")


@dataclass(frozen=True)
class Pick:
    text: str
    hitl: dict
    metadata: dict


def nudgeable(items: Sequence[Ask], view: View) -> list[Ask]:
    return [a for a in items if a.reply_state == "waiting" and a.reply_checked
            and view.outcomes.get(a.key) not in FINAL_OUTCOMES and view.scored(a).tier != "recent"]


def question(items: Sequence[Ask], view: View) -> Pick | None:
    offered = nudgeable(items, view)[:MAX_OPTIONS]
    if not offered:
        return None
    options = [(_label(a, view), f"{view.scored(a).tier} nudge") for a in offered]
    hitl = question_metadata(HEADER, options, multi_select=True, allow_custom_input=True)
    text = (f"{HEADER} Pick any (I'll draft them one at a time for your approval), type an instruction, "
            "or say skip.")
    return Pick(text, hitl, {METADATA_KEY: {"numbers": {a.id: a.key for a in offered}}})


def _label(a: Ask, view: View) -> str:
    label = f"#{a.id} {a.who}: {a.gist or a.quote} ({int(view.scored(a).days_waiting)}d)"
    return label.encode("utf-8")[:MAX_OPTION_LABEL_LEN].decode("utf-8", errors="ignore").strip()


def read_answer(answer: str, numbers: Mapping[str, str]) -> tuple[tuple[str, ...], str]:
    """(picked ask keys in order, the remaining lines as one instruction)."""
    keys: tuple[str, ...] = ()
    rest: tuple[str, ...] = ()
    for line in (answer or "").splitlines():
        match = _PICKED.match(line)
        if match and match.group(1) in numbers:
            keys = keys if numbers[match.group(1)] in keys else (*keys, numbers[match.group(1)])
        elif line.strip():
            rest = (*rest, line.strip())
    return keys, "\n".join(rest)
