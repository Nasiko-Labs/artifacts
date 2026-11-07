"""Pure: the parse summary of one statement, which the model must narrate and may not contradict.

Per statement: kind; CTEs in WITH order; sources; joins (type, condition, equality or not); WHERE and
HAVING conjuncts; grouping and aggregates; windows; projection; ordering, limit and offset; set operations;
and for writes the target and whether a WHERE exists."""

from __future__ import annotations

from sqlglot import exp

from ast_util import (
    aggregates,
    alias_map,
    conjuncts,
    equalities,
    from_source,
    joins,
    render,
)

KINDS = ((exp.Select, "select"), (exp.SetOperation, "select"), (exp.Insert, "insert"), (exp.Update, "update"),
         (exp.Delete, "delete"), (exp.Merge, "merge"))


def kind_of(statement: exp.Expression) -> str:
    if isinstance(statement, exp.Create):
        return "view" if str(statement.args.get("kind") or "").upper() == "VIEW" else "ddl"
    return next((name for cls, name in KINDS if isinstance(statement, cls)), "other")


def main_query(statement: exp.Expression) -> exp.Expression | None:
    """The query a statement reads: itself for a SELECT, the source of INSERT ... SELECT or a view."""
    if isinstance(statement, (exp.Select, exp.SetOperation)):
        return statement
    if isinstance(statement, (exp.Insert, exp.Create)):
        inner = statement.expression
        return inner if isinstance(inner, (exp.Select, exp.SetOperation)) else None
    return None


def branches(query: exp.Expression | None) -> list[exp.Select]:
    if isinstance(query, exp.SetOperation):
        return [*branches(query.left), *branches(query.right)]
    return [query] if isinstance(query, exp.Select) else []


def set_operations(query: exp.Expression | None) -> list[str]:
    ops = []
    for node in (query.find_all(exp.SetOperation) if query is not None else []):
        name = type(node).__name__.upper()
        ops.append(f"{name}{'' if node.args.get('distinct') else ' ALL'}")
    return ops


def summarize(statement: exp.Expression, dialect: str = "") -> dict:
    kind = kind_of(statement)
    query = main_query(statement)
    out: dict = {"kind": kind}
    ctes = [{"name": cte.alias, "reads": sorted({t.name for t in cte.this.find_all(exp.Table)})}
            for cte in statement.find_all(exp.CTE)]
    if ctes:
        out["ctes"] = ctes
    if kind in ("insert", "update", "delete", "merge"):
        out["write"] = _write(statement, dialect)
    if kind in ("update", "delete") and isinstance(statement, (exp.Update, exp.Delete)):
        out["filters"] = [render(c, dialect) for c in conjuncts(statement.args.get("where"))]
    blocks = [_select(s, dialect) for s in branches(query)]
    if blocks:
        out["query"] = blocks[0] if len(blocks) == 1 else {"branches": blocks}
    ops = set_operations(query)
    if ops:
        out["set_operations"] = ops
    subqueries = sum(1 for _ in query.find_all(exp.Subquery)) if query is not None else 0
    if subqueries:
        out["subqueries"] = subqueries
    return out


def _write(statement: exp.Expression, dialect: str) -> dict:
    target = statement.this
    table = target.this if isinstance(target, exp.Schema) else target
    columns = [c.name for c in target.expressions] if isinstance(target, exp.Schema) else []
    has_where = statement.args.get("where") is not None
    write = {"target": render(table, dialect), "where": has_where}
    if columns:
        write["columns"] = columns
    if isinstance(statement, exp.Update):
        write["sets"] = [render(e, dialect) for e in statement.expressions]
    if isinstance(statement, (exp.Update, exp.Delete)) and not has_where:
        write["affects"] = "all rows"
    return write


def _select(select: exp.Select, dialect: str) -> dict:
    block: dict = {}
    first = from_source(select)
    if first:
        block["from"] = first[0] if first[0] == first[1] else f"{first[0]} AS {first[1]}"
    block["joins"] = [{"type": j.kind, "source": j.table if j.table == j.alias else f"{j.table} AS {j.alias}",
                       "condition": render(j.on, dialect) or (f"USING ({', '.join(j.using)})" if j.using else ""),
                       "equality_on_columns": bool(equalities(j.on)) or bool(j.using)} for j in joins(select)]
    block["where"] = [render(c, dialect) for c in conjuncts(select.args.get("where"))]
    group = select.args.get("group")
    block["group_by"] = [render(g, dialect) for g in (group.expressions if group else [])]
    block["aggregates"] = sorted({render(a, dialect) for a in aggregates(select)})
    block["having"] = [render(c, dialect) for c in conjuncts(select.args.get("having"))]
    block["windows"] = [_window(w, dialect) for w in select.find_all(exp.Window) if w.find_ancestor(exp.Select) is select]
    block["distinct"] = select.args.get("distinct") is not None
    block["columns"] = [{"output": e.alias_or_name or render(e, dialect), "expression": render(e.unalias(), dialect)}
                        for e in select.expressions]
    order = select.args.get("order")
    block["order_by"] = [render(o, dialect) for o in (order.expressions if order else [])]
    limit, offset = select.args.get("limit"), select.args.get("offset")
    if limit is not None:
        block["limit"] = render(limit.args.get("expression") or limit.args.get("count") or limit, dialect)
    if offset is not None:
        block["offset"] = render(offset.args.get("expression") or offset, dialect)
    block["aliases"] = alias_map(select)
    return {k: v for k, v in block.items() if v not in ([], {}, False, "")}


def _window(window: exp.Window, dialect: str) -> dict:
    spec = window.args.get("spec")
    order = window.args.get("order")
    return {"function": render(window.this, dialect),
            "partition_by": [render(p, dialect) for p in window.args.get("partition_by") or []],
            "order_by": [render(o, dialect) for o in (order.expressions if order else [])],
            "frame": render(spec, dialect) if spec is not None else "default frame"}


def names_to_mention(statement: exp.Expression) -> list[str]:
    """Every CTE and every joined/read table the narrative must name (the set check)."""
    names = [cte.alias for cte in statement.find_all(exp.CTE)]
    names += [t.name for t in statement.find_all(exp.Table) if t.name]
    return list(dict.fromkeys(n for n in names if n))
