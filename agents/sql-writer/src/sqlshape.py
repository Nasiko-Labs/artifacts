"""Pure: small facts about a parsed query that validation, lint, equivalence and index advice share."""

from __future__ import annotations

from sqlglot import exp

from schema import Schema, Table


def cte_names(expr: exp.Expression) -> set[str]:
    return {c.alias_or_name.casefold() for c in expr.find_all(exp.CTE)}


def base_tables(expr: exp.Expression) -> set[str]:
    ctes = cte_names(expr)
    return {t.name.casefold() for t in expr.find_all(exp.Table) if t.name and t.name.casefold() not in ctes}


def order_and_limit(expr: exp.Expression) -> bool:
    root = expr.this if isinstance(expr, exp.Subquery) else expr
    return bool(root.args.get("order")) and bool(root.args.get("limit") or root.args.get("fetch"))


def aliases(select: exp.Expression, schema: Schema) -> dict[str, Table]:
    """alias-or-name -> schema table, for the tables directly in this SELECT's FROM / JOIN."""
    out: dict[str, Table] = {}
    sources = [select.args.get("from_") or select.args.get("from"), *(select.args.get("joins") or [])]
    for source in sources:
        if source is None:
            continue
        table = source.this if isinstance(source, (exp.From, exp.Join)) else source
        if isinstance(table, exp.Table) and table.name:
            found = schema.table(table.name)
            if found is not None:
                out[table.alias_or_name.casefold()] = found
    return out


def column_table(col: exp.Column, scope: dict[str, Table]) -> Table | None:
    """The schema table a column belongs to: by qualifier, else the only table in scope having it."""
    if col.table:
        return scope.get(col.table.casefold())
    owners = [t for t in scope.values() if t.column(col.name) is not None]
    return owners[0] if len(owners) == 1 else None


def selects(expr: exp.Expression) -> list[exp.Select]:
    return list(expr.find_all(exp.Select))


def own_nodes(select: exp.Select, kind) -> list[exp.Expression]:
    """Nodes of `kind` inside this SELECT, not inside a nested SELECT."""
    return [n for n in select.find_all(kind) if n.find_ancestor(exp.Select) is select]


def where_conditions(select: exp.Select) -> list[exp.Expression]:
    """WHERE and JOIN ON predicates of this SELECT (not nested ones)."""
    out = []
    where = select.args.get("where")
    if where is not None:
        out.append(where.this)
    for join in select.args.get("joins") or []:
        if join.args.get("on") is not None:
            out.append(join.args["on"])
    return out


def comparisons(select: exp.Select) -> list[exp.Expression]:
    """Comparison predicates in this SELECT's WHERE / JOIN ON, not those of nested SELECTs."""
    kinds = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.In, exp.Between, exp.Like, exp.ILike)
    return [n for cond in where_conditions(select) for n in cond.find_all(*kinds)
            if n.find_ancestor(exp.Select) is select]


def snippet(node: exp.Expression, dialect: str, limit: int = 120) -> str:
    text = " ".join(node.sql(dialect=dialect).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"
