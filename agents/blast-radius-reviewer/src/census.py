"""Pure: the change census. What the diff removed, added and changed, as flat `Fact`s that the probes read:
symbols and signatures, file deletions and renames, dependency versions, and (via `census_lines`) literals,
keys, wire fields, SQL and timing. Every token is copied from a diff line, so it is verbatim in the input."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import census_lines
import census_sql
from diff_parse import DiffFile, Line
from facts import Fact

_SYMBOL_PATTERNS = (
    re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>\w+)\s*\((?P<params>.*)"),
    re.compile(r"^\s*class\s+(?P<name>\w+)"),
    re.compile(r"^\s*(?P<export>export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>\w+)\s*"
               r"\((?P<params>.*)"),
    re.compile(r"^\s*(?P<export>export\s+)(?:const|let|var|class|interface|type|enum)\s+(?P<name>\w+)"
               r"(?:\s*=\s*(?:async\s*)?\((?P<params>.*))?"),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>\w+)\s*\((?P<params>.*)"),
    re.compile(r"^\s*(?P<export>pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(?P<name>\w+)\s*(?:<[^>]*>)?\((?P<params>.*)"),
    re.compile(r"^\s*(?P<export>public\s+)(?:static\s+|final\s+|abstract\s+|synchronized\s+)*[\w<>\[\],\s]+?\s+"
               r"(?P<name>\w+)\s*\((?P<params>.*)"),
    re.compile(r"^\s*(?:override\s+|suspend\s+)*fun\s+(?P<name>\w+)\s*\((?P<params>.*)"),
)
_ROUTE = re.compile(r"(?:@\w+\.(?:route|get|post|put|delete|patch)|\brouter\.(?:get|post|put|delete|patch)|"
                    r"@(?:Get|Post|Put|Delete|Patch|Request)Mapping)\(\s*(?:value\s*=\s*|path\s*=\s*)?[\"']"
                    r"(?P<route>[^\"']+)")
_SKIP_PARAMS = {"self", "cls", "*", "/", ""}


@dataclass(frozen=True)
class Symbol:
    name: str
    signature: str
    params: tuple[tuple[str, str | None], ...]   # (name, default text or None)
    public: bool
    line: Line


@dataclass
class Census:
    facts: list[Fact] = field(default_factory=list)

    def of(self, kind: str) -> list[Fact]:
        return [f for f in self.facts if f.kind == kind]


def build(files: list[DiffFile]) -> Census:
    facts: list[Fact] = []
    for f in files:
        facts += _file_facts(f)
        if f.kind in ("source", "api-schema", "config", "build"):
            facts += _symbol_facts(f) if f.kind == "source" else []
            facts += census_lines.facts_for(f)
        elif f.kind == "migration":
            facts += census_sql.sql_facts(f)
        elif f.kind == "test":
            facts += census_lines.test_expectation_facts(f)
        elif f.kind == "prompt":
            facts += census_lines.facts_for(f, literals_only=True)
    facts += census_sql.dependency_facts(files)
    facts += _whole_diff_facts(files)
    return Census(facts)


def _file_facts(f: DiffFile) -> list[Fact]:
    if f.status == "deleted":
        return [Fact("deleted", f.path, None, _search_name(f.path), "removed", f"{f.path} deleted")]
    if f.status == "renamed" and f.old_path and f.old_path != f.path:
        return [Fact("deleted", f.old_path, None, _search_name(f.old_path), "renamed",
                     f"{f.old_path} renamed to {f.path}")]
    if f.kind == "lock":
        return [Fact("lock", f.path, None, PurePosixPath(f.path).name, "changed", f"lockfile {f.path} changed")]
    if f.kind == "generated":
        return [Fact("generated", f.path, None, PurePosixPath(f.path).name, "changed", f"{f.path} is generated")]
    return []


def _search_name(path: str) -> str:
    p = PurePosixPath(path)
    return p.stem if p.suffix in (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rb", ".rs") else p.name


def symbols_in(lines: list[Line]) -> dict[str, Symbol]:
    found: dict[str, Symbol] = {}
    for ln in lines:
        for pattern in _SYMBOL_PATTERNS:
            m = pattern.match(ln.text)
            if m and m.group("name") not in found:
                name = m.group("name")
                params = _params(m.groupdict().get("params"))
                public = _is_public(name, ln.text, m.groupdict().get("export"))
                found[name] = Symbol(name, " ".join(ln.text.split()), params, public, ln)
                break
        route = _ROUTE.search(ln.text)
        if route and route.group("route") not in found:
            found[route.group("route")] = Symbol(route.group("route"), ln.text.strip(), (), True, ln)
    return found


def _is_public(name: str, text: str, export: str | None) -> bool:
    if export:
        return True
    stripped = text.lstrip()
    if stripped.startswith(("def ", "async def ", "class ")):
        return not name.startswith("_")
    if stripped.startswith("func "):
        return name[:1].isupper()
    return False


def _params(raw: str | None) -> tuple[tuple[str, str | None], ...]:
    if raw is None:
        return ()
    depth, cur, parts = 0, "", []
    for ch in raw:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            if depth == 0 and ch == ")":
                break
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    parts.append(cur)
    out = []
    for p in parts:
        name = re.split(r"[:=\s]", p.strip().lstrip("*&"), maxsplit=1)[0]
        if name not in _SKIP_PARAMS:
            out.append((name, p.split("=", 1)[1].strip() if "=" in p else None))
    return tuple(out)


def signature_change(old: Symbol, new: Symbol) -> str:
    """'' when the signatures match; else what changed, in words built from the parameter lists."""
    if old.signature == new.signature:
        return ""
    old_names, new_names = [p for p, _ in old.params], [p for p, _ in new.params]
    added_required = [p for p, default in new.params if p not in old_names and default is None]
    removed = [p for p in old_names if p not in new_names]
    if added_required:
        return "added required parameter " + ", ".join(f"`{p}`" for p in added_required)
    if removed:
        return "removed parameter " + ", ".join(f"`{p}`" for p in removed)
    if old_names != new_names and sorted(old_names) == sorted(new_names):
        return "reordered parameters"
    if old_names == new_names and old.params != new.params:
        return "changed parameter defaults"
    if old_names == new_names and "(" in old.signature:
        return "changed parameter or return types"
    return ""


def _symbol_facts(f: DiffFile) -> list[Fact]:
    removed, added = symbols_in(f.changed("-")), symbols_in(f.changed("+"))
    facts = []
    for name, old in removed.items():
        flags = ("public",) if old.public else ()
        if name not in added:
            facts.append(Fact("symbol", f.path, old.line.number, name, "removed",
                              f"`{name}` removed or renamed", flags))
            continue
        what = signature_change(old, added[name])
        if what == "changed parameter defaults":
            facts.append(Fact("behaviour", f.path, added[name].line.number, name, "changed",
                              f"`{name}` parameter defaults changed"))
        elif what:
            facts.append(Fact("symbol", f.path, added[name].line.number, name, "changed",
                              f"`{name}` signature: {what}", flags + (("required",) if "required" in what else ())))
    return facts


def _whole_diff_facts(files: list[DiffFile]) -> list[Fact]:
    facts: list[Fact] = []
    sources = [f for f in files if f.kind == "source" and f.status != "deleted"]
    if sources and not any(f.kind == "test" for f in files):
        first = sources[0]
        symbols = [x for x in symbols_in(first.changed("+") + first.changed("-"))]
        token = symbols[0] if symbols else _search_name(first.path)
        facts.append(Fact("untested", first.path, None, token, "changed",
                          f"{len(sources)} source file(s) changed and no test file is in the diff"))
    schemas = [f for f in files if f.kind == "api-schema"]
    if schemas and not any(f.kind == "generated" for f in files):
        facts.append(Fact("schema_no_gen", schemas[0].path, None, PurePosixPath(schemas[0].path).name, "changed",
                          "schema changed with no regenerated client in the diff"))
    workers = [f for f in sources if re.search(r"worker|tasks?|consumer|celery|jobs?|queue", f.path, re.IGNORECASE)]
    webs = [f for f in sources if re.search(r"api|views?|routes?|handlers?|controllers?|server", f.path, re.IGNORECASE)]
    if workers and webs and workers[0] is not webs[0]:
        facts.append(Fact("timing", workers[0].path, None, _search_name(workers[0].path), "changed",
                          "worker and web code changed together; they may restart at different times",
                          ("rollout",)))
    return facts
