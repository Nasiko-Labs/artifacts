"""Pure: the chat history the platform prepends to a message, read as this agent's only memory.

On a multi-turn chat the first text part is

    user: <earlier user text>
    assistant: <earlier agent reply>
    ...

    Current message: <this message>

The history is a budgeted slice: it may be trimmed, compressed or absent. A line opening with `user:` or
`assistant:` starts a turn; following lines continue it; anything before the first role line is dropped.
Nothing here is trusted: it only ever stands in for what the user could paste again.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
USER, ASSISTANT = "user", "assistant"

_ROLE_LINE = re.compile(r"^\s*(user|assistant)\s*:[ \t]?(.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class Turn:
    role: str
    text: str


def split(raw: str) -> tuple[str, str]:
    """(history block, current message). No marker: no history, the whole text is the message."""
    text = raw or ""
    idx = text.rfind(CURRENT_MESSAGE_MARKER)
    if idx == -1:
        return "", text
    return text[:idx], text[idx + len(CURRENT_MESSAGE_MARKER):]


def parse(block: str) -> tuple[Turn, ...]:
    """Turns in order; empty, garbled or role-less history gives ()."""
    turns: list[tuple[str, list[str]]] = []
    for line in (block or "").splitlines():
        match = _ROLE_LINE.match(line)
        if match:
            turns.append((match.group(1).casefold(), [match.group(2)]))
        elif turns:
            turns[-1][1].append(line)
    built = (Turn(role, "\n".join(lines).strip()) for role, lines in turns)
    return tuple(turn for turn in built if turn.text)


def last(turns: Sequence[Turn], role: str) -> str:
    for turn in reversed(turns):
        if turn.role == role:
            return turn.text
    return ""
