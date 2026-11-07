"""Pure: the analysable frame from a parsed grid. Layouts, in the order tried:

1. rate with counts: `before_num, before_den, after_num, after_den` (or synonyms such as signups/sessions);
2. wide: one `before` and one `after` number column (synonyms: prior, baseline, last_week / current, this_week…);
3. long: a period column with exactly two labels and a value column (or a numerator and a denominator).

Every other text column is a segment dimension (at most 4). Blank segment cells become "(blank)"; a
`Total`/`All` row is kept aside to cross-check the sum; duplicate segment keys are summed. Ambiguous
before/after number columns → the caller asks which column is "before".
"""

from __future__ import annotations

from dataclasses import dataclass

import frame as fr
from columns import (
    TOTAL_LABELS,
    numeric_columns,
    order_periods,
    period_column,
    period_of,
    role_of,
)
from frame import Frame
from grid import Grid, parse_number

MAX_DIMS = 4
MAX_OPTIONS = 9


@dataclass(frozen=True)
class Detected:
    frame: Frame | None = None
    ambiguous: tuple[str, ...] = ()   # number columns to choose "before" from
    problem: str = ""


def detect(grid: Grid, forced: tuple[str, str] | None = None) -> Detected:
    numeric = numeric_columns(grid)
    if forced is not None:
        cols = tuple(grid.header.index(c) for c in forced if c in grid.header)
        if len(cols) == 2:
            return _wide(grid, numeric, cols, "additive", "wide", (forced[0], forced[1]))
    rate = _rate_columns(grid, numeric)
    if rate is not None:
        return _wide(grid, numeric, rate, "rate", "rate", ("before", "after"))
    befores = [i for i in numeric if period_of(grid.header[i]) == "before"]
    afters = [i for i in numeric if period_of(grid.header[i]) == "after"]
    if len(befores) == 1 and len(afters) == 1:
        labels = (grid.header[befores[0]], grid.header[afters[0]])
        return _wide(grid, numeric, (befores[0], afters[0]), "additive", "wide", labels)
    long = period_column(grid, numeric)
    if long is not None:
        return _long(grid, numeric, *long)
    if len(numeric) >= 2:
        return Detected(ambiguous=tuple(grid.header[i] for i in numeric[:MAX_OPTIONS]))
    return Detected(problem="I couldn't find a 'before' and an 'after' number column in that table.")


def _rate_columns(grid: Grid, numeric: list[int]) -> tuple[int, ...] | None:
    found: dict[tuple[str, str], list[int]] = {}
    for i in numeric:
        key = (period_of(grid.header[i]), role_of(grid.header[i]))
        if None not in key:
            found.setdefault(key, []).append(i)
    wanted = (("before", "num"), ("before", "den"), ("after", "num"), ("after", "den"))
    if all(len(found.get(k, [])) == 1 for k in wanted):
        return tuple(found[k][0] for k in wanted)
    return None


def _dims(grid: Grid, used: set[int]) -> tuple[list[int], list[str]]:
    dims = [i for i in range(len(grid.header)) if i not in used]
    notes = []
    if len(dims) > MAX_DIMS:
        skipped = ", ".join(grid.header[i] for i in dims[MAX_DIMS:])
        notes.append(f"I analysed the first {MAX_DIMS} segment columns and skipped: {skipped}.")
    return dims[:MAX_DIMS], notes


def _keys(row: tuple[str, ...], dims: list[int]) -> tuple[str, ...]:
    return tuple(row[i].strip() or "(blank)" for i in dims)


def _is_total(keys: tuple[str, ...]) -> bool:
    return any(k.casefold() in TOTAL_LABELS for k in keys)


def _wide(grid: Grid, numeric: list[int], cols: tuple[int, ...], kind: str, layout: str,
          labels: tuple[str, str]) -> Detected:
    dims, notes = _dims(grid, set(numeric))
    if not dims:
        return Detected(problem="I need at least one segment column (channel, country, plan…) next to the numbers.")
    rows: list[fr.Row] = []
    total = None
    skipped = 0
    for row in grid.rows:
        values = [parse_number(row[i]) for i in cols]
        keys = _keys(row, dims)
        if any(v is None for v in values):
            skipped += 1
            continue
        if _is_total(keys):
            total = tuple(values)
            continue
        rows.append(fr.Row(keys, tuple(values)))
    names = tuple(grid.header[i] for i in cols)
    return _finish(grid, kind, layout, dims, rows, names, labels, total, skipped, notes)


def _long(grid: Grid, numeric: list[int], period: int, labels: list[str]) -> Detected:
    if len(labels) != 2:
        shown = ", ".join(labels[:6])
        return Detected(problem=f"I compare exactly two periods, and the period column has {len(labels)} "
                                f"({shown}). Paste only the two periods to compare.")
    (before, after), how = order_periods(labels)
    value_cols = [i for i in numeric if i != period]
    roles = {role_of(grid.header[i]): i for i in value_cols}
    if "num" in roles and "den" in roles:
        kind, cols = "rate", (roles["num"], roles["den"])
    elif value_cols:
        kind, cols = "additive", (value_cols[0],)
    else:
        return Detected(problem="I couldn't find the value column in that table.")
    dims, notes = _dims(grid, set(numeric) | {period})
    if not dims:
        return Detected(problem="I need at least one segment column (channel, country, plan…) next to the numbers.")
    width = len(cols)
    sums: dict[tuple[str, ...], list[float]] = {}
    total, skipped = [0.0] * (2 * width), 0
    has_total = False
    for row in grid.rows:
        values = [parse_number(row[i]) for i in cols]
        side = {before: 0, after: 1}.get(row[period].strip())
        if any(v is None for v in values) or side is None:
            skipped += 1
            continue
        keys = _keys(row, dims)
        target = total if _is_total(keys) else sums.setdefault(keys, [0.0] * (2 * width))
        has_total = has_total or _is_total(keys)
        for k, v in enumerate(values):
            target[side * width + k] += v
    rows = [fr.Row(k, tuple(v)) for k, v in sums.items()]  # (before..., after...), as in the wide layouts
    names = tuple(grid.header[i] for i in cols)
    notes.append(f"Read '{before}' as before and '{after}' as after ({how}).")
    return _finish(grid, kind, "long", dims, rows, names, (before, after),
                   tuple(total) if has_total else None, skipped, notes)


def _finish(grid: Grid, kind: str, layout: str, dims: list[int], rows: list[fr.Row], names: tuple[str, ...],
            labels: tuple[str, str], total, skipped: int, notes: list[str]) -> Detected:
    merged, duplicates = fr.merge(rows)
    if duplicates:
        notes.append(f"{duplicates:,} rows repeated a segment already seen; I summed them.")
    if skipped:
        notes.append(f"{skipped:,} rows had a blank or non-number value, so I skipped them.")
    frame = Frame(kind=kind, layout=layout, dims=tuple(grid.header[i] for i in dims), rows=tuple(merged),
                  value_cols=names, labels=labels, stated_total=tuple(total) if total else None,
                  notes=(*grid.notes, *notes), skipped=skipped, source_rows=grid.source_rows)
    return Detected(frame=frame)
