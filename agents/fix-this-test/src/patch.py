"""Pure: the unified diff, computed by code (difflib) from the original files to the final files, and its
per-file line counts. The model never writes the patch."""

from __future__ import annotations

import difflib


def file_diff(path: str, before: str, after: str) -> list[str]:
    lines = list(difflib.unified_diff(_lines(before), _lines(after), fromfile=f"a/{path}", tofile=f"b/{path}"))
    return lines


def unified(originals: dict[str, str], finals: dict[str, str]) -> str:
    out: list[str] = []
    for path, before in originals.items():
        out.extend(file_diff(path, before, finals.get(path, before)))
    return "".join(out)


def stats(originals: dict[str, str], finals: dict[str, str]) -> list[dict]:
    """[{"path", "added", "removed"}] for each changed file, in paste order."""
    items = []
    for path, before in originals.items():
        lines = file_diff(path, before, finals.get(path, before))
        added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
        if added or removed:
            items.append({"path": path, "added": added, "removed": removed})
    return items


def changed_lines(originals: dict[str, str], finals: dict[str, str]) -> int:
    return sum(item["added"] + item["removed"] for item in stats(originals, finals))


def _lines(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n\\ No newline at end of file\n"
    return lines
