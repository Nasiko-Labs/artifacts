"""Pure: the upgrade target and the one-line manifest edit.

Target: the smallest listed fixed version above the declared minimum within the same major; else the
smallest above it at all, flagged as a major bump. The edit replaces the version token on the line the
declaration sits on, and is kept only when: the token occurs exactly once on that line, the line count
is unchanged, exactly one line differs, and the edited declaration re-parses to a range whose minimum
is the target. Anything else (variables, markers, multi-line entries) is a copy-only suggestion.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass

import manifests
import ranges
import versions
from dependency import Dep

_VERSION_TOKEN = re.compile(r"v?\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.\-+]*)?")


@dataclass(frozen=True)
class Edit:
    path: str
    old_line: str
    new_line: str
    new_content: str
    diff: str


def target(eco: str, fixed: Sequence[str], current: str | None) -> tuple[str | None, bool]:
    """(target version text, is a major bump). None when no fixed version is above `current`."""
    now = versions.parse(eco, ranges.padded(eco, current)) if current else None
    parsed = sorted(v for v in (versions.parse(eco, f) for f in fixed) if v is not None)
    above = [v for v in parsed if now is None or v > now]
    if not above:
        return None, False
    same = [v for v in above if now is not None and v.major == now.major]
    chosen = same[0] if same else above[0]
    return chosen.text, now is not None and chosen.major != now.major


def edit(content: str, dep: Dep, new_version: str) -> Edit | None:
    if not dep.editable or dep.line <= 0:
        return None
    declared = ranges.parse_declared(dep.ecosystem, dep.spec)
    if declared is None:
        return None
    lines = content.splitlines(keepends=True)
    if dep.line > len(lines):
        return None
    old = lines[dep.line - 1]
    token = _floor_token(dep, declared)
    if token is None or len(_occurrences(old, token)) != 1:
        return None
    replacement = _styled(token, new_version)
    start = _occurrences(old, token)[0].start()
    new = old[:start] + replacement + old[start + len(token):]
    new_lines = [*lines[:dep.line - 1], new, *lines[dep.line:]]
    new_content = "".join(new_lines)
    if not _verified(content, new_content, dep, replacement):
        return None
    diff = "".join(difflib.unified_diff(lines, new_lines, fromfile=f"a/{dep.path}", tofile=f"b/{dep.path}", n=1))
    return Edit(dep.path, old.rstrip("\r\n"), new.rstrip("\r\n"), new_content, diff)


def _floor_token(dep: Dep, declared: ranges.Declared) -> str | None:
    """The version text in the spec that is the declared minimum (`^4.17.0` → `4.17.0`)."""
    if declared.minimum is None:
        return None
    for candidate in _VERSION_TOKEN.findall(dep.spec):
        parsed = versions.parse(dep.ecosystem, ranges.padded(dep.ecosystem, candidate))
        if parsed is not None and parsed == declared.minimum:
            return candidate
    return None


def _occurrences(line: str, token: str) -> list[re.Match]:
    return list(re.finditer(rf"(?<![\w.]){re.escape(token)}(?![\w.])", line))


def _styled(token: str, new_version: str) -> str:
    bare = new_version.removeprefix("v")
    return f"v{bare}" if token.startswith("v") else bare


def _verified(old: str, new: str, dep: Dep, replacement: str) -> bool:
    before, after = old.splitlines(), new.splitlines()
    if len(before) != len(after) or sum(a != b for a, b in zip(before, after, strict=True)) != 1:
        return False
    wanted = versions.parse(dep.ecosystem, ranges.padded(dep.ecosystem, replacement))
    for again in manifests.find(dep.path, new, dep.name, dep.ecosystem):
        if again.line != dep.line:
            continue
        declared = ranges.parse_declared(dep.ecosystem, again.spec)
        if declared is not None and wanted is not None and declared.minimum == wanted and ranges.contains(
                declared.intervals, wanted):
            return True
    return False
