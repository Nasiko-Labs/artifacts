"""Pure: unified-diff parsing and the changed-line map. A file's patch hunks (`@@ -a,b +c,d @@`) give the
set of new-file line numbers that were added or modified; a finding gates the PR only when its lines
intersect that set. A pasted diff is split into files the same way, and a file whose contents could not
be read is rebuilt from its hunks with every line at its real line number (blank lines elsewhere)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from records import ChangedFile

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DIFF_GIT = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)
_PLAIN_HEADER = re.compile(r"^--- (?:a/)?(\S+).*\n\+\+\+ (?:b/)?(\S+)", re.MULTILINE)


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: tuple[str, ...]


def parse_hunks(patch: str) -> tuple[Hunk, ...]:
    hunks: list[Hunk] = []
    header: tuple[int, int, int, int] | None = None
    body: list[str] = []
    for line in (patch or "").splitlines():
        m = _HUNK.match(line)
        if m:
            if header:
                hunks.append(Hunk(*header, tuple(body)))
            a, b, c, d = m.groups()
            header = (int(a), int(b) if b is not None else 1, int(c), int(d) if d is not None else 1)
            body = []
        elif header is not None and line[:1] in (" ", "+", "-", "\\", ""):
            body.append(line if line else " ")
    if header:
        hunks.append(Hunk(*header, tuple(body)))
    return tuple(hunks)


def new_side(patch: str) -> tuple[tuple[int, str, bool], ...]:
    """(new line number, text, added?) for every kept or added line of the patch."""
    out: list[tuple[int, str, bool]] = []
    for h in parse_hunks(patch):
        n = h.new_start
        for line in h.lines:
            mark = line[:1]
            if mark in ("-", "\\"):
                continue
            out.append((n, line[1:], mark == "+"))
            n += 1
    return tuple(out)


def changed_lines(patch: str) -> frozenset[int]:
    """New-file line numbers added or modified by the patch."""
    return frozenset(n for n, _, added in new_side(patch) if added)


def added_lines(patch: str) -> tuple[tuple[int, str], ...]:
    return tuple((n, text) for n, text, added in new_side(patch) if added)


def fragment(patch: str) -> str:
    """The new side of the hunks as file text, each line at its real number, blank lines elsewhere."""
    lines = new_side(patch)
    if not lines:
        return ""
    last = max(n for n, _, _ in lines)
    body = [""] * last
    for n, text, _ in lines:
        body[n - 1] = text
    return "\n".join(body) + "\n"


def parse_unified(text: str) -> tuple[ChangedFile, ...]:
    """A pasted diff split into files: `diff --git` blocks, else plain `---`/`+++` blocks."""
    starts = list(_DIFF_GIT.finditer(text or ""))
    if not starts:
        return _plain(text or "")
    out = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        block = text[m.start():end]
        old, new = m.group(1), m.group(2)
        status = ("removed" if "\ndeleted file mode" in block else "added" if "\nnew file mode" in block
                  else "renamed" if old != new else "modified")
        if "\nBinary files " in block or "GIT binary patch" in block:
            status = "binary"
        at = block.find("\n@@")
        out.append(ChangedFile(new if status != "removed" else old, status, block[at + 1:] if at != -1 else ""))
    return tuple(out)


def _plain(text: str) -> tuple[ChangedFile, ...]:
    heads = list(_PLAIN_HEADER.finditer(text))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        block = text[m.end():end]
        old, new = m.group(1), m.group(2)
        status = "removed" if new == "/dev/null" else "added" if old == "/dev/null" else "modified"
        at = block.find("@@")
        out.append(ChangedFile(old if status == "removed" else new, status, block[at:] if at != -1 else ""))
    return tuple(out)


def looks_like_diff(text: str) -> bool:
    body = text or ""
    return "diff --git " in body or bool(_PLAIN_HEADER.search(body) and "\n@@ " in body)
