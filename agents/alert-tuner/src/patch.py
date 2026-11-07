"""Pure: find an alert's rule in YAML alert-definition files and rewrite only its `for:`-style duration.

A rule starts at an `alert:` or `name:` key; its block is the following lines indented deeper than the
rule's own line (a list item's dash counts as its indent). The rule matches an alert when its name is the
alert's display name or a whole word of it, and the block has a duration key (`for`, `duration`,
`evaluation_period`) as a direct sibling of the name key. Exactly one match across all files is required.
The patch changes that one value; `verify` re-checks the result line by line before anything is written.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from records import RepoFile
from send_guard import content_hash

_RULE_START = re.compile(r"^(?P<indent>\s*)(?P<dash>-\s+)?(?P<key>alert|name)\s*:\s*(?P<value>.+?)\s*(?:#.*)?$")
_DURATION_LINE = re.compile(r"^(?P<prefix>(?P<indent>\s*)(?:-\s+)?(?P<key>for|duration|evaluation_period)\s*:\s*)"
                            r"(?P<q>[\"']?)(?P<value>[0-9][0-9a-z]*)(?P=q)(?P<rest>\s*(?:#.*)?)$")
_PART = re.compile(r"(\d+)(ms|s|m|h|d|w)")
_UNIT_SECS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


@dataclass(frozen=True)
class Patch:
    path: str
    line: int              # 1-based
    old_value: str
    new_value: str
    old_line: str
    new_line: str
    old_text: str
    new_text: str
    file_sha: str = ""

    @property
    def diff(self) -> str:
        return unified_diff(self.path, self.old_text, self.new_text)

    @property
    def digest(self) -> str:
        return content_hash(self.new_text)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Patch:
        return Patch(**{k: (int(data[k]) if k == "line" else str(data.get(k) or "")) for k in Patch.__dataclass_fields__})


@dataclass(frozen=True)
class Match:
    path: str
    name: str
    duration_line: int     # 0-based index of the duration line


def parse_duration(text: str) -> float | None:
    """`90s` → 90, `2m` → 120, `1h30m` → 5400; seconds, or None when not a duration."""
    value = (text or "").strip().strip("\"'").casefold()
    if not value or _PART.sub("", value):
        return None
    return float(sum(int(n) * _UNIT_SECS[u] for n, u in _PART.findall(value)))


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _content_indent(m: re.Match) -> int:
    return len(m.group("indent")) + len(m.group("dash") or "")


def _block_end(lines: Sequence[str], start: int) -> int:
    own = _indent(lines[start])
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i].lstrip().startswith("#") and _indent(lines[i]) <= own:
            return i
    return len(lines)


def _names_alert(rule_name: str, alert: str) -> bool:
    name = rule_name.strip().strip("\"'").casefold()
    if len(name) < 3:
        return False
    title = alert.casefold()
    return name == title or re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", title) is not None


def find_rules(files: Sequence[RepoFile], alert: str) -> list[Match]:
    found: list[Match] = []
    for f in files:
        lines = f.text.splitlines()
        for i, line in enumerate(lines):
            m = _RULE_START.match(line)
            if not m or not _names_alert(m.group("value"), alert):
                continue
            key_indent = _content_indent(m)
            for j in range(i + 1, _block_end(lines, i)):
                d = _DURATION_LINE.match(lines[j])
                if d and _indent(lines[j]) == key_indent and not d.group(0).lstrip().startswith("-"):
                    found.append(Match(f.path, m.group("value").strip("\"'"), j))
                    break
    return found


def make_patch(f: RepoFile, match: Match, minutes: int) -> Patch | str:
    """The one-line edit, or why there is none."""
    lines = f.text.split("\n")
    d = _DURATION_LINE.match(lines[match.duration_line])
    if d is None:
        return "the duration line could not be read"
    old = parse_duration(d.group("value"))
    if old is None:
        return f"`{d.group('value')}` is not a duration I can read"
    new_value = f"{minutes}m"
    if minutes * 60 <= old:
        return f"its `{d.group('key')}` is already {d.group('value')}, not shorter than {new_value}"
    new_line = f"{d.group('prefix')}{d.group('q')}{new_value}{d.group('q')}{d.group('rest')}"
    new_lines = [*lines[:match.duration_line], new_line, *lines[match.duration_line + 1:]]
    p = Patch(f.path, match.duration_line + 1, d.group("value"), new_value, lines[match.duration_line], new_line,
              f.text, "\n".join(new_lines), f.sha)
    problem = verify(p)
    return problem or p


def verify(p: Patch) -> str | None:
    """Exactly one line changed, at the recorded place, same indentation, a longer duration; else a reason."""
    old_lines, new_lines = p.old_text.split("\n"), p.new_text.split("\n")
    if len(old_lines) != len(new_lines):
        return "the edit would change the number of lines"
    changed = [i for i, (a, b) in enumerate(zip(old_lines, new_lines, strict=True)) if a != b]
    if changed != [p.line - 1]:
        return "the edit would change more than the one duration line"
    a, b = old_lines[p.line - 1], new_lines[p.line - 1]
    if a != p.old_line or b != p.new_line or _indent(a) != _indent(b):
        return "the edited line does not match the planned edit"
    da, db = _DURATION_LINE.match(a), _DURATION_LINE.match(b)
    if not da or not db or da.group("prefix") != db.group("prefix") or da.group("rest") != db.group("rest"):
        return "the edit would change more than the duration value"
    old, new = parse_duration(da.group("value")), parse_duration(db.group("value"))
    if old is None or new is None or new <= old:
        return "the new duration must be longer than the old one"
    return None


def unified_diff(path: str, old: str, new: str) -> str:
    lines = difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{path}", f"b/{path}", n=2, lineterm="")
    return "\n".join(lines)


def with_minutes(p: Patch, minutes: int) -> Patch | str:
    """The same edit with another duration (an edit instruction at the commit card), re-verified."""
    line = p.line - 1
    return make_patch(RepoFile(p.path, p.old_text, p.file_sha), Match(p.path, "", line), minutes)
