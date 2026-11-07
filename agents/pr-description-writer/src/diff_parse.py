"""Pure: a unified diff (git diff, .patch) or a `git diff --stat` read into files and line counts.

Lines of the pasted message that are not part of the diff (a request, commit messages, ticket text) are
returned separately as `outside`, in order, so the caller can use them as context. Hunk bodies are consumed by
their `@@` counts; an empty line inside a hunk counts as a context line (chat pastes strip trailing spaces)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_GIT = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_HEADER = re.compile(r"^(?:index [0-9a-f]+\.\.[0-9a-f]+|new file mode|deleted file mode|old mode|new mode|"
                     r"similarity index|dissimilarity index|rename from|rename to|copy from|copy to)\b")
_STAT = re.compile(r"^\s*(\S.*?)\s+\|\s+(\d+|Bin\b.*)\s*[+\-]*\s*$")
_STAT_SUMMARY = re.compile(r"^\s*(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?")
_FENCE = re.compile(r"^\s*```\s*(?:diff|patch|git)?\s*$")


@dataclass
class FileDiff:
    path: str
    old_path: str = ""
    status: str = "modified"  # added | deleted | modified | renamed
    plus: list[str] = field(default_factory=list)
    minus: list[str] = field(default_factory=list)
    binary: bool = False
    text: list[str] = field(default_factory=list)  # this file's raw diff lines

    @property
    def added(self) -> int:
        return len(self.plus)

    @property
    def removed(self) -> int:
        return len(self.minus)


@dataclass(frozen=True)
class ParsedDiff:
    files: tuple[FileDiff, ...] = ()
    outside: tuple[str, ...] = ()
    stat_only: bool = False
    stat_totals: tuple[int, int] | None = None
    raw: str = ""

    @property
    def found(self) -> bool:
        return bool(self.files)


def _strip_prefix(path: str) -> str:
    path = path.strip().split("\t")[0]
    return path[2:] if path[:2] in ("a/", "b/") else path


class _Reader:
    def __init__(self, lines: list[str]):
        self.lines = lines
        self.files: list[FileDiff] = []
        self.outside: list[str] = []
        self.diff_lines: list[str] = []
        self.i = 0
        self.open_git = False  # a `diff --git` line was read and its `---`/`+++` pair has not come yet

    def run(self) -> None:
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if self._start_file(line) or self._header(line) or self._hunk(line):
                continue
            self.outside.append(line)
            self.i += 1

    def _take(self, current: FileDiff | None = None) -> str:
        line = self.lines[self.i]
        self.diff_lines.append(line)
        if current is not None:
            current.text.append(line)
        self.i += 1
        return line

    def _start_file(self, line: str) -> bool:
        git = _GIT.match(line)
        if git:
            self.files.append(FileDiff(path=git.group(2), old_path=git.group(1)))
            self.open_git = True
            self._take(self.files[-1])
            return True
        nxt = self.lines[self.i + 1] if self.i + 1 < len(self.lines) else ""
        if line.startswith("--- ") and nxt.startswith("+++ "):
            if not self.open_git:
                self.files.append(FileDiff(path=""))
            self.open_git = False
            current = self.files[-1]
            old, new = _strip_prefix(line[4:]), _strip_prefix(nxt[4:])
            current.old_path = old if old != "/dev/null" else current.old_path
            current.path = new if new != "/dev/null" else (current.path or old)
            current.status = "added" if old == "/dev/null" else "deleted" if new == "/dev/null" else current.status
            self._take(current)
            self._take(current)
            return True
        return False

    def _header(self, line: str) -> bool:
        if not self.files or not (_HEADER.match(line) or line.startswith("Binary files ")):
            return False
        current = self.files[-1]
        if line.startswith("new file mode"):
            current.status = "added"
        elif line.startswith("deleted file mode"):
            current.status = "deleted"
        elif line.startswith(("rename to", "rename from")):
            current.status = "renamed"
        elif line.startswith("Binary files "):
            current.binary = True
        self._take(current)
        return True

    def _hunk(self, line: str) -> bool:
        match = _HUNK.match(line)
        if not match or not self.files:
            return False
        current = self.files[-1]
        self.open_git = False
        old_left = int(match.group(1)) if match.group(1) is not None else 1
        new_left = int(match.group(2)) if match.group(2) is not None else 1
        self._take(current)
        while self.i < len(self.lines) and (old_left > 0 or new_left > 0):
            body = self.lines[self.i]
            head = body[:1]
            if head == "+":
                current.plus.append(body[1:])
                new_left -= 1
            elif head == "-":
                current.minus.append(body[1:])
                old_left -= 1
            elif head == " " or body == "":
                old_left, new_left = old_left - 1, new_left - 1
            elif head != "\\":
                break
            self._take(current)
        while self.i < len(self.lines) and self.lines[self.i].startswith("\\"):
            self._take(current)
        return True


def parse(text: str) -> ParsedDiff:
    """The diff in `text`, else the `--stat` listing in it, else an empty result with every line outside."""
    lines = [line for line in (text or "").replace("\r\n", "\n").split("\n") if not _FENCE.match(line)]
    reader = _Reader(lines)
    reader.run()
    files = tuple(f for f in reader.files if f.path)
    if files:
        return ParsedDiff(files, tuple(reader.outside), raw="\n".join(reader.diff_lines))
    return _parse_stat(lines)


def _parse_stat(lines: list[str]) -> ParsedDiff:
    files: list[FileDiff] = []
    outside: list[str] = []
    totals: tuple[int, int] | None = None
    stat_lines: list[str] = []
    for line in lines:
        summary = _STAT_SUMMARY.match(line)
        stat = _STAT.match(line)
        if summary:
            totals = (int(summary.group(2) or 0), int(summary.group(3) or 0))
            stat_lines.append(line)
        elif stat:
            files.append(FileDiff(path=stat.group(1).strip(), binary=stat.group(2).startswith("Bin")))
            stat_lines.append(line)
        else:
            outside.append(line)
    if not files:
        return ParsedDiff(outside=tuple(lines))
    return ParsedDiff(tuple(files), tuple(outside), stat_only=True, stat_totals=totals, raw="\n".join(stat_lines))
