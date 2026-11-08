"""Pure: clean the converter's Markdown and compute the facts and warnings about it, all in code."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

MAX_SHEET_ROWS = 500
MIN_CHARS_PER_PAGE = 20
MIN_TEXT_RATIO = 0.01
SCRAMBLED_RUN = 15
MAX_DOC_CHARS = 400_000

SCANNED = "looks scanned (no text layer); no OCR in this agent"
_DATA_IMAGE = re.compile(r"!\[([^\]]*)\]\(data:[^)]*\)")
_TRAILING = re.compile(r"[ \t]+$", re.MULTILINE)
_BLANKS = re.compile(r"\n{3,}")
_EMPTY_ROW = re.compile(r"^\|(?:\s*\|)+\s*$")
_SEPARATOR = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")
_GAPS = re.compile(r"\S {2,}\S")


@dataclass(frozen=True)
class Cleaned:
    markdown: str
    warnings: tuple[str, ...]
    scanned: bool = False


def normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n\n")
    text = _DATA_IMAGE.sub(lambda m: f"![{m.group(1)}](image omitted)", text)
    text = _TRAILING.sub("", text)
    text = "\n".join(_drop_empty_rows(text.split("\n")))
    return _BLANKS.sub("\n\n", text).strip() + "\n"


def _drop_empty_rows(lines: list[str]) -> list[str]:
    """Empty table rows go, except a header row (the next line is the separator)."""
    kept = []
    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if _EMPTY_ROW.match(line) and not _SEPARATOR.match(line) and not _SEPARATOR.match(nxt):
            continue
        kept.append(line)
    return kept


def label_sheets(markdown: str, sheets: Sequence[str]) -> str:
    names = set(sheets)
    return "\n".join(f"## Sheet: {line[3:].strip()}" if line.startswith("## ") and line[3:].strip() in names else line
                     for line in markdown.split("\n"))


def cap_sheet_rows(markdown: str, limit: int = MAX_SHEET_ROWS) -> tuple[str, list[int]]:
    """Each sheet's table cut at `limit` data rows, with a visible line; returns the omitted counts."""
    out: list[str] = []
    omitted: list[int] = []
    rows = 0
    cut = 0
    for line in markdown.split("\n") + ["## "]:
        if line.startswith("## "):
            if cut:
                out.append(f"\n({cut:,} more rows omitted)")
                omitted.append(cut)
            rows, cut = 0, 0
            out.append(line)
            continue
        if line.startswith("|") and not _SEPARATOR.match(line):
            rows += 1
            if rows > limit + 1:  # +1: the header row
                cut += 1
                continue
        out.append(line)
    return "\n".join(out[:-1]), omitted


def clean(raw: str, fmt: str, input_bytes: int, units: dict, sheets: Sequence[str] = ()) -> Cleaned:
    markdown = normalise(raw)
    warnings: list[str] = []
    if fmt in ("xlsx", "xls"):
        markdown, omitted = cap_sheet_rows(label_sheets(markdown, sheets))
        if omitted:
            noun = "sheet" if len(omitted) == 1 else "sheets"
            warnings.append(f"{len(omitted)} {noun} cut at {MAX_SHEET_ROWS} rows ({sum(omitted):,} more rows omitted).")
    text_chars = len(markdown.strip())
    scanned = fmt == "pdf" and text_chars < MIN_CHARS_PER_PAGE * max(1, units.get("pages", 1))
    if scanned:
        warnings.append(f"{SCANNED}.")
    elif fmt in ("html", "xml") and input_bytes and text_chars < input_bytes * MIN_TEXT_RATIO:
        warnings.append("Very little text extracted.")
    if _scrambled(markdown):
        warnings.append("Layout may be scrambled (many one-word lines; a multi-column page read out of order).")
    if fmt == "pdf" and not scanned and _tabular(markdown):
        warnings.append("Tables in PDFs are best-effort.")
    if len(markdown) > MAX_DOC_CHARS:
        extra = len(markdown) - MAX_DOC_CHARS
        markdown = markdown[:MAX_DOC_CHARS] + f"\n\n({extra:,} more characters not included)\n"
        warnings.append(f"Output cut at {MAX_DOC_CHARS:,} characters ({extra:,} more not included).")
    return Cleaned(markdown, tuple(warnings), scanned)


def _scrambled(markdown: str) -> bool:
    run = 0
    for line in markdown.split("\n"):
        words = line.split()
        if len(words) == 1 and not line.startswith(("#", "|", "-", "*")):
            run += 1
            if run >= SCRAMBLED_RUN:
                return True
        elif words:
            run = 0
    return False


def _tabular(markdown: str) -> bool:
    return any(line.startswith("|") for line in markdown.split("\n")) or \
        sum(1 for line in markdown.split("\n") if len(_GAPS.findall(line)) >= 2) >= 3
