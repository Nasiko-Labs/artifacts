"""Pure: schema facts from pasted DDL (CREATE TABLE / CREATE INDEX / ALTER TABLE ... ADD CONSTRAINT) and from
hint lines such as `orders.id is the primary key`, `index on orders(customer_id)` or
`assume orders.customer_id is NOT NULL`. Pasted DDL is the only schema source: no database is contacted."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlglot import exp

_COLUMN_FACT = re.compile(r"^\s*(?:assume\s+)?(?:that\s+)?[`\"]?(\w+)[`\"]?\.[`\"]?(\w+)[`\"]?\s+is\s+(?:the\s+|a\s+)?"
                          r"(primary key|unique|not null)\b", re.IGNORECASE)
_INDEX_FACT = re.compile(r"^\s*(?:there is an?\s+)?(unique\s+)?index\s+on\s+[`\"]?([\w.]+?)[`\"]?\s*\(([^)]*)\)",
                         re.IGNORECASE)


@dataclass(frozen=True)
class Schema:
    columns: dict[str, dict[str, str]] = field(default_factory=dict)   # table -> column -> type
    not_null: frozenset[tuple[str, str]] = frozenset()
    unique: frozenset[tuple[str, str]] = frozenset()
    indexed: frozenset[tuple[str, str]] = frozenset()                 # leading columns of an index or key
    tables: frozenset[str] = frozenset()

    @property
    def known(self) -> bool:
        return bool(self.tables)

    def is_unique(self, table: str | None, column: str) -> bool:
        return (_n(table), _n(column)) in self.unique

    def is_not_null(self, table: str | None, column: str) -> bool:
        return (_n(table), _n(column)) in self.not_null or self.is_unique_pk(table, column)

    def is_unique_pk(self, table: str | None, column: str) -> bool:
        return (_n(table), _n(column)) in self.unique and (_n(table), _n(column)) in self.not_null

    def has_table(self, table: str | None) -> bool:
        return _n(table) in self.tables

    def has_index(self, table: str | None, column: str) -> bool:
        return (_n(table), _n(column)) in self.indexed

    def type_of(self, table: str | None, column: str) -> str:
        return self.columns.get(_n(table), {}).get(_n(column), "")

    def names(self) -> set[str]:
        return set(self.tables) | {c for cols in self.columns.values() for c in cols}


def _n(name: str | None) -> str:
    return (name or "").strip("`\"[]").casefold()


def is_hint_line(line: str) -> bool:
    return bool(_COLUMN_FACT.match(line or "") or _INDEX_FACT.match(line or ""))


class _Builder:
    def __init__(self):
        self.columns: dict[str, dict[str, str]] = {}
        self.not_null: set[tuple[str, str]] = set()
        self.unique: set[tuple[str, str]] = set()
        self.indexed: set[tuple[str, str]] = set()
        self.tables: set[str] = set()

    def key(self, table: str, cols: list[str], primary: bool, unique: bool = True) -> None:
        table, cols = _n(table), [_n(c) for c in cols if c]
        if not cols:
            return
        self.tables.add(table)
        self.indexed.add((table, cols[0]))
        if unique and len(cols) == 1:
            self.unique.add((table, cols[0]))
        if primary:
            self.not_null.update((table, c) for c in cols)

    def build(self) -> Schema:
        return Schema({t: dict(c) for t, c in self.columns.items()}, frozenset(self.not_null),
                      frozenset(self.unique), frozenset(self.indexed), frozenset(self.tables))


def build(statements: Iterable[exp.Expression], hint_lines: Iterable[str] = ()) -> Schema:
    builder = _Builder()
    for statement in statements:
        if isinstance(statement, exp.Create):
            _from_create(builder, statement)
        elif isinstance(statement, exp.Alter):
            _from_alter(builder, statement)
    for line in hint_lines:
        _from_hint(builder, line)
    return builder.build()


def is_ddl(statement: exp.Expression) -> bool:
    return isinstance(statement, exp.Alter) or (
        isinstance(statement, exp.Create) and str(statement.args.get("kind") or "").upper() in ("TABLE", "INDEX"))


def _from_create(builder: _Builder, create: exp.Create) -> None:
    kind = str(create.args.get("kind") or "").upper()
    if kind == "INDEX" and isinstance(create.this, exp.Index):
        index = create.this
        table = index.args.get("table")
        params = index.args.get("params")
        cols = [c.this.name if isinstance(c, exp.Ordered) else c.name for c in (params.args.get("columns") or [])] \
            if params else []
        if table is not None:
            builder.key(table.name, cols, primary=False, unique=bool(create.args.get("unique")))
        return
    if kind != "TABLE" or not isinstance(create.this, exp.Schema):
        return
    table = create.this.this.name if isinstance(create.this.this, exp.Table) else ""
    builder.tables.add(_n(table))
    columns = builder.columns.setdefault(_n(table), {})
    for item in create.this.expressions:
        if isinstance(item, exp.ColumnDef):
            _column_def(builder, table, item, columns)
        else:
            _table_constraint(builder, table, item)


def _column_def(builder: _Builder, table: str, item: exp.ColumnDef, columns: dict[str, str]) -> None:
    name = item.name
    kind = item.args.get("kind")
    columns[_n(name)] = kind.sql().upper() if kind is not None else ""
    for constraint in item.args.get("constraints") or []:
        ckind = constraint.args.get("kind")
        if isinstance(ckind, exp.NotNullColumnConstraint) and not ckind.args.get("allow_null"):
            builder.not_null.add((_n(table), _n(name)))
        elif isinstance(ckind, exp.PrimaryKeyColumnConstraint):
            builder.key(table, [name], primary=True)
        elif isinstance(ckind, exp.UniqueColumnConstraint):
            builder.key(table, [name], primary=False)


def _table_constraint(builder: _Builder, table: str, item: exp.Expression) -> None:
    for node in item.find_all(exp.PrimaryKey, exp.UniqueColumnConstraint):
        primary = isinstance(node, exp.PrimaryKey)
        target = node if primary else node.this
        cols = [e.name for e in (target.expressions if target is not None else [])]
        builder.key(table, cols, primary=primary)


def _from_alter(builder: _Builder, alter: exp.Alter) -> None:
    table = alter.this.name if isinstance(alter.this, exp.Table) else ""
    for action in alter.args.get("actions") or []:
        _table_constraint(builder, table, action)


def _from_hint(builder: _Builder, line: str) -> None:
    fact = _COLUMN_FACT.match(line or "")
    if fact:
        table, column, what = fact.group(1), fact.group(2), fact.group(3).casefold()
        builder.tables.add(_n(table))
        if what == "not null":
            builder.not_null.add((_n(table), _n(column)))
        else:
            builder.key(table, [column], primary=what == "primary key")
        return
    index = _INDEX_FACT.match(line or "")
    if index:
        cols = [c.strip(" `\"") for c in index.group(3).split(",")]
        builder.key(index.group(2).split(".")[-1], cols, primary=False, unique=bool(index.group(1)))
