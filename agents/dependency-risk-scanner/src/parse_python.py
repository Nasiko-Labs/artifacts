"""Pure: Python manifests. `requirements*.txt` (extras, markers, comments, `-r`/`-e` skipped) and
`pyproject.toml` (PEP 621 `[project]` plus poetry's dependency tables and groups)."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping

from dependency import Dep, locate_line, scope_word

_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([^;]*?)\s*(;.*)?$")
_COMMENT = re.compile(r"(^|\s)#.*$")


def requirements(path: str, text: str) -> tuple[Dep, ...]:
    scope = scope_word(path.rsplit("/", 1)[-1])
    out = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _COMMENT.sub("", raw).strip()
        if not line or line.startswith("-") or "://" in line:
            continue
        dep = _requirement(line, path, scope, number)
        if dep is not None:
            out.append(dep)
    return tuple(out)


def _requirement(entry: str, path: str, scope: str, number: int) -> Dep | None:
    match = _REQ.match(entry)
    if match is None:
        return None
    name, _extras, spec, marker = match.groups()
    return Dep(name=name, spec=spec.strip(), ecosystem="pip", scope=scope, line=number, path=path,
               editable=marker is None)


def pyproject(path: str, text: str) -> tuple[Dep, ...]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return ()
    lines = text.splitlines()
    project = data.get("project") if isinstance(data.get("project"), Mapping) else {}
    out = [*_pep621(path, lines, project.get("dependencies"), "runtime")]
    optional = project.get("optional-dependencies")
    for group, entries in (optional.items() if isinstance(optional, Mapping) else ()):
        out.extend(_pep621(path, lines, entries, scope_word(group)))
    poetry = (data.get("tool") or {}).get("poetry") if isinstance(data.get("tool"), Mapping) else None
    if isinstance(poetry, Mapping):
        out.extend(_poetry(path, lines, poetry))
    return tuple(out)


def _pep621(path: str, lines: list[str], entries: object, scope: str) -> list[Dep]:
    if not isinstance(entries, list):
        return []
    out = []
    for entry in entries:
        if not isinstance(entry, str):
            continue
        dep = _requirement(entry, path, scope, locate_line(lines, (f'"{entry}"',)) or locate_line(
            lines, (f"'{entry}'",)))
        if dep is not None:
            out.append(dep if dep.line else _not_one_line(dep))
    return out


def _poetry(path: str, lines: list[str], poetry: Mapping) -> list[Dep]:
    tables = [("dependencies", poetry.get("dependencies"), "runtime"),
              ("dev-dependencies", poetry.get("dev-dependencies"), "dev")]
    groups = poetry.get("group")
    for group, body in (groups.items() if isinstance(groups, Mapping) else ()):
        if isinstance(body, Mapping):
            tables.append((f"group.{group}.dependencies", body.get("dependencies"),
                           "runtime" if group == "main" else "dev"))
    out = []
    for header, table, scope in tables:
        if not isinstance(table, Mapping):
            continue
        start = _section_start(lines, f"tool.poetry.{header}")
        for name, value in table.items():
            if name.casefold() == "python":
                continue
            out.append(_poetry_dep(path, lines, start, name, value, scope))
    return out


def _poetry_dep(path: str, lines: list[str], start: int, name: str, value: object, scope: str) -> Dep:
    spec = value if isinstance(value, str) else value.get("version", "") if isinstance(value, Mapping) else ""
    spec = spec if isinstance(spec, str) else ""
    end = _section_end(lines, start)
    line = locate_line(lines, (name, f'"{spec}"'), start, end) if spec and start >= 0 else 0
    return Dep(name=name, spec=spec, ecosystem="pip", scope=scope, line=line, path=path,
               editable=bool(line and spec))


def _section_start(lines: list[str], header: str) -> int:
    wanted = f"[{header}]"
    return next((i + 1 for i, line in enumerate(lines) if line.strip() == wanted), -1)


def _section_end(lines: list[str], start: int) -> int:
    if start < 0:
        return start
    return next((i for i in range(start, len(lines)) if lines[i].strip().startswith("[")
                 and not lines[i].strip().startswith("[\"")), len(lines))


def _not_one_line(dep: Dep) -> Dep:
    return Dep(dep.name, dep.spec, dep.ecosystem, dep.scope, 0, dep.path, editable=False)
