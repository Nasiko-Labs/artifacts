"""Pure: what to watch. Rule phrases in the user's words are matched in code against the sheet's headers
(case-insensitive): "<col> changes", "<col> within N days", "<col> blank [when <col> is <value>]", "new rows",
"removed rows", "key <col>". Phrases that look like rules but match nothing are handed to one model call
(validated here: header names only, days in range, a `when` value verbatim in the user's words or the cells).
Unset fields fall back to defaults chosen from the headers (least setup)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from models import MAX_DAYS, DateRule, Required, RuleSpec, When
from table import Table, is_blank

DEFAULT_DAYS = 3
ROW_KEY = "#row"  # the user chose row numbers as the key
_STATE_HEADER = re.compile(r"\b(status|state|stage|phase)\b", re.IGNORECASE)
_DATE_HEADER = re.compile(r"\b(due|deadline|date|eta|renewal|expires?|expiry)\b", re.IGNORECASE)
_RULE_WORDS = re.compile(r"\b(chang\w*|blank|empty|missing|unfilled|within|days?|overdue|required|filled|"
                         r"new rows?|added rows?|removed|deleted|identif\w+|key)\b", re.IGNORECASE)
_CHECK_WORDS = re.compile(r"\bwhat(?:'s| has| have)? changed\b|\bany(?:thing)? changed?\b|\bsince (?:the )?last\b",
                          re.IGNORECASE)
_FRAGMENT_SPLIT = re.compile(r"[,;:\n]|\s+(?:and|or|plus|also)\s+", re.IGNORECASE)
_NEW_ROWS = re.compile(r"\b(?:new|added) rows?\b|\brows? (?:are |get |being )?added\b", re.IGNORECASE)
_REMOVED_ROWS = re.compile(r"\b(?:removed|deleted) rows?\b|\brows? (?:are |get |being )?(?:removed|deleted)\b",
                           re.IGNORECASE)
_IGNORE = r"\b(?:ignore|don'?t (?:track|watch|report)|skip|no)\s+"


@dataclass(frozen=True)
class Parsed:
    spec: RuleSpec
    unmatched: tuple[str, ...]       # rule-looking fragments code could not map to a header


@dataclass(frozen=True)
class Resolved:
    """The rules this check runs with: stated values, else defaults."""

    key_column: str | None           # None: rows are keyed by row number
    state_column: str | None
    date_rules: tuple[tuple[str, int], ...]
    required: tuple[Required, ...]
    track_new_rows: bool
    track_removed_rows: bool
    display_column: str | None
    defaults: tuple[str, ...]        # which fields were defaulted (said in the reply)

    def to_dict(self) -> dict:
        return {"key_column": self.key_column, "state_column": self.state_column,
                "date_rules": [{"column": c, "days": d} for c, d in self.date_rules],
                "required": [r.model_dump() for r in self.required], "track_new_rows": self.track_new_rows,
                "track_removed_rows": self.track_removed_rows, "display_column": self.display_column,
                "defaults": list(self.defaults)}

    @property
    def tracked(self) -> tuple[str, ...]:
        cols = [self.key_column, self.display_column, self.state_column, *(c for c, _ in self.date_rules),
                *(r.column for r in self.required), *(r.when.column for r in self.required if r.when)]
        return tuple(dict.fromkeys(c for c in cols if c))


# --- parsing the user's words --------------------------------------------------------------------------------

def parse(text: str, header: Sequence[str]) -> Parsed:
    if not header or not text.strip():
        return Parsed(RuleSpec(), ())
    col = _column_alternation(header)
    spans: list[tuple[int, int]] = []
    state = key = None
    date_rules: list[DateRule] = []
    required: list[Required] = []

    for m in re.finditer(rf"(?:\bwhen\s+)?\b({col})\s+(?:changes?|changed|moves?|updates?)\b|"
                         rf"\bchanges?\s+(?:to|in|of)\s+(?:the\s+)?({col})\b", text, re.IGNORECASE):
        state = _canonical(m.group(1) or m.group(2), header)
        spans.append(m.span())
    for m in re.finditer(rf"\b({col})\s+(?:date\s+)?(?:is\s+|are\s+)?(?:due\s+)?(?:within|in(?: the next)?|less than)"
                         rf"\s+(\d{{1,3}})\s+days?\b|\b(\d{{1,3}})\s+days?\s+(?:before|ahead of|from)\s+(?:the\s+)?"
                         rf"({col})\b", text, re.IGNORECASE):
        column, days = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        if int(days) <= MAX_DAYS:
            date_rules = _with_date(date_rules, DateRule(column=_canonical(column, header), days=int(days)))
        spans.append(m.span())
    for m in re.finditer(rf"\b({col})\s+(?:is\s+|are\s+|cell\s+)?(?:blank|empty|missing|unfilled|not filled|required)"
                         rf"(?:\s+(?:when|if|where|while)\s+({col})\s+(?:is|=|equals)\s+[\"“']?([^,;\"”'\n]+?)[\"”']?"
                         rf"(?=$|[,;.\n]|\s+(?:and|or)\b))?|\b(?:no|missing|blank|empty)\s+({col})\b", text,
                         re.IGNORECASE):
        column = m.group(1) or m.group(4)
        when = When(column=_canonical(m.group(2), header), value=m.group(3).strip()) if m.group(2) else None
        required = _with_required(required, Required(column=_canonical(column, header), when=when))
        spans.append(m.span())
    for m in re.finditer(rf"\b(?:key(?:\s+column)?|identify rows? by|rows? (?:are\s+)?identified by|id column)\s*"
                         rf"(?:is|=|:)?\s*(?:the\s+)?({col})\b", text, re.IGNORECASE):
        key = _canonical(m.group(1), header)
        spans.append(m.span())
    new_rows, spans = _toggle(_NEW_ROWS, text, spans)
    removed_rows, spans = _toggle(_REMOVED_ROWS, text, spans)

    spec = RuleSpec(state_column=state, key_column=key, date_rules=date_rules, required=required,
                    track_new_rows=new_rows, track_removed_rows=removed_rows)
    return Parsed(spec, _unmatched(text, spans))


def _toggle(pattern: re.Pattern, text: str, spans: list[tuple[int, int]]) -> tuple[bool | None, list]:
    m = pattern.search(text)
    if not m:
        return None, spans
    ignored = re.search(_IGNORE + r"(?:\w+\s+){0,2}$", text[:m.start()], re.IGNORECASE)
    return not ignored, [*spans, m.span()]


def _unmatched(text: str, spans: Sequence[tuple[int, int]]) -> tuple[str, ...]:
    out: list[str] = []
    pos = 0
    for piece in _FRAGMENT_SPLIT.split(text):
        start = text.find(piece, pos)
        end = start + len(piece)
        pos = max(pos, end)
        fragment = piece.strip()
        if not fragment or not _RULE_WORDS.search(fragment) or _CHECK_WORDS.search(fragment):
            continue
        if any(s < end and e > start for s, e in spans):
            continue
        out.append(fragment)
    return tuple(out)


def _column_alternation(header: Sequence[str]) -> str:
    return "|".join(re.escape(h) for h in sorted(header, key=len, reverse=True))


def _canonical(name: str, header: Sequence[str]) -> str:
    return next(h for h in header if h.casefold() == name.strip().casefold())


def _with_date(rules: list[DateRule], rule: DateRule) -> list[DateRule]:
    return [*(r for r in rules if r.column != rule.column), rule]


def _with_required(rules: list[Required], rule: Required) -> list[Required]:
    return [*(r for r in rules if r.column != rule.column), rule]


# --- the model's translation -----------------------------------------------------------------------------

def validate_model_rules(raw: Any, header: Sequence[str], seen: str) -> tuple[RuleSpec, int]:
    """The model's rule spec, with every column checked against the headers and every `when` value checked
    verbatim against `seen` (the user's words and the cells). Returns the kept spec and how many values were
    dropped."""
    if not isinstance(raw, Mapping):
        return RuleSpec(), 0
    known = {h.casefold(): h for h in header}
    dropped = 0

    def column(value: Any) -> str | None:
        nonlocal dropped
        if not isinstance(value, str) or not value.strip():
            return None
        found = known.get(value.strip().casefold())
        dropped += found is None
        return found

    date_rules: list[DateRule] = []
    for item in raw.get("date_rules") or ():
        col = column(item.get("column")) if isinstance(item, Mapping) else None
        days = item.get("days") if isinstance(item, Mapping) else None
        if col and isinstance(days, int) and not isinstance(days, bool) and 0 <= days <= MAX_DAYS:
            date_rules = _with_date(date_rules, DateRule(column=col, days=days))
    required: list[Required] = []
    for item in raw.get("required") or ():
        col = column(item.get("column")) if isinstance(item, Mapping) else None
        if not col:
            continue
        when = _when(item.get("when"), column, seen)
        if when is False:
            dropped += 1
            continue
        required = _with_required(required, Required(column=col, when=when or None))
    try:
        spec = RuleSpec(state_column=column(raw.get("state_column")), key_column=column(raw.get("key_column")),
                        date_rules=date_rules, required=required,
                        track_new_rows=_bool(raw.get("track_new_rows")),
                        track_removed_rows=_bool(raw.get("track_removed_rows")))
    except ValidationError:
        return RuleSpec(), dropped + 1
    return spec, dropped


def _when(raw: Any, column, seen: str) -> When | None | bool:
    """None: no condition; False: a condition that could not be verified (dropped with its rule)."""
    if not isinstance(raw, Mapping):
        return None
    col, value = column(raw.get("column")), str(raw.get("value") or "").strip()
    if not col or not value or not _appears(value, seen):
        return False
    return When(column=col, value=value)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _appears(value: str, seen: str) -> bool:
    return bool(_squash(value)) and _squash(value) in _squash(seen)


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


# --- merging and defaults ----------------------------------------------------------------------------------

def merge(base: RuleSpec, patch: RuleSpec) -> RuleSpec:
    """`patch` wins where it says something; list rules are added (same column: replaced)."""
    dates_ = list(base.date_rules)
    for rule in patch.date_rules:
        dates_ = _with_date(dates_, rule)
    required = list(base.required)
    for rule in patch.required:
        required = _with_required(required, rule)
    return RuleSpec(
        state_column=patch.state_column or base.state_column,
        key_column=patch.key_column or base.key_column,
        date_rules=dates_, required=required,
        track_new_rows=base.track_new_rows if patch.track_new_rows is None else patch.track_new_rows,
        track_removed_rows=base.track_removed_rows if patch.track_removed_rows is None else patch.track_removed_rows,
    )


def unique_columns(table: Table) -> tuple[str, ...]:
    """Columns whose values are all filled and all different (after trimming, ignoring case)."""
    out = []
    for i, name in enumerate(table.header):
        values = [r[i].strip().casefold() for r in table.rows]
        if all(values) and len(set(values)) == len(values):
            out.append(name)
    return tuple(out)


def key_candidates(table: Table, limit: int = 8) -> tuple[str, ...]:
    """Columns ranked by how many distinct filled values they hold, for the key question."""
    def distinct(i: int) -> int:
        return len({r[i].strip().casefold() for r in table.rows if r[i].strip()})
    ranked = sorted(range(len(table.header)), key=lambda i: -distinct(i))
    return tuple(table.header[i] for i in ranked if distinct(i) > 1)[:limit]


def resolve(spec: RuleSpec, table: Table, prior_key: str | None = None) -> Resolved:
    header = table.header
    defaults: list[str] = []
    key: str | None = None
    if spec.key_column == ROW_KEY:
        key = None
    elif table.index(spec.key_column) is not None:
        key = header[table.index(spec.key_column)]
    elif prior_key and table.index(prior_key) is not None:
        key = header[table.index(prior_key)]
    else:
        unique = unique_columns(table)
        key = unique[0] if unique else None
        defaults.append("key")
    state = spec.state_column if table.index(spec.state_column) is not None else None
    if state is None and spec.state_column is None:
        state = next((h for h in header if _STATE_HEADER.search(h)), None)
        defaults.append("state")
    date_rules = tuple((r.column, r.days) for r in spec.date_rules if table.index(r.column) is not None)
    if not spec.date_rules:
        date_rules = tuple((h, DEFAULT_DAYS) for h in header if _DATE_HEADER.search(h) and h != state)
        defaults.append("dates")
    required = tuple(r for r in spec.required if table.index(r.column) is not None
                     and (r.when is None or table.index(r.when.column) is not None))
    return Resolved(key_column=key, state_column=state, date_rules=date_rules, required=required,
                    track_new_rows=spec.track_new_rows is not False,
                    track_removed_rows=spec.track_removed_rows is not False,
                    display_column=_display_column(table, key), defaults=tuple(defaults))


def _display_column(table: Table, key: str | None) -> str | None:
    """The first mostly-text column (the row's human name); the key when there is none."""
    for i, name in enumerate(table.header):
        filled = [r[i] for r in table.rows if not is_blank(r[i])]
        texty = [c for c in filled if re.search(r"[A-Za-z]", c) and not re.fullmatch(r"[\d\s/.,:-]+", c)]
        if filled and len(texty) * 2 >= len(filled) and not _DATE_HEADER.search(name):
            return name
    return key


def describe(resolved: Resolved) -> str:
    parts = []
    if resolved.state_column:
        parts.append(f"{resolved.state_column} changes")
    parts.extend(f"{c} within {d} day{'s' if d != 1 else ''}" for c, d in resolved.date_rules)
    for r in resolved.required:
        when = f" when {r.when.column} is \"{r.when.value}\"" if r.when else ""
        parts.append(f"{r.column} blank{when}")
    rows = [w for w, on in (("new rows", resolved.track_new_rows), ("removed rows", resolved.track_removed_rows)) if on]
    if rows:
        parts.append(" and ".join(rows))
    key = resolved.key_column or "row number"
    return f"Watching: {'; '.join(parts) or 'row changes'}. Rows identified by {key}."
