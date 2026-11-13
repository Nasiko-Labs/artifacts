"""Pure: size limits (Markdown table cells, Slack Block Kit) and the cuts that enforce them.

A cut always ends at a word boundary with an ellipsis; the caller records the original in `Stats` so
the fidelity check knows it was left out on purpose and the reply says how many were shortened."""

from __future__ import annotations

ELLIPSIS = "…"
MD_CELL_CHARS = 300
SLACK_HEADER_CHARS = 150
SLACK_SECTION_CHARS = 3000
SLACK_FIELD_CHARS = 2000
SLACK_FIELDS_PER_BLOCK = 10
SLACK_MAX_BLOCKS = 50
SUBJECT_CHARS = 80


def cut(text: str, limit: int) -> str:
    """`text` when it fits, else the longest word-boundary prefix plus an ellipsis, within `limit`."""
    if len(text) <= limit:
        return text
    head = text[: limit - len(ELLIPSIS)]
    space = head.rfind(" ")
    if space >= limit // 2:
        head = head[:space]
    return head.rstrip() + ELLIPSIS


def split_text(text: str, limit: int = SLACK_SECTION_CHARS) -> list[str]:
    """Splits at line boundaries into chunks of at most `limit`; a single over-long line is split at
    spaces (or hard, as a last resort). Concatenating the chunks with newlines/spaces gives the text."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        for piece in _split_line(line, limit):
            candidate = f"{current}\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = piece
    if current or not chunks:
        chunks.append(current)
    return chunks


def _split_line(line: str, limit: int) -> list[str]:
    pieces: list[str] = []
    rest = line
    while len(rest) > limit:
        space = rest.rfind(" ", 0, limit)
        at = space if space > 0 else limit
        pieces.append(rest[:at])
        rest = rest[at:].lstrip(" ") if space > 0 else rest[at:]
    pieces.append(rest)
    return pieces
