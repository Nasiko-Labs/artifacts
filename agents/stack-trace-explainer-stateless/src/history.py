"""Pure: the platform's chat history, this agent's only memory.

On a multi-turn chat the platform glues earlier turns in front of the message:

    user: <earlier user text>
    assistant: <earlier reply>
    ...

    Current message: <this message>

Each turn is `role: content`, joined by newlines, so a multi-line turn continues on the lines after its
`user:` / `assistant:` line. The slice is budgeted: it may be trimmed, compressed or missing, so every
reader here is best-effort and returns nothing rather than guessing. Text before the first role line is
ignored."""

from __future__ import annotations

import re
from dataclasses import dataclass

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
USER, ASSISTANT = "user", "assistant"

_ROLE_LINE = re.compile(r"^(user|assistant):[ \t]?(.*)$", re.IGNORECASE)


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
    """The turns in a history block, oldest first. Garbled or empty input gives what could be read."""
    turns: list[tuple[str, list[str]]] = []
    for line in (block or "").splitlines():
        match = _ROLE_LINE.match(line)
        if match:
            turns.append((match.group(1).casefold(), [match.group(2)]))
        elif turns:
            turns[-1][1].append(line)
    out = (Turn(role, "\n".join(lines).strip()) for role, lines in turns)
    return tuple(t for t in out if t.text)


def last_assistant(turns: tuple[Turn, ...]) -> str:
    """The most recent reply this agent gave, or ""."""
    return next((t.text for t in reversed(turns) if t.role == ASSISTANT), "")


def user_texts(turns: tuple[Turn, ...]) -> tuple[str, ...]:
    """The user's earlier messages, newest first."""
    return tuple(t.text for t in reversed(turns) if t.role == USER)


def user_before_last_assistant(turns: tuple[Turn, ...]) -> str:
    """The user message the most recent reply answered, or ""."""
    seen_reply = False
    for t in reversed(turns):
        if t.role == ASSISTANT:
            seen_reply = True
        elif seen_reply:
            return t.text
    return ""
