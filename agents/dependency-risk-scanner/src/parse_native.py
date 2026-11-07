"""Pure: `package.json` (runtime vs dev tables), `go.mod` (single and block `require`), `Cargo.toml`
(dependency tables, inline tables, renamed packages; a path or git dependency has no version)."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping

from dependency import Dep, locate_line, scope_word

_NPM_TABLES = (("dependencies", "runtime"), ("optionalDependencies", "runtime"), ("peerDependencies", "runtime"),
               ("devDependencies", "dev"))
_GO_REQUIRE = re.compile(r"^\s*(?:require\s+)?([A-Za-z0-9][\w.\-~/]*)\s+(v[0-9][\w.\-+]*)(\s*//.*)?$")
_CARGO_TABLES = (("dependencies", "runtime"), ("dev-dependencies", "dev"), ("build-dependencies", "dev"))


def package_json(path: str, text: str) -> tuple[Dep, ...]:
    try:
        data = json.loads(text)
    except ValueError:
        return ()
    if not isinstance(data, Mapping):
        return ()
    lines = text.splitlines()
    out = []
    for table, scope in _NPM_TABLES:
        entries = data.get(table)
        if not isinstance(entries, Mapping):
            continue
        start = locate_line(lines, (f'"{table}"',))
        for name, spec in entries.items():
            if not isinstance(spec, str):
                continue
            line = locate_line(lines, (f'"{name}"', f'"{spec}"'), max(start - 1, 0))
            out.append(Dep(name=name, spec=spec, ecosystem="npm", scope=scope, line=line, path=path,
                           editable=bool(line)))
    return tuple(out)


def go_mod(path: str, text: str) -> tuple[Dep, ...]:
    out = []
    in_block = False
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("require ("):
            in_block = True
            continue
        if in_block and stripped == ")":
            in_block = False
            continue
        if not (in_block or stripped.startswith("require ")):
            continue
        match = _GO_REQUIRE.match(raw)
        if match:
            out.append(Dep(name=match.group(1), spec=match.group(2), ecosystem="go", scope="runtime", line=number,
                           path=path))
    return tuple(out)


def cargo_toml(path: str, text: str) -> tuple[Dep, ...]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return ()
    lines = text.splitlines()
    tables = [(name, data.get(name), scope) for name, scope in _CARGO_TABLES]
    targets = data.get("target")
    for body in (targets.values() if isinstance(targets, Mapping) else ()):
        if isinstance(body, Mapping):
            tables.extend((name, body.get(name), scope) for name, scope in _CARGO_TABLES)
    out = []
    for _table, entries, scope in tables:
        for key, value in (entries.items() if isinstance(entries, Mapping) else ()):
            out.append(_cargo_dep(path, lines, key, value, scope_word(scope)))
    return tuple(out)


def _cargo_dep(path: str, lines: list[str], key: str, value: object, scope: str) -> Dep:
    if isinstance(value, str):
        spec, name = value, key
    elif isinstance(value, Mapping):
        spec = value.get("version") if isinstance(value.get("version"), str) else ""
        name = value.get("package") if isinstance(value.get("package"), str) else key
    else:
        spec, name = "", key
    line = locate_line(lines, (key, f'"{spec}"')) if spec else 0
    return Dep(name=name, spec=spec, ecosystem="cargo", scope=scope, line=line, path=path,
               editable=bool(line and spec))
