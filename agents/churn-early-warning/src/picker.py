"""Pure: the optional "Draft outreach for which accounts?" multi-select after a list, the one free-text
question for a missing customer address, and reading their answers. Picked lines are mapped back to
accounts by their `#n` prefix in code; any other line is an instruction for the draft step."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import outreach
from nasiko_hitl import MAX_OPTION_LABEL_LEN, question_metadata
from scoring import Row

METADATA_KEY = "churn_pick"
EMAIL_KEY = "churn_email"
MAX_OPTIONS = 10
HEADER = "Draft outreach for which accounts?"
NOTHING_PICKED = "There is nothing to do for that request."
FINAL_OUTCOMES = frozenset({"sent", "denied"})  # never drafted again in this conversation
_PICKED = re.compile(r"^\s*#(\d+)\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


@dataclass(frozen=True)
class Pick:
    text: str
    hitl: dict
    metadata: dict


def draftable(rows: Sequence[Row], outcomes: Mapping[str, str]) -> list[Row]:
    return [r for r in rows if outcomes.get(r.key) not in FINAL_OUTCOMES]


def question(rows: Sequence[Row], outcomes: Mapping[str, str]) -> Pick | None:
    offered = draftable(rows, outcomes)[:MAX_OPTIONS]
    if not offered:
        return None
    options = [(_label(r), outreach.LABELS.get(r.outreach, r.outreach)) for r in offered]
    hitl = question_metadata(HEADER, options, multi_select=True, allow_custom_input=True)
    text = (f"{HEADER} Pick any (I'll draft them one at a time for your approval), type an instruction, "
            "or say skip.")
    return Pick(text, hitl, {METADATA_KEY: {"numbers": {r.id: r.key for r in offered}}})


def _label(r: Row) -> str:
    label = f"#{r.id} {r.account.name}: {outreach.LABELS.get(r.outreach, r.outreach)} ({r.band} {r.risk_score:.1f})"
    return label.encode("utf-8")[:MAX_OPTION_LABEL_LEN].decode("utf-8", errors="ignore").strip()


def read_answer(answer: str, numbers: Mapping[str, str]) -> tuple[tuple[str, ...], str]:
    """(picked account keys in order, the remaining lines as one instruction)."""
    keys: tuple[str, ...] = ()
    rest: tuple[str, ...] = ()
    for line in (answer or "").splitlines():
        match = _PICKED.match(line)
        if match and match.group(1) in numbers:
            keys = keys if numbers[match.group(1)] in keys else (*keys, numbers[match.group(1)])
        elif line.strip():
            rest = (*rest, line.strip())
    return keys, "\n".join(rest)


def email_question(row: Row, instruction: str) -> Pick:
    """One free-text question: where should outreach to an account with no address on record go?"""
    header = f"Which email address should outreach to {row.account.name} go to?"
    if len(header.encode("utf-8")) > 200:
        header = "Which email address should this outreach go to?"
    hitl = question_metadata(header, [("Skip", "Don't draft it.")], multi_select=False, allow_custom_input=True)
    text = f"I have no email address on record for #{row.id} {row.account.name}. Type the address to write to, or say skip."
    return Pick(text, hitl, {EMAIL_KEY: {"key": row.key, "instruction": instruction}})


def email_in(answer: str) -> str:
    match = _EMAIL.search(answer or "")
    return match.group(0) if match else ""
