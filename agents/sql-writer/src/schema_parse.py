"""Pure: pasted schema text -> Schema. CREATE TABLE / CREATE INDEX statements are parsed with sqlglot; the list
form (`orders(id int pk, customer_id int fk customers.id)`) and psql `\\d table` output are first normalised
to DDL by small parsers. A statement that does not parse is named in `skipped`, never guessed at."""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError

import schema_text
from schema import Column, ForeignKey, Index, Schema, Table


def parse(text: str, dialect: str) -> Schema:
    ddl = schema_text.to_ddl(text)
    if not ddl.strip():
        return Schema()
    statements, skipped = _statements(ddl, dialect)
    tables = tuple(t for t in (_table(s, dialect) for s in statements) if t is not None)
    indexes = tuple(i for i in (_index(s) for s in statements) if i is not None)
    return Schema(tables, indexes, skipped)


def _statements(ddl: str, dialect: str) -> tuple[list[exp.Expression], tuple[str, ...]]:
    try:
        whole = [s for s in sqlglot.parse(ddl, read=dialect, error_level=ErrorLevel.RAISE) if s is not None]
        return [s for s in whole if not isinstance(s, exp.Command)], ()
    except (SqlglotError, ValueError, RecursionError):
        pass
    good, bad = [], []
    for chunk in (c.strip() for c in ddl.split(";")):
        if not chunk:
            continue
        try:
            parsed = sqlglot.parse_one(chunk, read=dialect, error_level=ErrorLevel.RAISE)
        except (SqlglotError, ValueError, RecursionError):
            bad.append(" ".join(chunk.split())[:60])
            continue
        if isinstance(parsed, exp.Command):
            bad.append(" ".join(chunk.split())[:60])
        else:
            good.append(parsed)
    return good, tuple(bad)


def _ident(node: exp.Expression) -> str:
    if isinstance(node, exp.Ordered):
        node = node.this
    return node.name if hasattr(node, "name") else str(node)


def _table(stmt: exp.Expression, dialect: str) -> Table | None:
    if not isinstance(stmt, exp.Create) or str(stmt.args.get("kind") or "").upper() != "TABLE":
        return None
    body = stmt.this
    if not isinstance(body, exp.Schema) or not isinstance(body.this, exp.Table):
        return None
    columns, pk, fks = [], [], []
    for item in body.expressions:
        if isinstance(item, exp.ColumnDef):
            col, is_pk, fk = _column(item, dialect)
            columns.append(col)
            pk += [col.name] if is_pk else []
            fks += [fk] if fk else []
        elif isinstance(item, exp.PrimaryKey):
            pk += [_ident(e) for e in item.expressions]
        elif isinstance(item, exp.ForeignKey):
            fks += [_fk(tuple(_ident(e) for e in item.expressions), item.args.get("reference"))]
    pk_set = set(pk)
    columns = [Column(c.name, c.type, c.not_null or c.name in pk_set) for c in columns]
    partition, cluster = _layout(stmt)
    return Table(body.this.name, tuple(columns), tuple(pk), tuple(f for f in fks if f), partition, cluster)


def _column(item: exp.ColumnDef, dialect: str) -> tuple[Column, bool, ForeignKey | None]:
    kind = item.args.get("kind")
    type_text = kind.sql(dialect=dialect) if kind is not None else ""
    type_text = "" if schema_text.UNKNOWN_TYPE.casefold() in type_text.casefold() else type_text
    is_pk = not_null = False
    fk = None
    for constraint in item.args.get("constraints") or []:
        k = constraint.args.get("kind")
        if isinstance(k, exp.PrimaryKeyColumnConstraint):
            is_pk = True
        elif isinstance(k, exp.NotNullColumnConstraint) and not k.args.get("allow_null"):
            not_null = True
        elif isinstance(k, exp.Reference):
            fk = _fk((item.name,), k)
    return Column(item.name, type_text, not_null or is_pk), is_pk, fk


def _fk(columns: tuple[str, ...], reference) -> ForeignKey | None:
    if not isinstance(reference, exp.Reference):
        return None
    target = reference.this
    table = target.find(exp.Table) if not isinstance(target, exp.Table) else target
    if table is None:
        return None
    ref_cols = tuple(_ident(e) for e in target.expressions) if isinstance(target, exp.Schema) else ()
    return ForeignKey(columns, table.name, ref_cols or ("id",))


def _layout(stmt: exp.Create) -> tuple[tuple[str, ...], tuple[str, ...]]:
    partition, cluster = [], []
    props = stmt.args.get("properties")
    for prop in props.expressions if props is not None else []:
        name = type(prop).__name__
        cols = [c.name for c in prop.find_all(exp.Column)] or [i.name for i in prop.find_all(exp.Identifier)]
        if "Partition" in name:
            partition += cols
        elif "Cluster" in name:
            cluster += cols
    return tuple(dict.fromkeys(partition)), tuple(dict.fromkeys(cluster))


def _index(stmt: exp.Expression) -> Index | None:
    if not isinstance(stmt, exp.Create) or str(stmt.args.get("kind") or "").upper() != "INDEX":
        return None
    index = stmt.this
    table = index.args.get("table") if isinstance(index, exp.Index) else None
    if table is None:
        return None
    params = index.args.get("params")
    cols = [c.name for c in params.find_all(exp.Column)] if params is not None else []
    return Index(index.name or f"idx_{table.name}", table.name, tuple(cols))


def has_schema(text: str) -> bool:
    return bool(re.search(r"\bcreate\s+(?:or\s+replace\s+)?(?:temp\w*\s+)?table\b", text or "", re.IGNORECASE)) \
        or schema_text.has_list_or_describe(text)
