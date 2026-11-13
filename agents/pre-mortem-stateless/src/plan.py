"""Pure: the plan as numbered lines (L1, L2, ...), and the first/last cuts of an over-long plan.

Lines are split on newlines; blank lines are dropped; headings are kept; a line over 400 characters is split
at sentence ends (a single sentence that is still too long is cut at a space)."""

from __future__ import annotations

import re

MAX_LINE_CHARS = 400
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def number_lines(text: str) -> tuple[str, ...]:
    """The plan's lines in order; index 0 is L1."""
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = " ".join(raw.split())
        if line:
            lines.extend(_split_long(line))
    return tuple(lines)


def numbered_block(lines: tuple[str, ...] | list[str]) -> str:
    return "\n".join(f"L{n}: {line}" for n, line in enumerate(lines, start=1))


def _split_long(line: str) -> list[str]:
    if len(line) <= MAX_LINE_CHARS:
        return [line]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(line):
        for chunk in _hard_cut(sentence):
            joined = f"{current} {chunk}".strip()
            if len(joined) <= MAX_LINE_CHARS:
                current = joined
            else:
                pieces.append(current)
                current = chunk
    if current:
        pieces.append(current)
    return [p for p in pieces if p]


def _hard_cut(sentence: str) -> list[str]:
    chunks: list[str] = []
    rest = sentence
    while len(rest) > MAX_LINE_CHARS:
        cut = rest.rfind(" ", 0, MAX_LINE_CHARS)
        cut = cut if cut > 0 else MAX_LINE_CHARS
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [*chunks, rest] if rest else chunks


def cut_first(text: str, max_chars: int) -> str:
    """The leading lines that fit in `max_chars`."""
    kept: list[str] = []
    size = 0
    for line in text.splitlines():
        if size + len(line) + 1 > max_chars:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept).strip() or text[:max_chars]


def cut_last(text: str, max_chars: int) -> str:
    """The trailing lines that fit in `max_chars`."""
    kept: list[str] = []
    size = 0
    for line in reversed(text.splitlines()):
        if size + len(line) + 1 > max_chars:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(reversed(kept)).strip() or text[-max_chars:]
