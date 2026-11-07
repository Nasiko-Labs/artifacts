"""Pure: follow-ups on a stored result. Re-render commands ("chart only", "only findings", "show the table
for r2", "as json", "as a png") cost no LLM call; "also show X by Y" adds one analysis; "drill into West"
filters the stored table to rows with that value and re-runs."""

from __future__ import annotations

import re
from collections.abc import Sequence

from load import Loaded

COMMAND_MAX_WORDS = 10
_WORDS = re.compile(r"[^a-z0-9' ]+")
_FILLER = re.compile(r"\b(?:please|pls|can you|could you|would you|show me|show|give me|just|only|me|now|again|"
                     r"the|a|an|as|in|it|list|what are|send|draw|make)\b")
_TABLE = re.compile(r"\btable (?:for|of)? ?(r\d{1,2})\b|^(r\d{1,2})(?: table)?$")
_COMMANDS = (
    ("chart", re.compile(r"^(?:chart|graph|plot)(?: only)?$")),
    ("findings", re.compile(r"^(?:key )?(?:findings?|insights?)(?: only)?$")),
    ("caveats", re.compile(r"^(?:caveats?|data quality|warnings?)(?: only)?$")),
    ("png", re.compile(r"^(?:(?:chart|graph|plot) )?(?:png|chart file|image file)(?: (?:chart|file))?$")),
    ("default", re.compile(r"^(?:json|json only|everything|full result|all)$")),
)
_ALSO = re.compile(r"^(?:also|and also|now also|add|plus)\b.*\b(?:by|over|per|vs|versus|against)\b")
_DRILL = re.compile(r"^(?:drill (?:into|down (?:into|on|to))|zoom (?:into|in on)|focus on|filter (?:to|on|by)|only|"
                    r"just)\s+(.{1,80}?)\s*$")


def _plain(text: str) -> str:
    return " ".join(_WORDS.sub(" ", (text or "").casefold()).split())


def parse_command(text: str) -> str | None:
    """The view to re-render ("chart", "findings", "caveats", "png", "default", "table:r2"), or None."""
    plain = _plain(text)
    if not plain or len(plain.split()) > COMMAND_MAX_WORDS:
        return None
    table = _TABLE.search(plain)
    if table:
        return f"table:{table.group(1) or table.group(2)}"
    core = " ".join(_FILLER.sub(" ", plain).split())
    for view, pattern in _COMMANDS:
        if pattern.match(core):
            return view
    return None


def is_also(text: str) -> bool:
    return bool(_ALSO.match(_plain(text)))


def wants_drill(text: str) -> bool:
    return bool(_DRILL.match(" ".join((text or "").casefold().split()).rstrip(".!?")))


def drill_target(text: str, loaded: Loaded, category_columns: Sequence[str]) -> tuple[str, str] | None:
    """(column, value) when the message asks to drill into one value of a category column."""
    match = _DRILL.match(" ".join((text or "").casefold().split()).rstrip(".!?"))
    if not match:
        return None
    wanted = match.group(1).strip().strip("'\"")
    for name in category_columns:
        idx = loaded.header.index(name)
        values = {row[idx].strip() for row in loaded.rows}
        exact = next((v for v in values if v.casefold() == wanted), None)
        if exact:
            return name, exact
    return None


def filtered(loaded: Loaded, column: str, value: str) -> Loaded:
    idx = loaded.header.index(column)
    rows = tuple(r for r in loaded.rows if r[idx].strip() == value)
    return Loaded(loaded.header, rows, (f"Rows where {column} is {value}: {len(rows):,} of {len(loaded.rows):,}.",),
                  len(rows), len(loaded.header), loaded.delimiter)
