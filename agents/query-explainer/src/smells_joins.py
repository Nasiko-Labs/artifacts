"""Pure: join smells (S03 cartesian, S04 non-equi, S07 left join defeated, S09 DISTINCT over fan-out,
S10 aggregate after fan-out, S11 ORDER BY + LIMIT without tie-breaker, S13 correlated subquery).

"Known unique" comes from pasted DDL only. A join on a column named `id` is treated as the conventional
primary key for S09/S10 (it still yields the A02 assumption), so the common `JOIN customers c ON c.id = ...`
is not reported as fan-out."""

from __future__ import annotations

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import build_scope

from ast_util import (
    CONVENTIONAL_KEY,
    JoinInfo,
    aggregates,
    columns_of,
    conjuncts,
    equalities,
    joins,
    qualifier,
    render,
    right_keys,
    selects,
)
from findings import Ctx, Finding, smell, table_for

FANOUT_AGGS = (exp.Sum, exp.Count, exp.Avg)


def detect(ctx: Ctx) -> list[Finding]:
    found: list[Finding] = []
    for select in selects(ctx.statement):
        infos = joins(select)
        found += _cartesian(ctx, select, infos) + _non_equi(ctx, infos) + _left_defeated(ctx, select, infos)
        found += _fanout(ctx, select, infos) + _tie_breaker(ctx, select)
    return found + _correlated(ctx)


def is_unique_join(ctx: Ctx, info: JoinInfo) -> bool:
    return any(ctx.schema.is_unique(info.table, key) for key in right_keys(info))


def may_fan_out(ctx: Ctx, info: JoinInfo) -> bool:
    keys = right_keys(info)
    if info.kind in ("COMMA", "CROSS") or not keys:
        return True
    return not is_unique_join(ctx, info) and all(k.casefold() != CONVENTIONAL_KEY for k in keys)


def _cartesian(ctx: Ctx, select: exp.Select, infos: list[JoinInfo]) -> list[Finding]:
    out = []
    where = conjuncts(select.args.get("where"))
    for info in infos:
        if info.kind not in ("COMMA", "CROSS") or not isinstance(info.node.this, (exp.Table, exp.Subquery)):
            continue
        alias = info.alias.casefold()
        linked = any(isinstance(c, exp.EQ) and isinstance(c.this, exp.Column) and isinstance(c.expression, exp.Column)
                     and alias in (qualifier(c.this), qualifier(c.expression))
                     and qualifier(c.this) != qualifier(c.expression) for c in where)
        if not linked:
            out.append(smell("S03", "high",
                             f"`{info.table}` is joined with no condition linking it to the other tables, so every "
                             "row pairs with every row (a cartesian product).",
                             render(info.node, ctx.dialect),
                             "Add the join condition (JOIN ... ON a.key = b.key), or confirm the cross join is "
                             "intended."))
    return out


def _non_equi(ctx: Ctx, infos: list[JoinInfo]) -> list[Finding]:
    return [smell("S04", "medium",
                  f"The join to `{i.table}` has no equality between columns of the two sides, so it matches by "
                  "range or expression; this may match many rows per row and cannot use a simple key lookup.",
                  render(i.node, ctx.dialect), "Check the join condition; add the key equality if one exists.")
            for i in infos if i.on is not None and not equalities(i.on) and not i.using]


def _rejects_null(part: exp.Expression) -> bool:
    if isinstance(part, exp.Is) and isinstance(part.expression, exp.Null):
        return False
    return not part.find(exp.Coalesce) and not any(
        isinstance(n, exp.Is) and isinstance(n.expression, exp.Null) for n in part.find_all(exp.Is))


def _left_defeated(ctx: Ctx, select: exp.Select, infos: list[JoinInfo]) -> list[Finding]:
    out = []
    where = conjuncts(select.args.get("where"))
    for info in infos:
        if info.kind != "LEFT":
            continue
        alias = info.alias.casefold()
        for part in where:
            if any(qualifier(c) == alias for c in columns_of(part)) and _rejects_null(part):
                out.append(smell("S07", "high",
                                 f"`WHERE {render(part, ctx.dialect)}` filters on `{info.table}`, the right side of a "
                                 "LEFT JOIN, so rows without a match (NULLs there) are removed: the LEFT JOIN behaves "
                                 "like an inner join.",
                                 render(part, ctx.dialect),
                                 "Move the condition into the join's ON clause to keep unmatched rows, or write an "
                                 "inner join if that is what you mean."))
    return out


def _fanout(ctx: Ctx, select: exp.Select, infos: list[JoinInfo]) -> list[Finding]:
    risky = [i for i in infos if may_fan_out(ctx, i)]
    if not risky:
        return []
    out = []
    names = ", ".join(f"`{i.table}`" for i in risky)
    if select.args.get("distinct") is not None:
        out.append(smell("S09", "medium",
                         f"DISTINCT removes duplicate rows that the join to {names} may create; it hides the fan-out "
                         "instead of preventing it and costs a sort or hash over the result.",
                         "SELECT DISTINCT", "Join to a pre-aggregated or de-duplicated subquery instead."))
    for agg in aggregates(select):
        if not isinstance(agg, FANOUT_AGGS) or agg.find(exp.Distinct) or agg.find(exp.Star):
            continue
        cols = columns_of(agg)
        sides = {qualifier(c) for c in cols}
        culprits = [i for i in risky if cols and "" not in sides and i.alias.casefold() not in sides]
        if culprits:
            out.append(smell("S10", "high",
                             f"`{render(agg, ctx.dialect)}` is computed after the join to "
                             f"{', '.join(f'`{i.table}`' for i in culprits)}; if a row matches several rows there, "
                             "its value is counted once per match and the result overcounts.",
                             render(agg, ctx.dialect),
                             "Aggregate the joined table in a subquery first (one row per key), then join."))
    return out


def _tie_breaker(ctx: Ctx, select: exp.Select) -> list[Finding]:
    order = select.args.get("order")
    if order is None or (select.args.get("limit") is None and select.args.get("offset") is None):
        return []
    for ordered in order.expressions:
        col = ordered.this
        if isinstance(col, exp.Column) and (col.name.casefold() == CONVENTIONAL_KEY
                                            or ctx.schema.is_unique(table_for(col, select), col.name)):
            return []
    return [smell("S11", "medium",
                  "ORDER BY with a row limit, but the ordering columns are not a known unique key: rows that tie may "
                  "come back in any order, so pages can differ between runs.",
                  render(order, ctx.dialect), "Add a unique column (such as the primary key) as the last ORDER BY "
                                              "column.")]


def _correlated(ctx: Ctx) -> list[Finding]:
    try:
        root = build_scope(ctx.statement)
    except (SqlglotError, ValueError, KeyError, AttributeError, TypeError):  # best effort: DML, dialect nodes
        return []
    out = []
    for scope in root.traverse() if root is not None else []:
        parent = scope.expression.parent
        if scope.is_correlated_subquery and isinstance(parent, exp.Subquery) and \
                not isinstance(parent.parent, (exp.Exists, exp.In)):
            out.append(smell("S13", "medium",
                             "A subquery refers to a column of the outer query, so it may be evaluated once per outer "
                             "row.", render(parent, ctx.dialect),
                             "Consider a join to a pre-aggregated subquery, if the database does not rewrite it."))
    return out
