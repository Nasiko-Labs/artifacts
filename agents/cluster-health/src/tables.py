"""Pure: kubectl's padded tables, ages and restart counts.

kubectl prints tables through a tab writer: every cell of a column starts at the header's offset. Rows are
cut at those offsets; a row whose cells do not line up (re-flowed by a chat window or an editor) is split on
runs of two or more spaces, then on single spaces with `(3m ago)` kept whole, and kept only when that gives
the header's column count. Every row keeps its original line as verbatim evidence."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_HEADER_CELL = re.compile(r"\S+(?: \S+)*")
_TWO_WORD = frozenset({"LAST SEEN", "FIRST SEEN", "NOMINATED NODE", "READINESS GATES", "NODE SELECTOR"})
_AGE_FULL = re.compile(r"^(?:\d+[smhdy])+$")
_AGE_PART = re.compile(r"(\d+)([smhdy])")
_MINUTES = {"s": 1 / 60, "m": 1.0, "h": 60.0, "d": 1440.0, "y": 525_600.0}
_RESTARTS = re.compile(r"^(\d+)(?:\s*\((\S+)\s+ago\))?$")
_RATIO = re.compile(r"^(\d+)/(\d+)$")
_AGO = re.compile(r"\(\S+ ago\)")
_PERCENT = re.compile(r"^(\d+)%$")
_TIMES = re.compile(r"\(x(\d+) over [^)]*\)")
_MEMORY = re.compile(r"^(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|K|M|G|T)?$")
_MIB = {None: 1 / 1_048_576, "Ki": 1 / 1024, "Mi": 1.0, "Gi": 1024.0, "Ti": 1_048_576.0,
        "K": 1000 / 1_048_576, "M": 1_000_000 / 1_048_576, "G": 1e9 / 1_048_576, "T": 1e12 / 1_048_576}


@dataclass(frozen=True)
class Row:
    cells: dict[str, str]
    line: str

    def get(self, column: str, default: str = "") -> str:
        return self.cells.get(column, default)


def header_columns(header: str) -> list[tuple[str, int]]:
    """(UPPERCASE column name, start offset) for each header cell; `LAST SEEN` stays one cell. A header whose
    padding was squeezed to single spaces is split on spaces, the known two-word names kept whole."""
    line = header.rstrip()
    if re.search(r"\S\s{2,}\S", line):
        return [(m.group(0).upper(), m.start()) for m in _HEADER_CELL.finditer(line)]
    out: list[tuple[str, int]] = []
    for m in re.finditer(r"\S+", line):
        word = m.group(0).upper()
        if out and f"{out[-1][0]} {word}" in _TWO_WORD:
            out[-1] = (f"{out[-1][0]} {word}", out[-1][1])
        else:
            out.append((word, m.start()))
    return out


def parse_rows(header: str, lines: Sequence[str]) -> tuple[list[Row], int]:
    """(rows, how many non-empty lines could not be read as rows of this table)."""
    columns = header_columns(header)
    rows, skipped = [], 0
    for raw in lines:
        line = raw.rstrip()
        if not line.strip() or set(line.strip()) <= {"-", " "}:
            continue
        cells = _by_offset(line, columns) or _by_runs(line, columns)
        if cells is None:
            skipped += 1
            continue
        rows.append(Row(dict(zip((name for name, _ in columns), cells, strict=True)), line))
    return rows, skipped


def _by_offset(line: str, columns: list[tuple[str, int]]) -> list[str] | None:
    starts = [start for _, start in columns]
    for start in starts[1:]:
        if start >= len(line) or line[start - 1] != " " or line[start] == " ":
            return None
    if line[: starts[0]].strip():
        return None
    ends = [*starts[1:], len(line)]
    return [line[s:e].strip() for s, e in zip(starts, ends, strict=True)]


def _by_runs(line: str, columns: list[tuple[str, int]]) -> list[str] | None:
    count = len(columns)
    free_text_last = columns[-1][0] == "MESSAGE"
    cells = re.split(r"\s{2,}", line.strip(), maxsplit=count - 1 if free_text_last else -1)
    if len(cells) == count:
        return cells
    tokens = _tokens(line)
    if free_text_last and len(tokens) > count:
        return [*tokens[: count - 1], " ".join(tokens[count - 1:])]
    return tokens if len(tokens) == count else None


def _tokens(line: str) -> list[str]:
    """Single-space split that keeps `17 (2m ago)` and `2m (x42 over 40m)` as one cell."""
    out: list[str] = []
    for token in line.split():
        if out and (token.startswith("(") or ("(" in out[-1] and not out[-1].endswith(")"))):
            out[-1] = f"{out[-1]} {token}"
        else:
            out.append(token)
    return out


def age_minutes(text: str | None) -> float | None:
    """`40m` → 40, `2h10m` → 130, `3d` → 4320; None when it is not a kubectl age."""
    value = (text or "").strip()
    if not _AGE_FULL.match(value):
        return None
    return sum(int(n) * _MINUTES[unit] for n, unit in _AGE_PART.findall(value))


def restarts(text: str) -> tuple[int, float | None]:
    """`5` → (5, None); `5 (3m ago)` → (5, 3.0)."""
    match = _RESTARTS.match((text or "").strip())
    if not match:
        return 0, None
    return int(match.group(1)), age_minutes(match.group(2)) if match.group(2) else None


def ratio(text: str) -> tuple[int, int] | None:
    match = _RATIO.match((text or "").strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def as_int(text: str) -> int | None:
    value = (text or "").strip()
    return int(value) if value.isdigit() else None


def percent(text: str) -> int | None:
    match = _PERCENT.match((text or "").strip())
    return int(match.group(1)) if match else None


def times(text: str) -> int:
    """`2m (x42 over 40m)` → 42; a plain age → 1."""
    match = _TIMES.search(text or "")
    return int(match.group(1)) if match else 1


def memory_mib(text: str) -> float | None:
    match = _MEMORY.match((text or "").strip())
    return float(match.group(1)) * _MIB[match.group(2)] if match else None


def strip_ago(text: str) -> str:
    return _AGO.sub("", text or "").strip()


def fmt_minutes(minutes: float | None) -> str:
    """130 → `2h10m`, 4320 → `3d`, 40 → `40m`; kubectl's own short form."""
    if minutes is None:
        return "unknown"
    total = int(minutes)
    if total >= 1440:
        days, hours = divmod(total // 60, 24)
        return f"{days}d{hours}h" if hours and days < 3 else f"{days}d"
    if total >= 60:
        hours, mins = divmod(total, 60)
        return f"{hours}h{mins}m" if mins else f"{hours}h"
    return f"{total}m"
