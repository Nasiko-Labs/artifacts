"""Pure: the schema model the user pasted (tables, columns, keys, partitioning, indexes), its JSON form for the
checkpointer, the mapping sqlglot's `qualify` needs, and the compact digest the model sees."""

from __future__ import annotations

from dataclasses import dataclass, field

_INT = ("int", "serial", "bigint", "smallint", "tinyint", "int64", "integer")
_NUM = ("numeric", "decimal", "float", "double", "real", "money", "number", "bignumeric")
_TEXT = ("char", "text", "string", "varchar", "clob", "uuid", "enum", "json")
_TIME = ("date", "time", "timestamp", "datetime", "interval")


@dataclass(frozen=True)
class Column:
    name: str
    type: str = ""            # "" = not stated
    not_null: bool = False


@dataclass(frozen=True)
class ForeignKey:
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()
    partition_by: tuple[str, ...] = ()
    cluster_by: tuple[str, ...] = ()

    def column(self, name: str) -> Column | None:
        key = name.casefold()
        return next((c for c in self.columns if c.name.casefold() == key), None)


@dataclass(frozen=True)
class Index:
    name: str
    table: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class Schema:
    tables: tuple[Table, ...] = ()
    indexes: tuple[Index, ...] = ()
    skipped: tuple[str, ...] = field(default=())   # statements that did not parse, named for the reply

    def __bool__(self) -> bool:
        return bool(self.tables)

    def table(self, name: str) -> Table | None:
        key = name.rsplit(".", 1)[-1].strip('`"[]').casefold()
        return next((t for t in self.tables if t.name.casefold() == key), None)

    def names(self) -> set[str]:
        """Every table and column name, case-folded: what a model's text may name."""
        return {t.name.casefold() for t in self.tables} | {c.name.casefold() for t in self.tables for c in t.columns}

    def mapping(self) -> dict[str, dict[str, str]]:
        return {t.name: {c.name: (c.type or "UNKNOWN") for c in t.columns} for t in self.tables}

    def digest(self) -> str:
        return "\n".join(_digest_line(t) for t in self.tables) + _index_lines(self.indexes)

    def merged(self, other: Schema) -> Schema:
        """`other` replaces tables of the same name; indexes and skips accumulate."""
        names = {t.name.casefold() for t in other.tables}
        kept = tuple(t for t in self.tables if t.name.casefold() not in names)
        return Schema(kept + other.tables, self.indexes + other.indexes, self.skipped + other.skipped)

    def to_dict(self) -> dict:
        return {"tables": [_table_dict(t) for t in self.tables],
                "indexes": [{"name": i.name, "table": i.table, "columns": list(i.columns)} for i in self.indexes],
                "skipped": list(self.skipped)}

    @staticmethod
    def from_dict(data: dict | None) -> Schema:
        data = data or {}
        tables = tuple(_table_from(t) for t in data.get("tables") or [])
        indexes = tuple(Index(i["name"], i["table"], tuple(i["columns"])) for i in data.get("indexes") or [])
        return Schema(tables, indexes, tuple(data.get("skipped") or []))


def type_family(type_text: str) -> str:
    """int | number | text | time | bool | other | "" (unknown), for type-mismatch rules."""
    t = (type_text or "").casefold()
    if not t or t == "unknown":
        return ""
    for family, words in (("int", _INT), ("number", _NUM), ("time", _TIME), ("text", _TEXT)):
        if any(w in t for w in words):
            return family
    return "bool" if "bool" in t else "other"


def _digest_line(t: Table) -> str:
    fks = {c: f"{fk.ref_table}.{fk.ref_columns[i] if i < len(fk.ref_columns) else '?'}"
           for fk in t.foreign_keys for i, c in enumerate(fk.columns)}
    cols = []
    for c in t.columns:
        bits = [c.name, c.type or "type?"]
        bits += ["PK"] if c.name in t.primary_key else []
        bits += [f"FK->{fks[c.name]}"] if c.name in fks else []
        bits += ["NOT NULL"] if c.not_null and c.name not in t.primary_key else []
        cols.append(" ".join(bits))
    extra = (f" PARTITION BY {', '.join(t.partition_by)}" if t.partition_by else "") + \
        (f" CLUSTER BY {', '.join(t.cluster_by)}" if t.cluster_by else "")
    return f"{t.name}({', '.join(cols)}){extra}"


def _index_lines(indexes: tuple[Index, ...]) -> str:
    return "".join(f"\nINDEX {i.name} ON {i.table}({', '.join(i.columns)})" for i in indexes)


def _table_dict(t: Table) -> dict:
    return {"name": t.name, "columns": [[c.name, c.type, c.not_null] for c in t.columns],
            "primary_key": list(t.primary_key), "partition_by": list(t.partition_by),
            "cluster_by": list(t.cluster_by),
            "foreign_keys": [[list(f.columns), f.ref_table, list(f.ref_columns)] for f in t.foreign_keys]}


def _table_from(d: dict) -> Table:
    return Table(d["name"], tuple(Column(n, ty, bool(nn)) for n, ty, nn in d["columns"]), tuple(d["primary_key"]),
                 tuple(ForeignKey(tuple(c), r, tuple(rc)) for c, r, rc in d["foreign_keys"]),
                 tuple(d["partition_by"]), tuple(d["cluster_by"]))
