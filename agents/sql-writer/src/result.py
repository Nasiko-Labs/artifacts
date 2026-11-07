"""Pure: turn a checked attempt into the envelope. Everything here is code, never the model's say-so:

- names the model mentions (backticked identifiers, `tables`, `columns`) must appear in the schema or the SQL,
  else the sentence or entry is dropped and counted;
- claimed speedups ("10x faster") are stripped: only findings are cited;
- mandatory assumptions are appended (dialect assumed, no data queried, unverified rewrite, NULL handling);
- `confidence` and `needs_human` are computed from the checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlglot import exp

import dialect as dialect_mod

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_TICKED = re.compile(r"`([^`]+)`")
_TOKEN = re.compile(r"[A-Za-z_][\w]*")
_CLAIM = re.compile(r"\b\d+(?:\.\d+)?\s*(?:x|×|times|%)\s*(?:faster|quicker|speed|less time|improvement|reduction)|"
                    r"\b(?:faster|quicker|speeds? up|sped up)\b[^.]*?\b\d", re.IGNORECASE)
NO_DATA = "No data was queried; the query was only checked against the schema you gave."
NO_DATA_NO_SCHEMA = "No data was queried, and no schema was given, so table and column names were not checked."
UNVERIFIED = "The rewrite's results are unverified until you run the comparison query."


@dataclass
class Grounding:
    allowed: set[str]
    dropped: int = 0

    def text(self, value: str) -> str:
        kept = []
        for sentence in _SENTENCE.split(value or ""):
            if _CLAIM.search(sentence) or not self._grounded(sentence):
                self.dropped += 1 if sentence.strip() else 0
                continue
            kept.append(sentence)
        return " ".join(kept).strip()

    def items(self, values) -> list[str]:
        out = []
        for value in values if isinstance(values, list) else []:
            cleaned = self.text(str(value)) if isinstance(value, str) else ""
            out += [cleaned] if cleaned else []
        return out

    def names(self, values, valid: set[str]) -> list[str]:
        out = []
        for value in values if isinstance(values, list) else []:
            if isinstance(value, str) and value.casefold().rsplit(".", 1)[-1] in valid:
                out.append(value)
            else:
                self.dropped += 1
        return out

    def _grounded(self, sentence: str) -> bool:
        return all(t.casefold() in self.allowed for chunk in _TICKED.findall(sentence) for t in _TOKEN.findall(chunk))


def allowed_tokens(*texts: str) -> set[str]:
    return {t.casefold() for text in texts for t in _TOKEN.findall(text or "")}


def null_notes(expr: exp.Expression | None) -> list[str]:
    if expr is None:
        return []
    notes = []
    if any(isinstance(n.this, exp.In) and n.this.args.get("query") is not None for n in expr.find_all(exp.Not)):
        notes.append("NULL handling: `NOT IN` returns no rows if its subquery yields a NULL.")
    if expr.find(exp.NEQ) is not None:
        notes.append("NULL handling: `<>` drops rows where the compared column is NULL.")
    if expr.find(exp.Div) is not None:
        notes.append("NULL handling: a NULL divisor gives NULL; a zero divisor errors or gives NULL depending on "
                     "the dialect.")
    return notes


def mandatory(choice: dialect_mod.Choice, has_schema: bool, optimize: bool, expr) -> list[str]:
    out = []
    if choice.assumed:
        out.append(f"Dialect assumed: {choice.label} (none was named).")
    out.append(NO_DATA if has_schema else NO_DATA_NO_SCHEMA)
    if optimize:
        out.append(UNVERIFIED)
    return out + null_notes(expr)


@dataclass(frozen=True)
class Quality:
    validated: bool
    repairs: int
    dropped: int
    dialect_assumed: bool
    warnings: tuple[str, ...] = ()
    unaddressed: tuple[str, ...] = ()
    fan_out: bool = False
    partial_schema: bool = False
    reasons: list[str] = field(default_factory=list)


def confidence(q: Quality) -> str:
    if not q.validated or q.partial_schema or (q.dialect_assumed and q.warnings):
        return "low"
    if q.repairs or q.unaddressed or q.dropped or q.dialect_assumed or q.warnings:
        return "medium"
    return "high"


def needs_human(q: Quality) -> bool:
    return not q.validated or confidence(q) == "low" or q.fan_out
