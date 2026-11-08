"""Pure: the "Which owners should I message?" multi-select after a check, and reading its answer.

Offered: owners with a checked chat match, not already messaged in this conversation, at most 10, labelled
`Name (N issues)` (unique). The platform returns the picked labels one per line and any typed text as a last
line; a line that is not a label is the tone note for every intro (capped). Picking selects; it never
approves text: every picked owner still gets their own approval card."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from nasiko_hitl import MAX_OPTION_LABEL_LEN, question_metadata
from records import Owner

PHASE_KEY = "issue_hygiene"
HEADER = "Which owners should I message?"
DONE = frozenset({"sent"})


@dataclass(frozen=True)
class Pause:
    """An input-required pause: the text, the question block, and the task metadata to resume from."""

    text: str
    hitl: dict
    metadata: dict


def offerable(owners: Sequence[Owner], outcomes: Mapping[str, str], limit: int) -> list[Owner]:
    return [o for o in owners if o.user_id and outcomes.get(o.key) not in DONE][:limit]


def labels(owners: Sequence[Owner]) -> list[str]:
    """`Name (N issues)`; a repeated label gets the owner's email so every label is unique."""
    plain = [f"{o.name} ({o.count} issue{'s' if o.count != 1 else ''})" for o in owners]
    repeated = {label for label in plain if plain.count(label) > 1}
    unique = [f"{label} {o.email}" if label in repeated else label for label, o in zip(plain, owners, strict=True)]
    return [_clip(label) for label in unique]


def question(owners: Sequence[Owner], outcomes: Mapping[str, str], limit: int) -> Pause | None:
    offered = offerable(owners, outcomes, limit)
    if not offered:
        return None
    names = labels(offered)
    options = [(label, f"{o.count} flagged issue(s); one message listing them, shown to you before sending.")
               for label, o in zip(names, offered, strict=True)]
    hitl = question_metadata(HEADER, options, multi_select=True, allow_custom_input=True)
    text = (f"{HEADER} Tick the owners to message; anything you type (for example \"keep it friendly\") shapes the "
            "opening line. Each message is shown to you on its own card before anything is sent.")
    metadata = {PHASE_KEY: {"phase": "pick", "offered": {label: o.key for label, o in zip(names, offered, strict=True)}}}
    return Pause(text, hitl, metadata)


def read_answer(answer: str, offered: Mapping[str, str], max_chars: int) -> tuple[tuple[str, ...], str]:
    """(picked owner keys in answer order, the typed note, capped)."""
    by_label = {label.casefold(): key for label, key in offered.items()}
    keys: list[str] = []
    note: list[str] = []
    for line in (answer or "").splitlines():
        text = line.strip()
        if not text:
            continue
        key = by_label.get(text.casefold())
        if key is not None:
            keys.append(key)
        else:
            note.append(text)
    return tuple(dict.fromkeys(keys)), " ".join(note)[:max_chars]


def _clip(text: str) -> str:
    data = text.encode("utf-8")
    if len(data) <= MAX_OPTION_LABEL_LEN:
        return text
    return data[:MAX_OPTION_LABEL_LEN - 3].decode("utf-8", errors="ignore").rstrip() + "..."
