"""Pure: the optional "Which owners should I draft messages for?" multi-select after a list, the one
question for an owner with no contact (unknown, or no chat id or email), and reading their answers.
Picked lines are mapped back to owner keys by their exact label in code; any other line is an
instruction for the draft step."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pricing
from nasiko_hitl import MAX_OPTION_LABEL_LEN, question_metadata
from owners import Group

METADATA_KEY = "reaper_pick"
CONTACT_KEY = "reaper_contact"
MAX_OPTIONS = 10
HEADER = "Which owners should I draft messages for?"
TYPE_OWNER = "I'll type the owner"
SKIP = "Skip"
NOTHING_PICKED = "There is nothing to do for that request."
FINAL_OUTCOMES = frozenset({"sent", "denied"})  # never drafted again in this conversation
_MAX_HEADER = 200


@dataclass(frozen=True)
class Pick:
    text: str
    hitl: dict
    metadata: dict


def label(g: Group) -> str:
    text = (f"{g.label} ({len(g.rows)} resource{'s' if len(g.rows) != 1 else ''}, "
            f"{pricing.money(g.total)}/month)")
    return text.encode("utf-8")[:MAX_OPTION_LABEL_LEN].decode("utf-8", errors="ignore").strip()


def question(groups: Sequence[Group], outcomes: Mapping[str, str]) -> Pick | None:
    offered = [g for g in groups if outcomes.get(g.key) not in FINAL_OUTCOMES][:MAX_OPTIONS]
    if not offered:
        return None
    labels = {label(g): g.key for g in offered}
    if len(labels) != len(offered):  # two owners whose labels collide after truncation: no picker
        return None
    options = [(text, f"via {g.delivery}" if g.known else "owner unknown: I'll ask who") for text, g in
               zip(labels, offered, strict=True)]
    hitl = question_metadata(HEADER, options, multi_select=True, allow_custom_input=True)
    text = (f"{HEADER} Pick any (I'll draft one message per owner, each for your approval), type an "
            "instruction, or say skip.")
    return Pick(text, hitl, {METADATA_KEY: {"labels": labels}})


def read_answer(answer: str, labels: Mapping[str, str]) -> tuple[tuple[str, ...], str]:
    """(picked owner keys in order, the remaining lines as one instruction)."""
    keys: tuple[str, ...] = ()
    rest: tuple[str, ...] = ()
    for line in (answer or "").splitlines():
        key = labels.get(line.strip())
        if key is not None:
            keys = keys if key in keys else (*keys, key)
        elif line.strip():
            rest = (*rest, line.strip())
    return keys, "\n".join(rest)


def contact_question(g: Group, instruction: str) -> Pick:
    """One question: who owns these (unknown owner), or where a message to a known owner should go."""
    if g.known:
        header = f"Where should the message to {g.label} go?"
        text = (f"I have no chat id or email for {g.label}. Type their chat handle (like @priya or U0123ABC) or "
                "email address, or say skip.")
    else:
        header = f"Who owns these {len(g.rows)} resources?"
        text = (f"{len(g.rows)} resource(s) have no known owner. Type the owner's chat handle (like @priya or "
                "U0123ABC) or email address, or say skip.")
    if len(header.encode("utf-8")) > _MAX_HEADER:
        header = "Where should this message go?"
    hitl = question_metadata(header, [(SKIP, "Don't draft it."), (TYPE_OWNER, "Type a chat handle or email.")],
                             multi_select=False, allow_custom_input=True)
    return Pick(text, hitl, {CONTACT_KEY: {"key": g.key, "instruction": instruction}})
