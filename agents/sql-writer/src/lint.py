"""Pure: static lint over the parsed query (sqlglot AST), rules Q01-Q08. Q09-Q17 are in lint_more.py.
Each rule looks at one SELECT at a time with its own table aliases, so nested queries are judged on their own."""

from __future__ import annotations

from sqlglot import exp

import lint_more
from findings import Finding, numbered
from schema import Schema
from sqlshape import (
    aliases,
    column_table,
    comparisons,
    own_nodes,
    selects,
    snippet,
    where_conditions,
)


def lint(expr: exp.Expression, dialect: str, schema: Schema) -> list[Finding]:
    found: list[Finding] = []
    every = selects(expr)
    for select in every:
        scope = aliases(select, schema)
        found += _q01(select, dialect, len(every) > 1)
        found += _q02(select, dialect, scope)
        found += _q03(select, dialect)
        found += _q04(select, dialect, scope, schema)
        found += _q05(select, dialect)
        found += _q06(select, dialect)
        found += _q07(select, dialect)
        found += _q08(select, dialect)
        found += lint_more.per_select(select, dialect, scope, schema.indexes)
    found += lint_more.whole(expr, dialect)
    return numbered(found)


def _q01(select: exp.Select, dialect: str, nested: bool) -> list[Finding]:
    stars = [e for e in select.expressions if isinstance(e, exp.Star) or (isinstance(e, exp.Column)
                                                                         and isinstance(e.this, exp.Star))]
    if not stars or not (nested or select.args.get("joins") or select.args.get("group")):
        return []
    return [Finding("", "Q01", snippet(stars[0], dialect), "Selecting every column reads and ships columns the "
                    "result may not need and breaks when the table changes; list the columns you use.")]


def _wrapped_column(side: exp.Expression | None) -> exp.Column | None:
    if side is None or isinstance(side, (exp.Column, exp.Literal)) or side.find(exp.AggFunc) is not None:
        return None
    if isinstance(side, (exp.Func, exp.Binary, exp.Cast, exp.Paren)) and not isinstance(side, exp.Subquery):
        return side.find(exp.Column)
    return None


def _q02(select: exp.Select, dialect: str, scope) -> list[Finding]:
    out = []
    for cmp in comparisons(select):
        if isinstance(cmp, (exp.Like, exp.ILike)):
            continue
        for side in (cmp.this, cmp.args.get("expression")):
            col = _wrapped_column(side)
            if col is None:
                continue
            table = column_table(col, scope)
            out.append(Finding("", "Q02", snippet(cmp, dialect), f"`{snippet(side, dialect)}` wraps the column "
                               f"`{col.name}`, so an index on it can't be used; compare the bare column with a "
                               "range, or index the expression.", table.name if table else "", (col.name,),
                               snippet(side, dialect)))
            break
    return out


def _q03(select: exp.Select, dialect: str) -> list[Finding]:
    out = []
    for like in own_nodes(select, (exp.Like, exp.ILike)):
        pattern = like.args.get("expression")
        if isinstance(pattern, exp.Literal) and pattern.is_string and pattern.this.startswith("%"):
            fix = " A trigram (pg_trgm) or full-text index can serve it." if dialect == "postgres" else ""
            out.append(Finding("", "Q03", snippet(like, dialect), "A pattern that starts with `%` can't use a "
                               "b-tree index, so every row is scanned." + fix))
    return out


def _nullable(col: exp.Expression | None, scope) -> bool:
    if not isinstance(col, exp.Column):
        return True
    table = column_table(col, scope)
    column = table.column(col.name) if table else None
    return column is None or not column.not_null


def _q04(select: exp.Select, dialect: str, scope, schema: Schema) -> list[Finding]:
    out = []
    for neg in own_nodes(select, exp.Not):
        inn = neg.this
        if not isinstance(inn, exp.In) or inn.args.get("query") is None:
            continue
        inner = inn.args["query"].find(exp.Select)
        inner_col = inner.expressions[0] if inner is not None and inner.expressions else None
        if not (_nullable(inn.this, scope) or _nullable(inner_col, aliases(inner, schema) if inner else {})):
            continue
        out.append(Finding("", "Q04", snippet(neg, dialect), "If the subquery returns a NULL, `NOT IN` returns no "
                           "rows at all, and it often plans worse; use `NOT EXISTS` with a correlated predicate."))
    return out


def _q05(select: exp.Select, dialect: str) -> list[Finding]:
    out = []
    where = select.args.get("where")
    for join in select.args.get("joins") or []:
        if join.args.get("on") is not None or join.args.get("using"):
            continue
        alias = join.this.alias_or_name.casefold() if isinstance(join.this, exp.Table) else ""
        linked = where is not None and any(
            isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column)
            and alias in {eq.this.table.casefold(), eq.expression.table.casefold()}
            and eq.this.table.casefold() != eq.expression.table.casefold()
            for eq in where.find_all(exp.EQ))
        if not linked:
            out.append(Finding("", "Q05", snippet(join, dialect), "This join has no predicate, so every row is "
                               "paired with every row of the other side (a cartesian product)."))
    return out


def _q06(select: exp.Select, dialect: str) -> list[Finding]:
    where = select.args.get("where")
    for node in (where.find_all(exp.Or) if where is not None else []):
        if node.find_ancestor(exp.Select) is not select or isinstance(node.parent, exp.Or):
            continue
        left = {c.name.casefold() for c in node.this.find_all(exp.Column)}
        right = {c.name.casefold() for c in node.expression.find_all(exp.Column)}
        if left and right and left != right:
            return [Finding("", "Q06", snippet(node, dialect), "OR across different columns usually stops index "
                            "use; two queries joined with UNION ALL (excluding the overlap) can each use one.")]
    return []


def _q07(select: exp.Select, dialect: str) -> list[Finding]:
    if not select.args.get("distinct") or not select.args.get("joins"):
        return []
    return [Finding("", "Q07", "SELECT DISTINCT ... JOIN", "DISTINCT over a join often hides rows duplicated by "
                    "the join; filter with EXISTS or aggregate before joining instead.")]


def _q08(select: exp.Select, dialect: str) -> list[Finding]:
    out = []
    for projection in select.expressions:
        for sub in projection.find_all(exp.Subquery):
            inner = sub.find(exp.Select)
            if inner is None:
                continue
            local = {t.alias_or_name.casefold() for t in inner.find_all(exp.Table)}
            outer = [c for cond in where_conditions(inner) for c in cond.find_all(exp.Column)
                     if c.table and c.table.casefold() not in local]
            if outer:
                out.append(Finding("", "Q08", snippet(sub, dialect), "This subquery runs once per output row; "
                                   "join a pre-aggregated subquery (GROUP BY the key) instead."))
    return out
