"""Pure: assumption candidates (A01-A10), each tied to a SQL fragment. A02 (key uniqueness) and A03 (NULL
handling) are resolved by pasted DDL: a confirmed one is `checked` and reported under "Confirmed by your
schema", never as an open assumption."""

from __future__ import annotations

from sqlglot import exp

from ast_util import (
    TIMESTAMP_NAME,
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
from findings import Ctx, Finding, assumption, table_for

TZ_FUNCS = tuple(getattr(exp, n) for n in ("CurrentDate", "CurrentTimestamp", "CurrentDatetime", "DateTrunc",
                                           "TimestampTrunc") if hasattr(exp, n))
TZ_NAMES = frozenset({"NOW", "GETDATE", "SYSDATE", "SYSDATETIME", "GETUTCDATE", "LOCALTIMESTAMP"})
SOFT_DELETE = frozenset({"deleted_at", "is_deleted", "deleted", "status", "active", "is_active"})
BOUNDARY = {exp.GTE: "includes", exp.GT: "excludes", exp.LTE: "includes only midnight of", exp.LT: "excludes"}
COLLATION = {"mysql": "MySQL", "tsql": "SQL Server"}


def detect(ctx: Ctx) -> list[Finding]:
    found: list[Finding] = []
    for select in selects(ctx.statement):
        found += _joins(ctx, select) + _dedupe(ctx, select) + _soft_delete(ctx, select) + _arbitrary(ctx, select)
    return found + _nulls(ctx) + _time(ctx) + _collation(ctx) + _writes(ctx)


def _joins(ctx: Ctx, select: exp.Select) -> list[Finding]:
    out = []
    aggs = aggregates(select)
    downstream = render(aggs[0], ctx.dialect) if aggs else ("DISTINCT" if select.args.get("distinct") else "")
    for info in joins(select):
        evidence = render(info.node, ctx.dialect)
        if info.kind == "INNER":
            out.append(assumption("A01", f"Assumes every row has a match in `{info.table}`; rows without one are "
                                         "silently dropped by the inner join.", evidence))
        if info.kind == "LEFT":
            for part in conjuncts(info.on):
                if not equalities(part):
                    out.append(assumption("A07", f"Rows from the left side are kept even when `{render(part, ctx.dialect)}`"
                                                 f" fails; only the `{info.table}` columns become NULL.", evidence))
        if not downstream:
            continue
        for key in right_keys(info)[:1]:
            unique = ctx.schema.is_unique(info.table, key)
            out.append(assumption("A02", f"Assumes `{info.table}.{key}` is unique; otherwise rows multiply and "
                                         f"`{downstream}` overcounts.", evidence, checked=unique,
                                  note=f"`{info.table}.{key}` is unique in your schema, so the join cannot multiply "
                                       "rows." if unique else ""))
    return out


def _dedupe(ctx: Ctx, select: exp.Select) -> list[Finding]:
    group = select.args.get("group")
    if select.args.get("distinct") is not None:
        return [assumption("A05", "Assumes duplicate rows are meaningless and can be merged (DISTINCT).",
                           "SELECT DISTINCT")]
    if group is not None and not aggregates(select):
        return [assumption("A05", "Assumes duplicate rows are meaningless: GROUP BY without an aggregate only "
                                  "removes duplicates.", render(group, ctx.dialect))]
    return []


def _soft_delete(ctx: Ctx, select: exp.Select) -> list[Finding]:
    if not ctx.schema.known:
        return []
    filtered = {(qualifier(c), c.name.casefold()) for c in columns_of(select.args.get("where") or exp.Null())
                if c.name.casefold() in SOFT_DELETE}
    if not filtered:
        return []
    out = []
    for info in joins(select):
        columns = ctx.schema.columns.get(info.table.casefold(), {})
        flag = next((c for c in columns if c in SOFT_DELETE), "")
        if flag and (info.alias.casefold(), flag) not in filtered:
            out.append(assumption("A08", f"`{info.table}` has `{flag}` but is not filtered on it, while another table "
                                         "is: its deleted or inactive rows may appear.", render(info.node, ctx.dialect)))
    return out


def _arbitrary(ctx: Ctx, select: exp.Select) -> list[Finding]:
    if select.args.get("limit") is not None and select.args.get("order") is None:
        return [assumption("A09", "LIMIT without ORDER BY returns an arbitrary subset of rows, which may differ "
                                  "between runs.", render(select.args["limit"], ctx.dialect))]
    return []


def _nulls(ctx: Ctx) -> list[Finding]:
    out = []
    for node in ctx.statement.find_all(exp.NEQ, exp.Like, exp.In):
        negated = isinstance(node, exp.NEQ) or node.args.get("negate") or isinstance(node.parent, exp.Not)
        col = node.this
        if not negated or not isinstance(col, exp.Column) or isinstance(node.expression, exp.Null):
            continue
        shown = node.parent if isinstance(node.parent, exp.Not) else node
        table = table_for(col, col.find_ancestor(exp.Select))
        safe = ctx.schema.is_not_null(table, col.name)
        out.append(assumption("A03", f"Rows where `{col.name}` is NULL are excluded by `{render(shown, ctx.dialect)}`.",
                              render(shown, ctx.dialect), checked=safe,
                              note=f"`{col.name}` is NOT NULL in your schema." if safe else ""))
    return out


def _time(ctx: Ctx) -> list[Finding]:
    out = []
    for node in ctx.statement.find_all(exp.Func):
        is_tz = isinstance(node, TZ_FUNCS) or (isinstance(node, exp.Anonymous) and str(node.this).upper() in TZ_NAMES)
        is_cast = isinstance(node, exp.Cast) and node.to.is_type("date") and columns_of(node)
        if is_tz or is_cast:
            out.append(assumption("A04", f"`{render(node, ctx.dialect)}` uses the session or server time zone; the "
                                         "day boundary moves with it.", render(node, ctx.dialect)))
    for cmp in ctx.statement.find_all(*BOUNDARY):
        col, lit = cmp.this, cmp.expression
        if isinstance(col, exp.Column) and isinstance(lit, exp.Literal) and lit.is_string \
                and TIMESTAMP_NAME.search(col.name) and lit.this[:4].isdigit():
            out.append(assumption("A04", f"Date boundary: `{render(cmp, ctx.dialect)}` {BOUNDARY[type(cmp)]} "
                                         f"{lit.this}, in the session time zone.", render(cmp, ctx.dialect)))
    return out


def _collation(ctx: Ctx) -> list[Finding]:
    out = []
    for cmp in ctx.statement.find_all(exp.EQ):
        if isinstance(cmp.this, exp.Column) and isinstance(cmp.expression, exp.Literal) and cmp.expression.is_string:
            if ctx.dialect in COLLATION:
                text = (f"`{render(cmp, ctx.dialect)}` depends on the column collation: {COLLATION[ctx.dialect]}'s "
                        "default collations compare case-insensitively.")
            elif ctx.dialect == "postgres":
                text = f"`{render(cmp, ctx.dialect)}` is case-sensitive in PostgreSQL: other casings do not match."
            else:
                continue
            out.append(assumption("A06", text, render(cmp, ctx.dialect)))
    return out


def _writes(ctx: Ctx) -> list[Finding]:
    s = ctx.statement
    if not isinstance(s, (exp.Update, exp.Delete)):
        return []
    tail = "" if s.args.get("returning") is not None else " Without RETURNING, nothing shows what changed."
    return [assumption("A10", "Affects every row matching the predicate; the row count is unknown." + tail,
                       render(s, ctx.dialect))]
