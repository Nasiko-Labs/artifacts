"""Pure: the format-neutral document every renderer draws from.

A `Document` is the input JSON laid out as titled sections (a paragraph, bullets, a table, numbered
cards, key/value lines, or a JSON block for very deep values). It holds the original scalar values
untouched: renderers escape them for their target and never change them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MARKDOWN, SLACK, EMAIL = "markdown", "slack", "email"
TARGET_LABELS = {MARKDOWN: "Markdown", SLACK: "Slack blocks", EMAIL: "email HTML"}

PARAGRAPH, BULLETS, TABLE, CARDS, FIELDS, JSON_BLOCK = "paragraph", "bullets", "table", "cards", "fields", "json"


@dataclass(frozen=True)
class Json:
    """A value too nested for its place, shown as inline JSON."""

    value: Any


@dataclass(frozen=True)
class ScalarList:
    """A short list of scalars inside a cell or a key/value line."""

    items: tuple


@dataclass(frozen=True)
class Card:
    title: Any                      # a scalar, or None
    lines: tuple[tuple[str, Any], ...]


@dataclass(frozen=True)
class Section:
    title: str
    kind: str
    text: Any = None                # paragraph: the scalar
    markdown: bool = False          # paragraph: the value is Markdown (sanitised, not escaped)
    items: tuple = ()               # bullets: cells
    columns: tuple[str, ...] = ()   # table: keys, first-seen order
    rows: tuple[tuple, ...] = ()    # table: one tuple of cells per row
    cards: tuple[Card, ...] = ()
    lines: tuple[tuple[str, Any], ...] = ()  # fields: (key, cell)
    value: Any = None               # json block

    @property
    def size(self) -> int:
        counts = {BULLETS: len(self.items), TABLE: len(self.rows), CARDS: len(self.cards), FIELDS: len(self.lines)}
        return counts.get(self.kind, 1)


@dataclass(frozen=True)
class Document:
    sections: tuple[Section, ...]
    lead: str | None = None                 # the envelope's summary
    footer: tuple[Any, Any] | None = None   # (confidence, needs_human) as given, None when absent
    needs_human: bool = False
    template: str | None = None             # None = the generic renderer guessed the shape
    deep: int = 0                           # fields nested deeper than the limit, shown as JSON


@dataclass(frozen=True)
class Options:
    target: str = MARKDOWN
    compact: bool = False
    footer: bool = True
    title: str = ""


@dataclass
class Stats:
    """What one render lost or reshaped, for the notes, the confidence and the fidelity check."""

    cut: int = 0
    collapsed: int = 0
    blocks: int = 0
    excluded: list[str] = field(default_factory=list)  # original text left out on purpose (cut, collapsed)


def is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


HEADER_OVERRIDES = {"to_whom": "To", "source_quote": "Quote", "days_overdue": "Days overdue",
                    "alt_shorter": "Shorter version", "memo_markdown": "Memo", "tldr": "TL;DR"}


def header(key: str) -> str:
    """`days_overdue` -> `Days overdue`; a few keys have a fixed heading."""
    if key in HEADER_OVERRIDES:
        return HEADER_OVERRIDES[key]
    words = str(key).replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else "Value"
