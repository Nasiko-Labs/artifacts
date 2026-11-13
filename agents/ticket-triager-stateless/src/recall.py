"""Pure: the earlier batch and triage, re-read from the conversation history (this agent keeps no state).

- The batch: the newest user message that holds tickets, plus every "add these: ..." the user sent after it,
  oldest first (texts, re-parsed when they are triaged again).
- The triage: the newest assistant reply after that batch holding a full table ("**Triage of N tickets**"
  with N rows and no "_Showing ..._" view), read back by `table.py`.
Either may be missing (history trimmed, compressed or absent); callers then triage again or ask for a
re-paste. Nothing is ever guessed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import followup
import parse_tickets
import table as table_mod
from history import ASSISTANT, USER, Turn


@dataclass(frozen=True)
class Prior:
    batch: tuple[str, ...] = ()               # the batch text, then each later "add these" text
    table: table_mod.Table | None = None


def read(turns: Sequence[Turn]) -> Prior:
    start = _newest_batch(turns)
    batch: list[str] = []
    if start >= 0:
        batch.append(turns[start].text)
        batch += [c.text for t in turns[start + 1:] if t.role == USER
                  for c in [followup.parse(t.text)] if c is not None and c.kind == "add"]
    found = None
    for turn in reversed(turns[start + 1:]):
        found = table_mod.read(turn.text) if turn.role == ASSISTANT else None
        if found is not None:
            break
    return Prior(tuple(batch), found)


def _newest_batch(turns: Sequence[Turn]) -> int:
    """The index of the newest user turn that holds tickets on its own (not an "add these"), or -1."""
    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
        command = followup.parse(turn.text) if turn.role == USER else None
        if turn.role == USER and (command is None or command.kind != "add") and holds_tickets(turn.text):
            return index
    return -1


def holds_tickets(text: str) -> bool:
    parsed = parse_tickets.parse(text)
    return parsed.ambiguous or bool(parsed.tickets)
