"""Pure: a paste -> (the replies part, existing macro texts, settings lines like `min 4` / `top 10`).

A line `Existing macros:` (or `Macros:`) starts the macros section, a line `Replies:` returns to replies;
macro texts are separated by blank lines or `---`."""

from __future__ import annotations

import re

_SETTING = re.compile(r"^\s*(min|top)\s*[:=]?\s*(\d{1,4})\s*$", re.IGNORECASE)
_MACROS_HEAD = re.compile(r"^\s*(?:existing macros|macros)\s*:\s*(.*)$", re.IGNORECASE)
_REPLIES_HEAD = re.compile(r"^\s*replies\s*:\s*$", re.IGNORECASE)


def split_sections(text: str) -> tuple[str, tuple[str, ...], tuple[tuple[str, int], ...]]:
    """(the replies part, existing macro texts, settings lines like `min 4`)."""
    settings, replies, macros = [], [], []
    target = replies
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        setting, head = _SETTING.match(line), _MACROS_HEAD.match(line)
        if setting:
            settings.append((setting.group(1).casefold(), int(setting.group(2))))
        elif head:
            target = macros
            if head.group(1).strip():
                macros.append(head.group(1))
        elif _REPLIES_HEAD.match(line):
            target = replies
        else:
            target.append(line)
    blocks = _blocks("\n".join(macros))
    return "\n".join(replies).strip(), tuple(b for b in blocks if b), tuple(settings)


def _blocks(text: str) -> list[str]:
    parts = re.split(r"\n\s*(?:-{3,}|={3,})\s*\n|\n\s*\n", f"\n{text}\n")
    return [p.strip() for p in parts if p.strip()]
