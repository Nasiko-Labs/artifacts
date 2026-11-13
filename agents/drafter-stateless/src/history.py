"""Pure: the platform's chat history, read as this agent's only memory.

On a multi-turn chat the platform prepends a budgeted slice of earlier turns to the user's text:

    user: <earlier user text>
    assistant: <earlier agent reply>
    ...

    Current message: <this message>

The slice may be trimmed, compressed or absent. Parsing is best-effort and never raises: a line that starts
`user:` or `assistant:` opens a turn, any other line continues the open turn, and text before the first
role line (a summary, a truncation marker) is ignored. A turn the platform compressed carries its elision
sigil "[⋯ " (`compressed`): its text is not the original and must not be used as source material.
Nothing here is stored anywhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
USER = "user"
ASSISTANT = "assistant"
COMPRESSION_MARK = "[⋯ "   # the platform's elision breadcrumb (lines/items/chars elided, or a table)
_ROLE_LINE = re.compile(r"^(user|assistant)\s*:\s?(.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class Turn:
    role: str      # user | assistant
    text: str

    @property
    def compressed(self) -> bool:
        return COMPRESSION_MARK in self.text


def split(raw: str) -> tuple[str, str]:
    """(history block, current message). No marker: no history, the whole text is the message."""
    text = raw or ""
    idx = text.rfind(CURRENT_MESSAGE_MARKER)
    if idx == -1:
        return "", text
    return text[:idx], text[idx + len(CURRENT_MESSAGE_MARKER):]


def parse(block: str) -> tuple[Turn, ...]:
    """The turns in a history block, oldest first; empty or garbled history gives ()."""
    turns: list[tuple[str, list[str]]] = []
    for line in (block or "").splitlines():
        match = _ROLE_LINE.match(line)
        if match:
            turns.append((match.group(1).casefold(), [match.group(2)]))
        elif turns:
            turns[-1][1].append(line)
    out = (Turn(role, "\n".join(lines).strip()) for role, lines in turns)
    return tuple(t for t in out if t.text)


def of_role(turns: tuple[Turn, ...], role: str) -> tuple[Turn, ...]:
    return tuple(t for t in turns if t.role == role)
