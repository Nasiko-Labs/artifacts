"""Pure: the diff between the fetched originals and the edited files, computed by difflib (never by a
model), with per-file added/removed counts. Line endings: a file that used CRLF keeps CRLF, and the
original's trailing-newline convention is restored when the edit dropped it."""

from __future__ import annotations

import difflib
import hashlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FileChange:
    path: str
    final: str
    added: int
    removed: int
    new_file: bool
    diff: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping) -> FileChange:
        return FileChange(str(data["path"]), str(data["final"]), int(data["added"]), int(data["removed"]),
                          bool(data["new_file"]), str(data["diff"]))

    @property
    def changed(self) -> int:
        return self.added + self.removed


def restore_conventions(original: str | None, edited: str) -> str:
    """Keep the original's line endings and trailing newline."""
    if original is None:
        return edited
    text = edited.replace("\r\n", "\n")
    if "\r\n" in original:
        text = text.replace("\n", "\r\n")
    newline = "\r\n" if "\r\n" in original else "\n"
    if original.endswith("\n") and not text.endswith("\n"):
        text += newline
    elif original and not original.endswith("\n") and text.endswith(newline):
        text = text[:-len(newline)]
    return text


def file_diff(path: str, original: str | None, final: str) -> FileChange | None:
    """The change for one file, or None when nothing changed."""
    if original is not None and original == final:
        return None
    before = (original or "").splitlines(keepends=True)
    after = final.splitlines(keepends=True)
    lines = list(difflib.unified_diff(before, after, fromfile="/dev/null" if original is None else f"a/{path}",
                                      tofile=f"b/{path}", lineterm=""))
    body = []
    for line in lines:
        body.append(line if line.endswith("\n") else line + "\n")
    added = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
    return FileChange(path, final, added, removed, original is None, "".join(body).replace("\r", ""))


def changes(originals: Mapping[str, str], edited: Mapping[str, str]) -> list[FileChange]:
    """Every changed or new file, in path order. A deleted original is not a change this agent makes."""
    out = []
    for path in sorted(edited):
        original = originals.get(path)
        final = restore_conventions(original, edited[path])
        change = file_diff(path, original, final)
        if change is not None:
            out.append(change)
    return out


def full_diff(items: list[FileChange]) -> str:
    return "".join(c.diff for c in items)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
