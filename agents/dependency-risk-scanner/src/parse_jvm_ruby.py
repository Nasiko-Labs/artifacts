"""Pure: `pom.xml` (direct `<dependency>` entries; a `${property}` version stays verbatim and reads as
unknown), `build.gradle(.kts)` (string notation `group:artifact:version`) and `Gemfile` (`gem` lines,
`group ... do` blocks and inline `group:`)."""

from __future__ import annotations

import re

from dependency import Dep

_TAG = re.compile(r"<(groupId|artifactId|version|scope)>\s*([^<]*?)\s*</\1>")
_GRADLE = re.compile(
    r"^\s*(\w+)\s*\(?\s*['\"]([^:'\"\s]+):([^:'\"\s]+):([^'\"@\s]+)(?:@\w+)?['\"]")
_GEM = re.compile(r"^\s*gem\s+['\"]([^'\"]+)['\"]((?:\s*,\s*['\"][^'\"]*['\"])*)(.*)$")
_QUOTED = re.compile(r"['\"]([^'\"]*)['\"]")
_GROUP_START = re.compile(r"^\s*group\b(.*)\bdo\b")
_BLOCK_START = re.compile(r"\bdo\b\s*(\|.*\|)?\s*$")
_DEV_GROUPS = re.compile(r":(development|test)\b|['\"](development|test)['\"]")


def pom(path: str, text: str) -> tuple[Dep, ...]:
    lines = text.splitlines()
    out = []
    current: dict[str, tuple[str, int]] | None = None
    for number, line in enumerate(lines, start=1):
        if "<dependency>" in line:
            current = {}
        for tag, value in _TAG.findall(line):
            if current is not None:
                current = {**current, tag: (value, number)}
        if "</dependency>" in line and current is not None:
            dep = _pom_dep(path, current)
            if dep is not None:
                out.append(dep)
            current = None
    return tuple(out)


def _pom_dep(path: str, fields: dict[str, tuple[str, int]]) -> Dep | None:
    group, artifact = fields.get("groupId", ("", 0))[0], fields.get("artifactId", ("", 0))[0]
    if not artifact:
        return None
    version, line = fields.get("version", ("", 0))
    scope = "dev" if fields.get("scope", ("", 0))[0] in ("test", "provided") else "runtime"
    editable = bool(version) and "${" not in version
    return Dep(name=f"{group}:{artifact}" if group else artifact, spec=version, ecosystem="maven", scope=scope,
               line=line if version else 0, path=path, editable=editable)


def gradle(path: str, text: str) -> tuple[Dep, ...]:
    out = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = _GRADLE.match(line)
        if match is None:
            continue
        config, group, artifact, version = match.groups()
        scope = "dev" if config.casefold().startswith("test") else "runtime"
        out.append(Dep(name=f"{group}:{artifact}", spec=version, ecosystem="maven", scope=scope, line=number,
                       path=path, editable="$" not in version))
    return tuple(out)


def gemfile(path: str, text: str) -> tuple[Dep, ...]:
    out = []
    stack: list[bool] = []  # one entry per open `do` block: True when it is a dev/test group
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        group = _GROUP_START.match(line)
        if group:
            stack.append(bool(_DEV_GROUPS.search(group.group(1))))
            continue
        if _BLOCK_START.search(stripped):
            stack.append(False)
            continue
        if stripped == "end" and stack:
            stack.pop()
            continue
        match = _GEM.match(line)
        if match is None:
            continue
        name, versions_part, rest = match.groups()
        spec = ", ".join(_QUOTED.findall(versions_part))
        dev = any(stack) or bool(_DEV_GROUPS.search(rest or ""))
        out.append(Dep(name=name, spec=spec, ecosystem="gem", scope="dev" if dev else "runtime", line=number,
                       path=path, editable=bool(spec)))
    return tuple(out)
