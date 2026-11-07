"""Pure: a unified diff parsed in code. Every path, new-file line number and added line the model may cite
comes from here, never from the model.

Lenient on purpose, because diffs arrive pasted into a chat box: trailing spaces on blank context lines
may be lost (an empty line inside a hunk is a blank context line), `diff --git` headers are optional, and a
hunk with no file header takes the most recent `# file: <name>` label (an attached file's name) or
`UNNAMED`. Text that is not part of the diff is returned as `rest` (the request, spec, standards).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

UNNAMED = "(unnamed file)"
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_GIT = re.compile(r"^diff --git (?:\"?a/)?(.+?)\"? (?:\"?b/)?(.+?)\"?$")
_HEADER = re.compile(r"^(?:index |new file mode|deleted file mode|old mode|new mode|similarity index|"
                     r"dissimilarity index|rename from |rename to |copy from |copy to )")
_BINARY = re.compile(r"^(?:Binary files .* differ|GIT binary patch)")
_FILE_LABEL = re.compile(r"^#\s*file:\s*(\S.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class Line:
    kind: str            # "+" added, "-" removed, " " context
    text: str
    new_no: int | None   # new-file line number (added and context lines)


@dataclass(frozen=True)
class Hunk:
    header: str
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class FileDiff:
    path: str
    old_path: str = ""
    status: str = "modified"      # added | deleted | modified | renamed
    binary: bool = False
    hunks: tuple[Hunk, ...] = ()

    @property
    def added(self) -> dict[int, str]:
        return {ln.new_no: ln.text for h in self.hunks for ln in h.lines if ln.kind == "+"}

    @property
    def context(self) -> dict[int, str]:
        return {ln.new_no: ln.text for h in self.hunks for ln in h.lines if ln.kind == " "}

    @property
    def removed(self) -> tuple[str, ...]:
        return tuple(ln.text for h in self.hunks for ln in h.lines if ln.kind == "-")

    @property
    def added_count(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "+")

    @property
    def removed_count(self) -> int:
        return len(self.removed)


@dataclass(frozen=True)
class Parsed:
    files: tuple[FileDiff, ...]
    rest: str


@dataclass
class _Builder:
    path: str
    old_path: str = ""
    status: str = "modified"
    binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)
    has_paths: bool = False

    def build(self) -> FileDiff:
        status = "renamed" if self.status == "modified" and self.old_path and self.old_path != self.path \
            else self.status
        return FileDiff(self.path, self.old_path, status, self.binary, tuple(self.hunks))


def _strip_prefix(path: str) -> str:
    path = path.strip().split("\t")[0].strip().strip('"')
    return path[2:] if path[:2] in ("a/", "b/") else path


def parse(text: str) -> Parsed:
    lines = (text or "").replace("\r\n", "\n").split("\n")
    files: list[_Builder] = []
    rest: list[str] = []
    cur: _Builder | None = None
    in_header = False
    label, new_label = "", False
    i = 0
    while i < len(lines):
        line = lines[i]
        git = _GIT.match(line)
        if git:
            cur = _Builder(_strip_prefix(git.group(2)), _strip_prefix(git.group(1)))
            files.append(cur)
            in_header, new_label, i = True, False, i + 1
            continue
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            cur = _file_paths(cur, files, line[4:], lines[i + 1][4:])
            in_header, new_label, i = True, False, i + 2
            continue
        hunk = _HUNK.match(line)
        if hunk:
            if cur is None or new_label:  # a hunk with no file header of its own
                cur = _Builder(label or UNNAMED)
                files.append(cur)
                new_label = False
            parsed, i = _read_hunk(lines, i, hunk)
            cur.hunks.append(parsed)
            in_header = False
            continue
        if in_header and cur is not None and (_HEADER.match(line) or _BINARY.match(line)):
            _header_line(cur, line)
            i += 1
            continue
        in_header = False
        file_label = _FILE_LABEL.match(line)
        if file_label:
            label, new_label = file_label.group(1).strip(), True
        rest.append(line)
        i += 1
    return Parsed(tuple(f.build() for f in files), "\n".join(rest).strip())


def _file_paths(cur: _Builder | None, files: list[_Builder], old: str, new: str) -> _Builder:
    old_path, new_path = _strip_prefix(old), _strip_prefix(new)
    if cur is None or cur.has_paths or cur.hunks:
        cur = _Builder(new_path if new_path != "/dev/null" else old_path)
        files.append(cur)
    cur.has_paths = True
    if old_path == "/dev/null":
        cur.status, cur.path = "added", new_path
    elif new_path == "/dev/null":
        cur.status, cur.path = "deleted", old_path
    else:
        cur.path, cur.old_path = new_path, old_path
    return cur


def _header_line(cur: _Builder, line: str) -> None:
    if _BINARY.match(line):
        cur.binary = True
    elif line.startswith("new file mode"):
        cur.status = "added"
    elif line.startswith("deleted file mode"):
        cur.status = "deleted"
    elif line.startswith("rename from "):
        cur.old_path = line[len("rename from "):].strip()
    elif line.startswith("rename to "):
        cur.path = line[len("rename to "):].strip()


def _read_hunk(lines: list[str], i: int, match: re.Match) -> tuple[Hunk, int]:
    old_left = int(match.group(2)) if match.group(2) is not None else 1
    new_left = int(match.group(4)) if match.group(4) is not None else 1
    new_no = int(match.group(3))
    out: list[Line] = []
    j = i + 1
    while j < len(lines):
        line = lines[j]
        counted = old_left > 0 or new_left > 0
        if line.startswith("\\"):
            j += 1
            continue
        if not counted and (not line or line[0] not in "+- " or _starts_file(lines, j)):
            break
        if counted and _starts_file(lines, j):
            break
        if line.startswith("+"):
            out.append(Line("+", line[1:], new_no))
            new_no, new_left = new_no + 1, new_left - 1
        elif line.startswith("-"):
            out.append(Line("-", line[1:], None))
            old_left -= 1
        elif line.startswith(" ") or line == "":
            out.append(Line(" ", line[1:], new_no))
            new_no, old_left, new_left = new_no + 1, old_left - 1, new_left - 1
        else:
            break
        j += 1
    return Hunk(lines[i], tuple(out)), j


def _starts_file(lines: list[str], j: int) -> bool:
    line = lines[j]
    if line.startswith("diff --git ") or _HUNK.match(line):
        return True
    return line.startswith("--- ") and j + 1 < len(lines) and lines[j + 1].startswith("+++ ")
