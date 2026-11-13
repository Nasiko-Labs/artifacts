"""Pure: the earlier turns the platform prepends to a chat message, read back into turns.

The platform sends "{history}\\n\\nCurrent message: {text}", where history is "user: ..." / "assistant: ..."
lines (a turn's later lines carry no prefix). It is a budgeted, possibly re-ordered, compressed or absent
slice, so this reader never fails: text before the first role line is ignored, unknown prefixes continue the
turn they follow, and an empty or garbled block gives no turns. It is this agent's only memory.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
USER, ASSISTANT = "user", "assistant"

_ROLE_LINE = re.compile(r"^(user|assistant|agent)\s*:\s?(.*)$", re.IGNORECASE)
_T = TypeVar("_T")


@dataclass(frozen=True)
class Turn:
    role: str      # user | assistant
    text: str


def split(raw: str) -> tuple[tuple[Turn, ...], str]:
    """(earlier turns, the current message). Without the marker the whole text is the current message."""
    text = raw or ""
    idx = text.rfind(CURRENT_MESSAGE_MARKER)
    if idx == -1:
        return (), text
    return parse(text[:idx]), text[idx + len(CURRENT_MESSAGE_MARKER):]


def parse(block: str) -> tuple[Turn, ...]:
    turns: list[tuple[str, list[str]]] = []
    for line in (block or "").splitlines():
        role = _ROLE_LINE.match(line)
        if role:
            name = USER if role.group(1).casefold() == USER else ASSISTANT
            turns.append((name, [role.group(2)]))
        elif turns:
            turns[-1][1].append(line)
    return tuple(Turn(role, "\n".join(lines).strip()) for role, lines in turns if "\n".join(lines).strip())


def newest(turns: Sequence[Turn], read: Callable[[Turn], _T | None]) -> _T | None:
    """The first non-None `read(turn)`, newest turn first."""
    for turn in reversed(turns):
        found = read(turn)
        if found is not None:
            return found
    return None
