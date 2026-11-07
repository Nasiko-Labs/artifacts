"""Requirement lines (split in code from the Requirements section) and the model's per-line flags,
validated with pydantic. A flag for an unknown id is ignored; an `ambiguous_term` that is not a verbatim
word of the line becomes null."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator

BATCH_SIZE = 30
MIN_WORDS = 3

_BULLET = re.compile(r"^\s*(?:[-*•+]|\d{1,3}[.)]|[a-z][.)]|\[[ x]\])\s+(.*)$", re.IGNORECASE)
_ID_PREFIX = re.compile(r"^\s*(?:\*\*)?(?:[A-Z]{1,4}-?\d{1,3}|R\d{1,3}|US-?\d{1,3})(?:\*\*)?\s*[:.)\-]\s*(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
_SUBHEAD = re.compile(r"^\s*(?:#|\*\*[^*]+\*\*\s*:?\s*$|__[^_]+__\s*:?\s*$)")


@dataclass(frozen=True)
class Requirement:
    id: str       # R1, R2... in PRD order
    line: int     # PRD line number
    text: str     # what the model sees (a table row's cells joined)
    quote: str    # a verbatim piece of the PRD line, for the gap location


class Flags(BaseModel):
    id: str
    single_behavior: bool = True
    has_actor: bool = True
    testable: bool = True
    has_condition_or_limit: bool = True
    contains_solution_detail: bool = False
    ambiguous_term: str | None = None

    @field_validator("ambiguous_term", mode="before")
    @classmethod
    def _term(cls, value: Any) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|") if c.strip()]


def split(body: list[tuple[int, str]]) -> list[Requirement]:
    """Bullets, numbered lines, `R1:` lines and table rows (header row and separator skipped). When the
    section has none of these, each line of at least three words is one requirement."""
    rows = [(n, line) for n, line in body if line.strip()]
    table_headers = {rows[i - 1][0] for i in range(1, len(rows)) if _TABLE_SEP.match(rows[i][1])}
    marked: list[tuple[int, str, str]] = []
    plain: list[tuple[int, str, str]] = []
    for n, line in rows:
        if _TABLE_SEP.match(line) or n in table_headers or _SUBHEAD.match(line):
            continue
        if line.strip().startswith("|"):
            cells = _cells(line)
            if cells:
                marked.append((n, " | ".join(cells), max(cells, key=len)))
            continue
        bullet = _BULLET.match(line) or _ID_PREFIX.match(line)
        if bullet:
            inner = _ID_PREFIX.match(bullet.group(1))
            text = (inner.group(1) if inner else bullet.group(1)).strip()
            marked.append((n, text, text))
        else:
            plain.append((n, line.strip(), line.strip()))
    chosen = marked or plain
    kept = [(n, t, q) for n, t, q in chosen if len(t.split()) >= MIN_WORDS]
    return [Requirement(f"R{i}", n, t, q) for i, (n, t, q) in enumerate(kept, start=1)]


def batches(reqs: list[Requirement], size: int = BATCH_SIZE) -> list[list[Requirement]]:
    return [reqs[i:i + size] for i in range(0, len(reqs), size)]


def parse_flags(data: Any, reqs: Iterable[Requirement]) -> dict[str, Flags]:
    """Validated flags by requirement id. Unknown ids, malformed rows and unverifiable terms are dropped."""
    by_id = {r.id: r for r in reqs}
    rows = data.get("requirements") if isinstance(data, dict) else None
    out: dict[str, Flags] = {}
    for row in rows if isinstance(rows, list) else []:
        try:
            flags = Flags.model_validate(row)
        except ValidationError:
            continue
        req = by_id.get(flags.id)
        if req is None or flags.id in out:
            continue
        term = flags.ambiguous_term
        if term and not re.search(rf"(?<!\w){re.escape(term)}(?!\w)", req.text, re.IGNORECASE):
            flags = flags.model_copy(update={"ambiguous_term": None})
        out[flags.id] = flags
    return out


def is_must(req: Requirement) -> bool:
    return bool(re.search(r"\b(?:must|shall|required|p0)\b", req.text, re.IGNORECASE))
