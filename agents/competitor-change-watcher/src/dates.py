"""Pure: dates written in changelogs, parsed in code. ISO (2026-10-28, 2026/10/28), "October 28, 2026",
"Oct 28 2026" and "28 October 2026". Never guessed: an unparsable or impossible date is no date."""

from __future__ import annotations

import re
from datetime import date

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), start=1)}
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_ISO = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
_MDY = re.compile(rf"\b{_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE)
_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH},?\s+(\d{{4}})\b", re.IGNORECASE)


def _make(y: str, m: int | str, d: str) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def find(text: str) -> tuple[date, re.Match] | None:
    """The earliest date in `text` with its match."""
    found = []
    for rx, build in ((_ISO, lambda g: _make(g[0], g[1], g[2])),
                      (_MDY, lambda g: _make(g[2], _MONTHS[g[0][:3].casefold()], g[1])),
                      (_DMY, lambda g: _make(g[2], _MONTHS[g[1][:3].casefold()], g[0]))):
        for match in rx.finditer(text or ""):
            parsed = build(match.groups())
            if parsed is not None:
                found.append((match.start(), parsed, match))
                break
    if not found:
        return None
    _, parsed, match = min(found, key=lambda f: f[0])
    return parsed, match


def leading(text: str) -> tuple[date, str] | None:
    """A date at the very start of `text`, and the text after it (separators stripped)."""
    hit = find(text)
    if hit is None or hit[1].start() > 0:
        return None
    return hit[0], text[hit[1].end():].lstrip(" -–—:|·,)]").strip()
