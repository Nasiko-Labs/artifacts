"""Pure: filter smells (S05 function on a filtered column, S06 leading wildcard, S08 NOT IN subquery,
S14 `= NULL`, S15 BETWEEN with a date end on a timestamp, S16 OR across columns, S18 HAVING without an
aggregate, S23 large IN list). Index wording is conditional unless pasted DDL settles it."""

from __future__ import annotations

import re

from sqlglot import exp

from ast_util import (
    TIMESTAMP_NAME,
    columns_of,
    conjuncts,
    is_literalish,
    render,
    selects,
)
from findings import Ctx, Finding, smell, table_for

COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)
WRAPPERS = (exp.Func, exp.Cast, exp.Add, exp.Sub, exp.Mul, exp.Div)
LARGE_IN_LIST = 200
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def detect(ctx: Ctx) -> list[Finding]:
    found: list[Finding] = []
    for where in ctx.statement.find_all(exp.Where):
        found += _wrapped(ctx, where) + _wildcards(ctx, where) + _ors(ctx, where)
    found += _not_in(ctx) + _null_compare(ctx) + _between(ctx) + _having(ctx) + _in_lists(ctx)
    return found


def owner(ctx: Ctx, column: exp.Column) -> str:
    select = column.find_ancestor(exp.Select)
    if select is not None:
        return table_for(column, select)
    target = ctx.statement.this
    return target.name if isinstance(target, exp.Table) else ""


def index_clause(ctx: Ctx, column: exp.Column, defeated: bool = True) -> str:
    table, name = owner(ctx, column), column.name
    if ctx.schema.has_index(table, name):
        return f"it does not use the index on `{name}` from your DDL" if defeated else \
            f"the index on `{name}` from your DDL cannot be used for it"
    if ctx.schema.has_table(table):
        return f"there is no index on `{name}` in your DDL, so this costs nothing extra today"
    return f"it may prevent index use if `{name}` is indexed"


def _wrapped(ctx: Ctx, where: exp.Where) -> list[Finding]:
    out = []
    for cmp in where.find_all(*COMPARISONS):
        for side, other in ((cmp.this, cmp.expression), (cmp.expression, cmp.this)):
            if isinstance(side, WRAPPERS) and not isinstance(side, exp.AggFunc) and columns_of(side) \
                    and is_literalish(other):
                column = columns_of(side)[0]
                out.append(smell("S05", "medium",
                                 f"`{render(cmp, ctx.dialect)}` wraps `{column.name}` in an expression; "
                                 f"{index_clause(ctx, column)}.",
                                 render(cmp, ctx.dialect),
                                 f"Compare the bare column to a range instead (for dates: `{column.name}` >= start "
                                 f"AND `{column.name}` < next day)."))
                break
    return out


def _wildcards(ctx: Ctx, where: exp.Where) -> list[Finding]:
    out = []
    for like in where.find_all(exp.Like, exp.ILike):
        pattern = like.expression
        if isinstance(pattern, exp.Literal) and pattern.is_string and pattern.this.startswith("%") \
                and isinstance(like.this, exp.Column):
            out.append(smell("S06", "medium",
                             f"The pattern starts with `%`, so the match cannot seek on `{like.this.name}`; "
                             f"{index_clause(ctx, like.this, defeated=False)}.",
                             render(like, ctx.dialect), "Anchor the pattern at the start, or use full-text search."))
    return out


def _ors(ctx: Ctx, where: exp.Where) -> list[Finding]:
    out = []
    for node in where.find_all(exp.Or):
        if isinstance(node.parent, exp.Or) or (isinstance(node.parent, exp.Paren) and
                                               isinstance(node.parent.parent, exp.Or)):
            continue
        if len({c.name.casefold() for c in columns_of(node)}) > 1:
            out.append(smell("S16", "low", "OR across different columns can stop the database from using one "
                                           "index for the whole condition.", render(node, ctx.dialect),
                             "If this is slow, try UNION ALL of the two filtered queries."))
    return out


def _not_in(ctx: Ctx) -> list[Finding]:
    out = []
    for node in ctx.statement.find_all(exp.In):
        negated = node.args.get("negate") or isinstance(node.parent, exp.Not)
        if negated and node.args.get("query") is not None:
            shown = node.parent if isinstance(node.parent, exp.Not) else node
            out.append(smell("S08", "high", "NOT IN with a subquery returns no rows at all if the subquery yields a "
                                            "single NULL.", render(shown, ctx.dialect),
                             "Use NOT EXISTS (SELECT 1 ... WHERE ...) instead."))
    return out


def _null_compare(ctx: Ctx) -> list[Finding]:
    return [smell("S14", "high", f"`{render(n, ctx.dialect)}` compares with NULL using `=` or `<>`; the result is "
                                 "always unknown, so the condition never matches.", render(n, ctx.dialect),
                  "Use IS NULL or IS NOT NULL.")
            for n in ctx.statement.find_all(exp.EQ, exp.NEQ)
            if isinstance(n.this, exp.Null) or isinstance(n.expression, exp.Null)]


def _between(ctx: Ctx) -> list[Finding]:
    out = []
    for node in ctx.statement.find_all(exp.Between):
        high, col = node.args.get("high"), node.this
        if not (isinstance(col, exp.Column) and isinstance(high, exp.Literal) and _DATE_ONLY.match(high.this)):
            continue
        declared = ctx.schema.type_of(owner(ctx, col), col.name)
        is_ts = ("TIME" in declared) if declared else bool(TIMESTAMP_NAME.search(col.name))
        if is_ts:
            out.append(smell("S15", "medium",
                             f"BETWEEN ends at `{high.this}` with no time, which means midnight at the start of that "
                             f"day: if `{col.name}` holds timestamps, the rest of that last day is left out.",
                             render(node, ctx.dialect), f"Use `{col.name}` >= start AND `{col.name}` < the day after "
                                                        "the end."))
    return out


def _having(ctx: Ctx) -> list[Finding]:
    out = []
    for select in selects(ctx.statement):
        for part in conjuncts(select.args.get("having")):
            if not part.find(exp.AggFunc):
                out.append(smell("S18", "low", "This HAVING condition uses no aggregate, so it filters groups by a "
                                               "grouping column; it belongs in WHERE, where it filters rows earlier.",
                                 render(part, ctx.dialect), "Move it to WHERE."))
    return out


def _in_lists(ctx: Ctx) -> list[Finding]:
    return [smell("S23", "low", f"An IN list with {len(n.expressions)} values is long to parse and plan.",
                  render(n.this, ctx.dialect) + f" IN (... {len(n.expressions)} values)",
                  "Load the values into a temporary table and join to it.")
            for n in ctx.statement.find_all(exp.In) if len(n.expressions) >= LARGE_IN_LIST]
