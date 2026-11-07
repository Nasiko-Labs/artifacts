"""Pure: what the judge sees for one fired probe. A numbered slice of the diff hunk around the probe's line,
and the pasted-context excerpts that mention the probe's token (found by code, each at most 600 chars)."""

from __future__ import annotations

from diff_parse import DiffFile, Line

LINES_AROUND = 6
FILE_HEAD_LINES = 10
MAX_EXCERPT_CHARS = 1_200
CONTEXT_EXCERPT_CHARS = 600
MAX_CONTEXT_EXCERPTS = 3


def number(line: Line) -> str:
    no = line.number
    return f"{no if no is not None else '':>5} {line.kind} {line.text}"


def around(f: DiffFile | None, line_no: int | None) -> str:
    if f is None or not f.hunks:
        return f"(file {f.path}: {f.status})" if f is not None else ""
    lines: list[Line] = []
    for hunk in f.hunks:
        idx = next((i for i, ln in enumerate(hunk.lines) if line_no is not None and line_no in
                    (ln.new_no, ln.old_no)), None)
        if idx is not None:
            lines = list(hunk.lines[max(0, idx - LINES_AROUND): idx + LINES_AROUND + 1])
            break
    if not lines:
        lines = list(f.hunks[0].lines[:FILE_HEAD_LINES])
    text = f"file: {f.path}\n" + "\n".join(number(ln) for ln in lines)
    return text[:MAX_EXCERPT_CHARS]


def context_excerpts(context: str, token: str) -> list[str]:
    """Context lines that mention `token` (case-insensitive), each with a little surrounding text."""
    if not context or not token:
        return []
    lines = context.splitlines()
    needle = token.casefold()
    found: list[str] = []
    for i, line in enumerate(lines):
        if needle in line.casefold():
            window = "\n".join(lines[max(0, i - 1): i + 2])
            found.append(window[:CONTEXT_EXCERPT_CHARS])
            if len(found) >= MAX_CONTEXT_EXCERPTS:
                break
    return found
