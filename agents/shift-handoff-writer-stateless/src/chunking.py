"""Pure: notes up to 24,000 chars go to the model in one pass; longer notes are cut into windows of whole
entries of at most 12,000 chars each (an entry longer than a window is its own window)."""

from __future__ import annotations

from entries import Entry, render_for_model

SINGLE_PASS_CHARS = 24_000
WINDOW_CHARS = 12_000
MAX_WINDOWS = 10


def windows(entries: list[Entry]) -> list[list[Entry]]:
    if len(render_for_model(entries)) <= SINGLE_PASS_CHARS:
        return [entries] if entries else []
    out: list[list[Entry]] = []
    current: list[Entry] = []
    size = 0
    for entry in entries:
        line = len(entry.text) + len(entry.id) + 4
        if current and size + line > WINDOW_CHARS:
            out.append(current)
            current, size = [], 0
        current.append(entry)
        size += line
    if current:
        out.append(current)
    return out
