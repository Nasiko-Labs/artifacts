"""Pure: cut the reviewable code into triage batches of at most 12,000 characters, at most 5 batches.
A file larger than the room left is split at a line boundary; what does not fit is skipped and named in
the coverage note (never truncated silently)."""

from __future__ import annotations

from collections.abc import Sequence

from diff_parse import CodeFile, Line

BATCH_CHARS = 12_000
MAX_BATCHES = 5
LINE_OVERHEAD = 8  # the "L123 +| " prefix the model sees


def cost(lines: Sequence[Line]) -> int:
    return sum(len(ln.text) + LINE_OVERHEAD for ln in lines)


def plan(files: Sequence[CodeFile], max_chars: int = BATCH_CHARS,
         max_batches: int = MAX_BATCHES) -> tuple[list[tuple[CodeFile, ...]], list[dict]]:
    """(batches, skipped [{file, reason, lines}])."""
    batches: list[tuple[CodeFile, ...]] = []
    skipped: list[dict] = []
    current: list[CodeFile] = []
    size = 0
    for code in files:
        rest = code.lines
        while rest:
            if len(batches) == max_batches:
                skipped.append({"file": code.path, "reason": "size cap", "lines": f"{rest[0].no}-{rest[-1].no}"})
                break
            take = _fit(rest, max_chars - size, force=not current)
            if take:
                current.append(CodeFile(code.path, rest[:take], code.is_diff))
                size += cost(rest[:take])
                rest = rest[take:]
            if rest:
                batches.append(tuple(current))
                current, size = [], 0
    if current:
        batches.append(tuple(current))
    return batches, skipped


def _fit(lines: Sequence[Line], room: int, force: bool) -> int:
    used = 0
    for n, ln in enumerate(lines):
        used += len(ln.text) + LINE_OVERHEAD
        if used > room:
            return n if n or not force else 1
    return len(lines)
