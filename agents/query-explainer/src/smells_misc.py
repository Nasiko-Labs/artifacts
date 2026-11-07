"""Pure: statement-level smells (S01 write without WHERE, S02 SELECT *, S12 deep OFFSET, S17 UNION where ALL
fits, S19 positional ORDER/GROUP BY, S20 integer division, S21 non-aggregated column, S22 window default frame,
S24 several writes without a transaction)."""

from __future__ import annotations

import re

from sqlglot import exp

from ast_util import CONVENTIONAL_KEY, WRITE_TYPES, is_param, render, selects
from findings import Ctx, Finding, smell, table_for
from structure import branches, main_query

DEEP_OFFSET = 10_000
TRUNCATING_DIALECTS = frozenset({"postgres", "tsql", "sqlite", "redshift"})
LOOSE_GROUP_DIALECTS = frozenset({"mysql", "sqlite"})
INT_TYPES = ("INT", "BIGINT", "SMALLINT", "INTEGER", "TINYINT")
_INT_NAME = re.compile(r"(?:^|_)(?:count|cnt|qty|quantity|num)(?:_|$)|_count$", re.IGNORECASE)
_RATIO_NAME = re.compile(r"ratio|rate|pct|percent|share", re.IGNORECASE)
WINDOW_AGGS = (exp.Sum, exp.Avg, exp.Count, exp.Min, exp.Max)


def detect(ctx: Ctx) -> list[Finding]:
    return (_unfiltered_write(ctx) + _star(ctx) + _offset(ctx) + _union(ctx) + _positional(ctx)
            + _int_division(ctx) + _loose_group(ctx) + _window_frame(ctx) + _no_transaction(ctx))


def _unfiltered_write(ctx: Ctx) -> list[Finding]:
    s = ctx.statement
    if not isinstance(s, (exp.Update, exp.Delete)) or s.args.get("where") is not None:
        return []
    verb = "UPDATE" if isinstance(s, exp.Update) else "DELETE"
    return [smell("S01", "critical", f"{verb} with no WHERE clause: it changes every row in "
                                     f"`{render(s.this, ctx.dialect)}`.", render(s, ctx.dialect),
                  "Add a WHERE clause; run the matching SELECT COUNT(*) first to see how many rows it touches.")]


def _star(ctx: Ctx) -> list[Finding]:
    query = main_query(ctx.statement)
    feeds = isinstance(ctx.statement, (exp.Insert, exp.Create))
    out = []
    for select in branches(query):
        for item in select.expressions:
            if isinstance(item, exp.Star) or (isinstance(item, exp.Column) and isinstance(item.this, exp.Star)):
                out.append(smell("S02", "medium" if feeds else "low",
                                 "SELECT * returns every column: it reads more than needed and its output changes "
                                 "when the table gains or loses columns" + (", which can break the INSERT or view "
                                                                           "it feeds." if feeds else "."),
                                 render(item, ctx.dialect), "List the columns you need."))
    return out


def _offset(ctx: Ctx) -> list[Finding]:
    out = []
    for offset in ctx.statement.find_all(exp.Offset):
        value = offset.args.get("expression")
        deep = is_param(value) or (isinstance(value, exp.Literal) and value.this.isdigit()
                                   and int(value.this) >= DEEP_OFFSET)
        if deep:
            out.append(smell("S12", "low", "A large or variable OFFSET still reads and discards every skipped row.",
                             render(offset, ctx.dialect), "Page by key instead (WHERE key > last seen key)."))
    return out


def _union(ctx: Ctx) -> list[Finding]:
    out = []
    for node in ctx.statement.find_all(exp.Union):
        if not node.args.get("distinct"):
            continue
        keyed = [c for s in branches(node) for c in s.expressions if isinstance(c, exp.Column)
                 and (c.name.casefold() == CONVENTIONAL_KEY or c.name.casefold().endswith("_id"))]
        if keyed:
            out.append(smell("S17", "low", "UNION removes duplicates, but the branches select a key column, so there "
                                           "may be none to remove; the de-duplication costs a sort or hash.",
                             "UNION", "Use UNION ALL if the branches cannot overlap."))
    return out


def _positional(ctx: Ctx) -> list[Finding]:
    out = []
    for clause in ctx.statement.find_all(exp.Order, exp.Group):
        for item in clause.expressions:
            node = item.this if isinstance(item, exp.Ordered) else item
            if isinstance(node, exp.Literal) and not node.is_string:
                word = "ORDER BY" if isinstance(clause, exp.Order) else "GROUP BY"
                out.append(smell("S19", "low", f"{word} {node.this} refers to a column by position; it silently "
                                               "changes meaning when the select list changes.",
                                 f"{word} {render(clause, ctx.dialect).split(' BY ', 1)[-1]}", "Name the column."))
                break
    return out


def _int_like(ctx: Ctx, node: exp.Expression) -> bool:
    if isinstance(node, exp.Literal):
        return not node.is_string and node.this.isdigit()
    if isinstance(node, exp.Count):
        return True
    if isinstance(node, exp.Column):
        declared = ctx.schema.type_of(table_for(node, node.find_ancestor(exp.Select)), node.name)
        return declared.startswith(INT_TYPES) if declared else bool(_INT_NAME.search(node.name))
    return False


def _int_division(ctx: Ctx) -> list[Finding]:
    out = []
    for div in ctx.statement.find_all(exp.Div, exp.IntDiv):
        is_int_div = isinstance(div, exp.IntDiv)
        if not is_int_div and (ctx.dialect not in TRUNCATING_DIALECTS
                               or not (_int_like(ctx, div.this) and _int_like(ctx, div.expression))):
            continue
        alias = div.find_ancestor(exp.Alias)
        feeds = div.find_ancestor(exp.AggFunc) or div.find(exp.AggFunc) or \
            (alias is not None and _RATIO_NAME.search(alias.alias or ""))
        if feeds or is_int_div:
            out.append(smell("S20", "medium", f"`{render(div, ctx.dialect)}` may divide two integers, which truncates "
                                              "the result (3 / 4 gives 0).", render(div, ctx.dialect),
                             "Cast one side to a decimal first, e.g. CAST(x AS DECIMAL) / y."))
    return out


def _loose_group(ctx: Ctx) -> list[Finding]:
    if ctx.dialect not in LOOSE_GROUP_DIALECTS:
        return []
    out = []
    for select in selects(ctx.statement):
        group = select.args.get("group")
        if group is None:
            continue
        keys = {render(g, ctx.dialect).casefold() for g in group.expressions}
        for item in select.expressions:
            bare = item.unalias()
            if bare.find(exp.AggFunc) or isinstance(bare, (exp.Literal, exp.Star)):
                continue
            names = {render(bare, ctx.dialect).casefold(), (item.alias_or_name or "").casefold()}
            if not names & keys:
                out.append(smell("S21", "medium", f"`{render(bare, ctx.dialect)}` is selected but neither grouped nor "
                                                  "aggregated; this database allows it but returns an arbitrary value "
                                                  "from each group.", render(item, ctx.dialect),
                                 "Add it to GROUP BY or wrap it in an aggregate such as MAX()."))
    return out


def _window_frame(ctx: Ctx) -> list[Finding]:
    out = []
    for window in ctx.statement.find_all(exp.Window):
        order = window.args.get("order")
        if order is None or window.args.get("spec") is not None or not isinstance(window.this, WINDOW_AGGS):
            continue
        cols = [o.this for o in order.expressions if isinstance(o.this, exp.Column)]
        unique = any(c.name.casefold() == CONVENTIONAL_KEY or
                     ctx.schema.is_unique(table_for(c, c.find_ancestor(exp.Select)), c.name) for c in cols)
        if not unique:
            out.append(smell("S22", "low", "A running aggregate with ORDER BY and no frame uses the default RANGE "
                                           "frame: rows that tie on the ordering share the same running value.",
                             render(window, ctx.dialect), "Add ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW, or a "
                                                          "unique tie-breaker."))
    return out


def _no_transaction(ctx: Ctx) -> list[Finding]:
    writes = [s for s in ctx.all_statements if isinstance(s, WRITE_TYPES)]
    has_tx = any(isinstance(s, (exp.Transaction, exp.Commit)) for s in ctx.all_statements)
    if len(writes) < 2 or has_tx or writes[0] is not ctx.statement:
        return []
    return [smell("S24", "low", f"{len(writes)} write statements run without BEGIN/COMMIT: if one fails, the earlier "
                                "ones stay applied.", render(ctx.statement, ctx.dialect),
                  "Wrap them in one transaction.")]
