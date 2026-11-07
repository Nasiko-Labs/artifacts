"""Pure: lint rules Q09-Q17 (see lint.py for Q01-Q08). Type, partition and index rules use only what the
pasted schema states; a rule whose input is missing (no types, no PARTITION BY, no index list) stays quiet."""

from __future__ import annotations

from sqlglot import exp

from findings import Finding
from schema import type_family
from sqlshape import column_table, comparisons, own_nodes, snippet

DEEP_OFFSET = 10_000
_APPROX = {"bigquery": "APPROX_COUNT_DISTINCT", "snowflake": "APPROX_COUNT_DISTINCT", "duckdb":
           "approx_count_distinct", "clickhouse": "uniq", "trino": "approx_distinct", "redshift":
           "APPROXIMATE COUNT(DISTINCT ...)", "tsql": "APPROX_COUNT_DISTINCT"}


def per_select(select: exp.Select, dialect: str, scope, indexes: tuple) -> list[Finding]:
    """`indexes` are the pasted CREATE INDEX statements; Q17 runs only when there are some."""
    out = _q09(select, dialect) + _q11(select, dialect, scope) + _q12(select, dialect, scope)
    out += _q13(select, dialect, scope) + _q14(select, dialect, scope) + _q15(select, dialect)
    out += _q16(select, dialect)
    return out + (_q17(select, dialect, scope, indexes) if indexes else [])


def whole(expr: exp.Expression, dialect: str) -> list[Finding]:
    return _q10(expr, dialect)


def _int_literal(node) -> int | None:
    if isinstance(node, exp.Offset):
        node = node.expression
    if isinstance(node, exp.Literal) and not node.is_string and node.this.isdigit():
        return int(node.this)
    return None


def _q09(select: exp.Select, dialect: str) -> list[Finding]:
    value = _int_literal(select.args.get("offset"))
    if value is None or value < DEEP_OFFSET:
        return []
    return [Finding("", "Q09", f"OFFSET {value}", f"The database still reads and discards {value:,} rows; page "
                    "by the last seen key instead (WHERE key > :last ORDER BY key LIMIT n).")]


def _branch_literals(select: exp.Expression) -> dict[str, str]:
    where = select.args.get("where") if isinstance(select, exp.Select) else None
    out = {}
    for eq in (where.find_all(exp.EQ) if where is not None else []):
        if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Literal):
            out[eq.this.name.casefold()] = eq.expression.this
    return out


def _q10(expr: exp.Expression, dialect: str) -> list[Finding]:
    out = []
    for union in expr.find_all(exp.Union):
        if not union.args.get("distinct"):
            continue
        left, right = _branch_literals(union.left), _branch_literals(union.right)
        if any(k in right and right[k] != v for k, v in left.items()):
            out.append(Finding("", "Q10", "UNION", "The branches filter the same column on different values, so "
                               "they can't overlap; UNION ALL skips the de-duplication sort."))
    return out


def _family(col: exp.Column, scope) -> str:
    table = column_table(col, scope)
    column = table.column(col.name) if table else None
    return type_family(column.type) if column else ""


def _q11(select: exp.Select, dialect: str, scope) -> list[Finding]:
    out = []
    for cmp in comparisons(select):
        col, lit = cmp.this, cmp.args.get("expression")
        if not isinstance(col, exp.Column) or not isinstance(lit, exp.Literal):
            continue
        family = _family(col, scope)
        if (family in ("int", "number") and lit.is_string) or (family == "text" and not lit.is_string):
            out.append(Finding("", "Q11", snippet(cmp, dialect), f"`{col.name}` is {family} but is compared with "
                               "a literal of another type; the implicit cast can block index use or compare "
                               "wrongly. Use a literal of the column's type."))
    return out


def _q12(select: exp.Select, dialect: str, scope) -> list[Finding]:
    out = []
    for join in select.args.get("joins") or []:
        on = join.args.get("on")
        for eq in (on.find_all(exp.EQ) if on is not None else []):
            if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                a, b = _family(eq.this, scope), _family(eq.expression, scope)
                if a and b and a != b and {a, b} != {"int", "number"}:
                    out.append(Finding("", "Q12", snippet(eq, dialect), f"The join keys are declared {a} and {b}; "
                                       "every row is cast before matching, which blocks index use."))
    return out


def _q13(select: exp.Select, dialect: str, scope) -> list[Finding]:
    if not select.args.get("joins"):
        return []
    out = []
    for agg in own_nodes(select, (exp.Sum, exp.Avg)):
        col = agg.find(exp.Column)
        parent = column_table(col, scope) if col is not None else None
        if parent is None:
            continue
        children = [t.name for t in scope.values() if t is not parent
                    and any(fk.ref_table.casefold() == parent.name.casefold() for fk in t.foreign_keys)]
        if children:
            out.append(Finding("", "Q13", snippet(agg, dialect), f"`{parent.name}` is joined to `{children[0]}` "
                               f"(one-to-many), so each `{parent.name}` row repeats and this total counts it once "
                               "per match. Aggregate the child first, or sum from the parent alone."))
    return out


def _q14(select: exp.Select, dialect: str, scope) -> list[Finding]:
    where = select.args.get("where")
    filtered = {c.name.casefold() for c in where.find_all(exp.Column)} if where is not None else set()
    return [Finding("", "Q14", t.name, f"`{t.name}` is partitioned by {', '.join(t.partition_by)} but the query "
                    "has no filter on it, so every partition is scanned.", t.name, t.partition_by)
            for t in scope.values() if t.partition_by and not filtered & {p.casefold() for p in t.partition_by}]


def _q15(select: exp.Select, dialect: str) -> list[Finding]:
    if dialect not in _APPROX:
        return []
    return [Finding("", "Q15", snippet(c, dialect), f"Exact distinct counts over large, high-cardinality columns "
                    f"are costly; if an estimate is enough, {_APPROX[dialect]} is much cheaper.")
            for c in own_nodes(select, exp.Count) if isinstance(c.this, exp.Distinct)]


def _q16(select: exp.Select, dialect: str) -> list[Finding]:
    nested = select.find_ancestor(exp.Subquery, exp.CTE) is not None
    if not nested or not select.args.get("order") or select.args.get("limit") or select.args.get("fetch"):
        return []
    return [Finding("", "Q16", snippet(select.args["order"], dialect), "An ORDER BY inside a subquery or CTE "
                    "without a LIMIT sorts rows the outer query doesn't keep in that order; drop it.")]


def _q17(select: exp.Select, dialect: str, scope, indexes: tuple) -> list[Finding]:
    out = []
    for cmp in comparisons(select):
        for col in (n for n in (cmp.this, cmp.args.get("expression")) if isinstance(n, exp.Column)):
            table = column_table(col, scope)
            if table is None or table.column(col.name) is None or _covered(table, col.name, indexes):
                continue
            out.append(Finding("", "Q17", snippet(cmp, dialect), f"No index or primary key on `{table.name}` "
                               f"starts with `{col.name}`, so this filter scans the table.", table.name,
                               (col.name,)))
    return out


def _covered(table, column: str, indexes: tuple) -> bool:
    leading = {table.primary_key[0].casefold()} if table.primary_key else set()
    leading |= {i.columns[0].casefold() for i in indexes if i.table.casefold() == table.name.casefold() and i.columns}
    return column.casefold() in leading
