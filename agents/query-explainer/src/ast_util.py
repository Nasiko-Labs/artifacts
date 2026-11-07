"""Pure helpers over sqlglot trees, shared by structure, smells and assumptions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlglot import exp

MAX_EVIDENCE_CHARS = 240
TIMESTAMP_NAME = re.compile(r"_at$|_ts$|time|date", re.IGNORECASE)
CONVENTIONAL_KEY = "id"
WRITE_TYPES = (exp.Insert, exp.Update, exp.Delete, exp.Merge)


@dataclass(frozen=True)
class JoinInfo:
    node: exp.Join
    table: str          # the joined table's name (or CTE / subquery alias)
    alias: str          # alias_or_name
    kind: str           # INNER, LEFT, RIGHT, FULL, CROSS, COMMA
    on: exp.Expression | None
    using: tuple[str, ...]


def render(node: exp.Expression | None, dialect: str = "") -> str:
    if node is None:
        return ""
    text = " ".join(node.sql(dialect=dialect or None).split())
    return text if len(text) <= MAX_EVIDENCE_CHARS else text[:MAX_EVIDENCE_CHARS - 1] + "…"


def selects(statement: exp.Expression) -> list[exp.Select]:
    return list(statement.find_all(exp.Select))


def conjuncts(condition: exp.Expression | None) -> list[exp.Expression]:
    if condition is None:
        return []
    node = condition.this if isinstance(condition, (exp.Where, exp.Having)) else condition
    node = node.unnest() if isinstance(node, exp.Paren) else node
    if isinstance(node, exp.And):
        return [*conjuncts(node.left), *conjuncts(node.right)]
    return [node]


def source_name(node: exp.Expression) -> tuple[str, str]:
    """(name, alias_or_name) of a FROM/JOIN source."""
    if isinstance(node, exp.Table):
        return node.name, node.alias_or_name
    alias = node.alias_or_name if hasattr(node, "alias_or_name") else ""
    return alias or "subquery", alias or "subquery"


def from_source(select: exp.Select) -> tuple[str, str] | None:
    source = select.args.get("from_")
    return source_name(source.this) if source is not None else None


def joins(select: exp.Select) -> list[JoinInfo]:
    out = []
    for join in select.args.get("joins") or []:
        name, alias = source_name(join.this)
        side, kind = str(join.args.get("side") or "").upper(), str(join.args.get("kind") or "").upper()
        on, using = join.args.get("on"), tuple(i.name for i in join.args.get("using") or [])
        if side:
            label = side
        elif kind == "CROSS":
            label = "CROSS"
        elif on is None and not using:
            label = "COMMA" if kind == "" else kind
        else:
            label = "INNER"
        out.append(JoinInfo(join, name, alias, label, on, using))
    return out


def alias_map(select: exp.Select) -> dict[str, str]:
    """alias (casefolded) -> table name, for the select's own FROM and JOIN sources."""
    mapping = {}
    first = from_source(select)
    if first:
        mapping[first[1].casefold()] = first[0]
    for info in joins(select):
        mapping[info.alias.casefold()] = info.table
    return mapping


def qualifier(column: exp.Column) -> str:
    return (column.table or "").casefold()


def columns_of(node: exp.Expression) -> list[exp.Column]:
    return [c for c in node.find_all(exp.Column)
            if not isinstance(c.this, exp.Star) and not c.name.startswith("__param_")]


def equalities(on: exp.Expression | None) -> list[tuple[exp.Column, exp.Column]]:
    """Column = column pairs in a join condition."""
    pairs = []
    for part in conjuncts(on):
        if isinstance(part, exp.EQ) and isinstance(part.this, exp.Column) and isinstance(part.expression, exp.Column):
            pairs.append((part.this, part.expression))
    return pairs


def right_keys(info: JoinInfo) -> list[str]:
    """The joined side's key columns: from `USING`, or the equality side qualified with the joined alias."""
    if info.using:
        return list(info.using)
    keys = []
    for left, right in equalities(info.on):
        for col in (left, right):
            if qualifier(col) == info.alias.casefold():
                keys.append(col.name)
    return keys


def aggregates(select: exp.Select) -> list[exp.AggFunc]:
    """Aggregates in this select's projection and HAVING (not inside subqueries or windows)."""
    found = []
    for part in [*select.expressions, select.args.get("having")]:
        if part is None:
            continue
        for agg in part.find_all(exp.AggFunc):
            if agg.find_ancestor(exp.Select) is select and not isinstance(agg.parent, exp.Window):
                found.append(agg)
    return found


def in_this_select(node: exp.Expression, select: exp.Select) -> bool:
    return node.find_ancestor(exp.Select) is select


def is_literalish(node: exp.Expression) -> bool:
    """A literal, NULL, a placeholder, or an expression with no column in it."""
    if isinstance(node, (exp.Literal, exp.Null, exp.Placeholder, exp.Boolean)):
        return True
    if isinstance(node, exp.Column) and node.name.startswith("__param_"):
        return True
    return not columns_of(node) and not node.find(exp.Subquery, exp.Select)


def is_param(node: exp.Expression | None) -> bool:
    return isinstance(node, exp.Placeholder) or (isinstance(node, exp.Column) and node.name.startswith("__param_"))
