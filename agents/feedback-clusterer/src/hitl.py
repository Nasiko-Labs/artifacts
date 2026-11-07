"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how
an answer is read. Reading fails closed: an answer that isn't recognised is Cancel.

    Q1 column   the text column is ambiguous    options: the candidate column names, Cancel
    Q2 too_big  over 2,000 items or 100k chars  options: Use the most recent 2,000, Cancel; custom = a filter
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import filters
from filters import Focus
from inputs import current_message
from items import FeedbackItem, numbered
from nasiko_hitl import question_metadata

METADATA_KEY = "feedback_clusterer"
COLUMN, TOO_BIG = "column", "too_big"
CANCEL = "Cancel"
MOST_RECENT = "Use the most recent 2,000"
MAX_ITEMS, MAX_CHARS = 2_000, 100_000
MAX_COLUMN_OPTIONS = 9
_YES = re.compile(r"^(?:yes|y|ok|okay|sure|go|go ahead|do it|proceed|use the most recent|most recent|recent)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    instruction: str = ""
    paste: str = ""                                   # Q1: the raw paste, re-parsed with the chosen column
    candidates: tuple[str, ...] = ()                  # Q1
    items: tuple[dict, ...] = ()                      # Q2: the most recent items that fit, already cut
    total: int = 0                                    # Q2: how many items the paste had
    append: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "paste": self.paste,
                               "candidates": list(self.candidates), "items": list(self.items), "total": self.total,
                               "append": self.append, "notes": list(self.notes)}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (COLUMN, TOO_BIG):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], instruction=str(data.get("instruction") or ""),
                       paste=str(data.get("paste") or ""), candidates=tuple(str(c) for c in data.get("candidates") or ()),
                       items=tuple(dict(i) for i in data.get("items") or ()), total=int(data.get("total") or 0),
                       append=bool(data.get("append")), notes=tuple(str(n) for n in data.get("notes") or ()))


@dataclass(frozen=True)
class Answer:
    action: str                       # run | cancel
    text_column: str = ""
    items: tuple[FeedbackItem, ...] = ()
    focus: Focus | None = None


def most_recent(items: Sequence[FeedbackItem]) -> tuple[FeedbackItem, ...]:
    """Newest first (undated last), at most 2,000 items and 100k chars of text, back in their own order."""
    chosen, chars = [], 0
    for item in sorted(items, key=lambda i: i.date, reverse=True):
        if len(chosen) >= MAX_ITEMS or chars + len(item.text) > MAX_CHARS:
            break
        chosen.append(item)
        chars += len(item.text)
    keep = {i.id for i in chosen}
    return numbered([i for i in items if i.id in keep])


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == COLUMN:
        header = "Which column holds the feedback text?"
        options = [(c, "") for c in pending.candidates[:MAX_COLUMN_OPTIONS]] + [(CANCEL, "")]
        text = f"{header} More than one column could be it: {', '.join(pending.candidates)}."
    else:
        header = (f"This paste has {pending.total:,} items, more than I can cluster in one go (2,000 items or "
                  "100k characters). What should I do?")
        options = [(MOST_RECENT, ""), (CANCEL, "")]
        text = header + ' You can also type a narrower filter, for example "only enterprise" or "last 30 days".'
    lead = "\n".join(pending.notes)
    return "\n\n".join(p for p in (lead, text) if p), question_metadata(header, options)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    words = " ".join(answer.casefold().split())
    if not words or words == CANCEL.casefold():
        return Answer("cancel")
    if pending.kind == COLUMN:
        chosen = next((c for c in pending.candidates if c.casefold() == words), None)
        return Answer("run", text_column=chosen) if chosen else Answer("cancel")
    items = tuple(FeedbackItem.from_dict(i) for i in pending.items)
    if words == MOST_RECENT.casefold() or _YES.match(words):
        return Answer("run", items=items)
    focus = filters.parse_focus(answer)
    return Answer("run", items=items, focus=focus) if not focus.is_empty else Answer("cancel")
