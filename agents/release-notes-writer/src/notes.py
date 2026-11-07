"""Pure: the release notes structure, built in code from the checked sentences.

    ## <version or [version?]> - <date or [date?]>
    Breaking changes, Security (only if any), then New, Improved, Fixed, Deprecated (empty blocks omitted)

Items are numbered once across the whole notes in the order shown, so "drop item 4" always means the
fourth item on screen. A block of 6+ items is sub-grouped by theme (proposed order, then input order) when
the notes have 4+ items; fewer than 4 items -> no themes at all."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from change import Change, block_of, included
from inputs import Header

BLOCKS = ("breaking", "security", "new", "improved", "fixed", "deprecated")
LABELS = {"breaking": "Breaking changes", "security": "Security", "new": "New", "improved": "Improved",
          "fixed": "Fixed", "deprecated": "Deprecated"}
THEME_MIN_BLOCK = 6
THEME_MIN_TOTAL = 4
UNTHEMED = ("breaking", "security")
MIGRATION_PLACEHOLDER = "[migration steps?]"


@dataclass(frozen=True)
class Entry:
    number: int
    block: str
    theme: str
    change: Change


def _index(change: Change) -> int:
    digits = change.id.lstrip("c")
    return int(digits) if digits.isdigit() else 0


def _themed(block: str, size: int, total: int) -> bool:
    return block not in UNTHEMED and total >= THEME_MIN_TOTAL and size >= THEME_MIN_BLOCK


def ordered(changes: Sequence[Change], themes: Sequence[str]) -> list[Entry]:
    shown = included(changes)
    entries: list[Entry] = []
    rank = {t: n for n, t in enumerate(themes)}
    for block in BLOCKS:
        members = [c for c in shown if block_of(c) == block]
        themed = _themed(block, len(members), len(shown))
        key = (lambda c: (rank.get(c.theme, len(themes)), _index(c))) if themed else _index
        for change in sorted(members, key=key):
            entries.append(Entry(len(entries) + 1, block, change.theme if themed else "", change))
    return entries


def heading(header: Header) -> str:
    return f"## {header.version or '[version?]'} - {header.date or '[date?]'}"


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def migration_note(change: Change) -> str:
    """" Migration: <pasted words>." for a breaking change, unless the sentence already says them."""
    if not change.breaking:
        return ""
    if not change.migration:
        return f" {MIGRATION_PLACEHOLDER}"
    if change.migration.casefold() in change.sentence.casefold():
        return ""
    return f" Migration: {change.migration.rstrip('.')}."


def item_text(change: Change) -> str:
    """One item's words without numbering: the sentence, its reference and the migration note."""
    text = _sentence(change.sentence)
    if change.ref and change.url:
        text += f" ([{change.ref}]({change.url}))"
    elif change.ref:
        text += f" ({change.ref})"
    elif change.url:
        text += f" ({change.url})"
    return text + migration_note(change)


def markdown(entries: Sequence[Entry], header: Header, only: str | None = None) -> str:
    lines = [heading(header)]
    for block in BLOCKS:
        members = [e for e in entries if e.block == block and (only is None or e.block == only)]
        if not members:
            continue
        lines += ["", f"**{LABELS[block]}**"]
        theme = None
        for entry in members:
            if entry.theme and entry.theme != theme:
                theme = entry.theme
                lines += ["", f"_{theme}_"]
            lines.append(f"{entry.number}. {item_text(entry.change)}")
    return "\n".join(lines)
