"""Pure: execute a validated query spec over the header map. filter -> group -> aggregate -> sort -> limit.

Numbers stay `Decimal`. Empty cells are left out of sum/avg/min/max/latest/count and counted in
`blank_count`. Division happens only in `avg` and in period-over-period (`pct = delta / base`, null when
the base is 0 or missing). `latest` is the value at the most recent date of the group (the compare date
column, else the first date column; sheet order when there is none). Every output row carries the sheet
row numbers it came from.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import coltypes
from headers import HeaderMap, SheetRow
from spec import Agg, Filter, QuerySpec, Sort, agg_label

Value = Decimal | date | str | None


@dataclass(frozen=True)
class OutColumn:
    name: str
    type: str  # number | date | text | percent

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type}


@dataclass(frozen=True)
class Result:
    columns: tuple[OutColumn, ...]
    rows: tuple[tuple[Value, ...], ...]
    src_rows: tuple[tuple[int, ...], ...]
    matched: int = 0
    blank_count: int = 0
    total_rows: int = 0               # output rows before `limit`
    skipped_filters: tuple[str, ...] = field(default=())
    windows: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _Typed:
    number: int
    values: tuple[Value, ...]


def execute(table: HeaderMap, spec: QuerySpec) -> Result:
    typed = tuple(_Typed(r.number, _typed_row(table, r)) for r in table.rows)
    rows, skipped = _filtered(table, typed, spec.where)
    if spec.compare is not None:
        result = _period_over_period(table, rows, spec)
    elif spec.agg or spec.group_by:
        result = _grouped(table, rows, spec)
    else:
        result = _selected(table, rows, spec)
    sorted_rows = _sorted(result, spec.sort)
    limited = sorted_rows[:spec.limit] if spec.limit else sorted_rows
    return Result(result.columns, tuple(r for r, _ in limited), tuple(s for _, s in limited), len(rows),
                  result.blank_count, len(sorted_rows), skipped, result.windows)


# --- typing and filters ------------------------------------------------------------------------------------

def typed_value(table: HeaderMap, index: int, cell: object) -> Value:
    if coltypes.is_blank(cell):
        return None
    column = table.columns[index]
    if column.type == "number":
        return coltypes.parse_number(cell)
    if column.type == "date":
        return coltypes.parse_date(cell, column.date_order)
    return str(cell).strip()


def _typed_row(table: HeaderMap, row: SheetRow) -> tuple[Value, ...]:
    return tuple(typed_value(table, i, c) for i, c in enumerate(row.cells))


def _filtered(table: HeaderMap, rows: Sequence[_Typed],
              filters: Sequence[Filter]) -> tuple[tuple[_Typed, ...], tuple[str, ...]]:
    tests: list[Callable[[_Typed], bool]] = []
    skipped: list[str] = []
    for f in filters:
        test = _filter_test(table, f)
        if test is None:
            skipped.append(f"{f.column} {f.op} {f.value}")
        else:
            tests.append(test)
    return tuple(r for r in rows if all(t(r) for t in tests)), tuple(skipped)


def _filter_test(table: HeaderMap, f: Filter) -> Callable[[_Typed], bool] | None:
    index = table.index(f.column)
    kind = table.columns[index].type
    if f.op == "contains":
        needle = str(f.value).casefold()
        return lambda r: r.values[index] is not None and needle in str(r.values[index]).casefold()
    values = f.value if isinstance(f.value, list) else [f.value]
    converted = [_convert(kind, v) for v in values]
    if any(v is None for v in converted):
        return None
    if f.op == "in":
        return lambda r: _key(r.values[index]) in {_key(v) for v in converted}
    if f.op == "between":
        if len(converted) != 2:
            return None
        lo, hi = sorted(converted)
        return lambda r: r.values[index] is not None and lo <= _key(r.values[index]) <= hi
    target = converted[0]
    compare = _COMPARE[f.op]
    return lambda r: r.values[index] is not None and compare(_key(r.values[index]), target)


_COMPARE: Mapping[str, Callable[[Any, Any], bool]] = {
    "=": lambda a, b: a == b, "!=": lambda a, b: a != b, ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
}


def _convert(kind: str, value: object) -> Value:
    if kind == "number":
        return coltypes.parse_number(value)
    if kind == "date":
        return coltypes.parse_date(value, "iso")
    return str(value).strip().casefold()


def _key(value: Value) -> Any:
    return value.casefold() if isinstance(value, str) else value


# --- shapes -----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Shape:
    columns: tuple[OutColumn, ...]
    rows: tuple[tuple[tuple[Value, ...], tuple[int, ...]], ...]
    blank_count: int = 0
    windows: Mapping[str, str] = field(default_factory=dict)


def _selected(table: HeaderMap, rows: Sequence[_Typed], spec: QuerySpec) -> _Shape:
    names = spec.select or list(table.names)
    idx = [table.index(n) for n in names]
    columns = tuple(OutColumn(n, table.columns[i].type) for n, i in zip(names, idx, strict=True))
    return _Shape(columns, tuple((tuple(r.values[i] for i in idx), (r.number,)) for r in rows))


def _groups(table: HeaderMap, rows: Sequence[_Typed], keys: Sequence[str]) -> dict[tuple, list[_Typed]]:
    idx = [table.index(k) for k in keys]
    groups: dict[tuple, list[_Typed]] = {}
    for r in rows:
        groups.setdefault(tuple(_key(r.values[i]) for i in idx), []).append(r)
    return groups


def _group_values(table: HeaderMap, members: Sequence[_Typed], keys: Sequence[str]) -> tuple[Value, ...]:
    return tuple(members[0].values[table.index(k)] for k in keys)


def _grouped(table: HeaderMap, rows: Sequence[_Typed], spec: QuerySpec) -> _Shape:
    aggs = list(spec.agg)
    columns = (*(OutColumn(k, table.column(k).type) for k in spec.group_by),
               *(OutColumn(agg_label(a.column, a.fn), _agg_type(table, a)) for a in aggs))
    if not aggs:
        columns = (*columns, OutColumn("rows", "number"))
    out = []
    blanks = 0
    groups = _groups(table, rows, spec.group_by) if spec.group_by else {(): list(rows)}
    for members in groups.values():
        values: list[Value] = list(_group_values(table, members, spec.group_by)) if members else []
        for a in aggs:
            value, blank = aggregate(table, members, a)
            values.append(value)
            blanks += blank
        if not aggs:
            values.append(Decimal(len(members)))
        if members or aggs:
            out.append((tuple(values), tuple(m.number for m in members)))
    return _Shape(columns, tuple(out), blanks)


def _period_over_period(table: HeaderMap, rows: Sequence[_Typed], spec: QuerySpec) -> _Shape:
    cmp = spec.compare
    span = (cmp.end - cmp.start).days + 1
    base_start, base_end = cmp.start - timedelta(days=span), cmp.start - timedelta(days=1)
    di = table.index(cmp.date_column)
    current = [r for r in rows if r.values[di] is not None and cmp.start <= r.values[di] <= cmp.end]
    base = [r for r in rows if r.values[di] is not None and base_start <= r.values[di] <= base_end]
    aggs = list(spec.agg) or [Agg(column=cmp.date_column, fn="count")]
    columns: tuple[OutColumn, ...] = tuple(OutColumn(k, table.column(k).type) for k in spec.group_by)
    for a in aggs:
        label, kind = agg_label(a.column, a.fn), _agg_type(table, a)
        columns = (*columns, OutColumn(f"{label} current", kind), OutColumn(f"{label} previous", kind),
                   OutColumn(f"{label} change", kind), OutColumn(f"{label} change %", "percent"))
    cur_groups = _groups(table, current, spec.group_by) if spec.group_by else {(): current}
    base_groups = _groups(table, base, spec.group_by) if spec.group_by else {(): base}
    out, blanks = [], 0
    for key in dict.fromkeys((*cur_groups, *base_groups)):
        cur, prev = cur_groups.get(key, []), base_groups.get(key, [])
        values: list[Value] = list(_group_values(table, cur or prev, spec.group_by))
        for a in aggs:
            (c, cb), (p, pb) = aggregate(table, cur, a), aggregate(table, prev, a)
            blanks += cb + pb
            values.extend([c, p, *change(c, p)])
        out.append((tuple(values), tuple(m.number for m in (*cur, *prev))))
    windows = {"current": f"{cmp.start.isoformat()}..{cmp.end.isoformat()}",
               "previous": f"{base_start.isoformat()}..{base_end.isoformat()}"}
    return _Shape(columns, tuple(out), blanks, windows)


def change(current: Value, base: Value) -> tuple[Value, Value]:
    """(delta, pct) where pct is a fraction; pct is None when the base is 0 or either side is missing."""
    if not isinstance(current, Decimal) or not isinstance(base, Decimal):
        return None, None
    delta = current - base
    return delta, (None if base == 0 else delta / base)


# --- aggregates -------------------------------------------------------------------------------------------

def aggregate(table: HeaderMap, members: Sequence[_Typed], a: Agg) -> tuple[Value, int]:
    """(value, blank cells left out)."""
    i = table.index(a.column)
    present = [m for m in members if m.values[i] is not None]
    blanks = len(members) - len(present)
    if a.fn == "count":
        return Decimal(len(present)), blanks
    if a.fn == "latest":
        return _latest(table, present, i), blanks
    values = [m.values[i] for m in present]
    if a.fn in ("sum", "avg"):
        numbers = [v for v in values if isinstance(v, Decimal)]
        if not numbers:
            return None, blanks
        total = sum(numbers, Decimal(0))
        return (total if a.fn == "sum" else total / len(numbers)), blanks
    if not values:
        return None, blanks
    return (min(values, key=_key) if a.fn == "min" else max(values, key=_key)), blanks


def _latest(table: HeaderMap, present: Sequence[_Typed], index: int) -> Value:
    if not present:
        return None
    di = next((j for j, c in enumerate(table.columns) if c.type == "date"), None)
    dated = [m for m in present if di is not None and m.values[di] is not None]
    if not dated:
        return present[-1].values[index]
    best = dated[0]
    for m in dated[1:]:
        if m.values[di] >= best.values[di]:
            best = m
    return best.values[index]


def _agg_type(table: HeaderMap, a: Agg) -> str:
    return "number" if a.fn in ("sum", "avg", "count") else table.column(a.column).type


# --- sort -------------------------------------------------------------------------------------------------

def _sorted(shape: _Shape, sorts: Sequence[Sort]) -> list[tuple[tuple[Value, ...], tuple[int, ...]]]:
    rows = list(shape.rows)
    names = [c.name for c in shape.columns]
    for s in reversed(list(sorts)):
        i = _sort_index(names, s.column)
        if i is None:
            continue
        present = [r for r in rows if r[0][i] is not None]
        missing = [r for r in rows if r[0][i] is None]
        rows = sorted(present, key=lambda r, i=i: _key(r[0][i]), reverse=s.desc) + missing
    return rows


def _sort_index(names: Sequence[str], column: str) -> int | None:
    key = column.casefold()
    for test in (lambda n: n.casefold() == key, lambda n: n.casefold().startswith(key + " (")):
        hit = next((i for i, n in enumerate(names) if test(n)), None)
        if hit is not None:
            return hit
    return None
