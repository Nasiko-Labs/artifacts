"""Pure: census facts for schema migrations (destructive SQL and ORM operations) and dependency manifests
(name, old version, new version, whether the semver major differs)."""

from __future__ import annotations

import re

from diff_parse import DiffFile, Line
from facts import Fact

_SQL = (
    (re.compile(r"ALTER\s+TABLE\s+(\w+)\s+DROP\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?(\w+)", re.IGNORECASE), "drop column", 2),
    (re.compile(r"RENAME\s+COLUMN\s+(\w+)\s+TO\s+\w+", re.IGNORECASE), "rename column", 1),
    (re.compile(r"ALTER\s+COLUMN\s+(\w+)\s+(?:SET\s+DATA\s+)?TYPE", re.IGNORECASE), "column type change", 1),
    (re.compile(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\w+)", re.IGNORECASE), "drop table", 1),
    (re.compile(r"TRUNCATE\s+(?:TABLE\s+)?(\w+)", re.IGNORECASE), "truncate", 1),
    (re.compile(r"op\.drop_column\(\s*['\"]\w+['\"]\s*,\s*['\"](\w+)", re.IGNORECASE), "drop column", 1),
    (re.compile(r"removeColumn\(\s*['\"]\w+['\"]\s*,\s*['\"](\w+)", re.IGNORECASE), "drop column", 1),
    (re.compile(r"RemoveField\([^)]*name\s*=\s*['\"](\w+)", re.IGNORECASE), "drop column", 1),
    (re.compile(r"(?:op\.drop_table|dropTable)\(\s*['\"](\w+)", re.IGNORECASE), "drop table", 1),
    (re.compile(r"DROP\s+INDEX\s+(?:IF\s+EXISTS\s+)?(\w+)", re.IGNORECASE), "drop index", 1),
    (re.compile(r"CREATE\s+UNIQUE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.IGNORECASE), "unique index", 1),
)
_ADD_NOT_NULL = re.compile(r"ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(\w+)[^;]*NOT\s+NULL", re.IGNORECASE)
_DELETE_ALL = re.compile(r"DELETE\s+FROM\s+(\w+)\s*;?\s*$", re.IGNORECASE)
NON_DESTRUCTIVE_SQL = {"drop index", "unique index"}
_DEP = (
    re.compile(r"^\s*([A-Za-z0-9][\w.\-]*)(?:\[[^\]]*\])?\s*(?:===|==|>=|~=|<=)\s*v?(\d[\w.\-*]*)"),
    re.compile(r"^\s*\"(@?[\w.\-/]+)\"\s*:\s*\"[\^~>=<\s]*(\d[\w.\-]*)\""),
    re.compile(r"^\s*\"([A-Za-z0-9][\w.\-]*)\s*(?:==|>=|~=)\s*(\d[\w.\-]*)"),
    re.compile(r"^\s*([\w\-]+)\s*=\s*(?:\{[^}]*version\s*=\s*)?\"[\^~=]*(\d[\w.\-]*)\""),
    re.compile(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-/]+)\s+v(\d[\w.\-+]*)"),
)

def sql_facts(f: DiffFile) -> list[Fact]:
    facts = []
    for ln in f.changed("+"):
        for pattern, op, group in _SQL:
            m = pattern.search(ln.text)
            if m:
                flags = () if op in NON_DESTRUCTIVE_SQL else ("destructive",)
                facts.append(Fact("sql", f.path, ln.new_no, m.group(group), "changed", op, flags))
        add = _ADD_NOT_NULL.search(ln.text)
        if add and "DEFAULT" not in ln.text.upper():
            facts.append(Fact("sql", f.path, ln.new_no, add.group(1), "added", "NOT NULL column without "
                                     "a default", ("destructive",)))
        delete = _DELETE_ALL.search(ln.text)
        if delete and "WHERE" not in ln.text.upper():
            facts.append(Fact("sql", f.path, ln.new_no, delete.group(1), "changed", "delete without WHERE",
                                     ("destructive",)))
    if not facts and f.hunks and f.status != "deleted":
        facts.append(Fact("sql", f.path, None, f.path.rsplit("/", 1)[-1], "changed", "schema migration"))
    return facts


def dependency_facts(files: list[DiffFile]) -> list[Fact]:
    facts = []
    for f in (x for x in files if x.kind == "build"):
        old, new = _versions(f.changed("-")), _versions(f.changed("+"))
        for name in sorted(set(old) | set(new)):
            o, n = old.get(name), new.get(name)
            if o and n and o[0] == n[0]:
                continue
            line = (n or o)[1]
            major = bool(o and n and o[0].split(".")[0] != n[0].split(".")[0])
            change = "changed" if o and n else ("added" if n else "removed")
            detail = f"`{name}` {o[0] if o else 'none'} -> {n[0] if n else 'removed'}"
            facts.append(Fact("dep", f.path, line, name, change, detail, ("major",) if major else ()))
    return facts


def _versions(lines: list[Line]) -> dict[str, tuple[str, int | None]]:
    found = {}
    for ln in lines:
        for pattern in _DEP:
            m = pattern.match(ln.text)
            if m:
                found[m.group(1)] = (m.group(2), ln.number)
                break
    return found
