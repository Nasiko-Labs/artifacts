"""Pure: the earlier turns the platform prepends to the user's message, parsed into (role, text).

This agent keeps no state between messages. On a multi-turn chat the platform sends

    user: <earlier user text>
    assistant: <earlier agent reply>
    ...

    Current message: <this message>

as the first text part. That block is a budgeted slice of the conversation: it may be absent, trimmed,
compressed or garbled, so parsing is best-effort and never raises. A line that starts with `user:` or
`assistant:` opens a turn; every other line continues the open turn; text before the first role line is
dropped (it belongs to a turn whose start was trimmed away)."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
USER, ASSISTANT = "user", "assistant"
MAX_HISTORY_CHARS = 400_000

_ROLE_LINE = re.compile(r"^\s*(user|assistant)\s*:\s?(.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class HistoryTurn:
    role: str      # user | assistant
    text: str


def split(raw: str) -> tuple[str, str]:
    """(history block, current message). No marker: no history, the whole text is the message."""
    text = raw or ""
    idx = text.rfind(CURRENT_MESSAGE_MARKER)
    if idx == -1:
        return "", text
    return text[:idx], text[idx + len(CURRENT_MESSAGE_MARKER):]


def parse(block: str) -> tuple[HistoryTurn, ...]:
    turns: list[HistoryTurn] = []
    role, lines = "", []
    for line in (block or "")[-MAX_HISTORY_CHARS:].splitlines():
        match = _ROLE_LINE.match(line)
        if match:
            turns += _close(role, lines)
            role, lines = match.group(1).casefold(), [match.group(2)]
        elif role:
            lines.append(line)
    return tuple(turns + _close(role, lines))


def _close(role: str, lines: Sequence[str]) -> list[HistoryTurn]:
    text = "\n".join(lines).strip()
    return [HistoryTurn(role, text)] if role and text else []


def latest(turns: Sequence[HistoryTurn], role: str,
           accept: Callable[[str], bool] = lambda _t: True) -> str:
    """The text of the most recent turn of `role` that `accept` takes, else ""."""
    return next((t.text for t in reversed(turns) if t.role == role and accept(t.text)), "")


def texts(turns: Sequence[HistoryTurn], role: str) -> tuple[str, ...]:
    """Every `role` turn's text, most recent first."""
    return tuple(t.text for t in reversed(turns) if t.role == role)
