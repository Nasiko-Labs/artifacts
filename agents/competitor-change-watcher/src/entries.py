"""Pure: split a changelog text into entries, with dates parsed in code.

- A heading that is only a date (`## 2026-10-28`) sets the date for what follows; each bullet or paragraph
  under it is its own entry.
- A heading with text, or a line that starts with a date and has text (`2026-10-28 - SAML SSO`), starts an
  entry; bullets and lines under it belong to it (a heading's body ends at the first blank line after it).
- A bullet outside such a heading is its own entry; other undated text is split by blank lines."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date

import dates
from records import Entry

_HEADING = re.compile(r"^\s*(?:#{1,6}\s+|\*\*(?=.+\*\*\s*$))(.*?)(?:\*\*)?\s*$")
_BULLET = re.compile(r"^\s*(?:[-*•+]|\d{1,3}[.)])\s+(.*)$")
_NO_WORDS = re.compile(r"[\W_]+")


@dataclass(frozen=True)
class _Open:
    lines: tuple[str, ...]
    date: date | None
    kind: str  # heading (open across blank lines) | dated | bullet | para


def _date_only(text: str) -> date | None:
    hit = dates.find(text)
    if hit is None:
        return None
    rest = text[:hit[1].start()] + text[hit[1].end():]
    return hit[0] if not _NO_WORDS.sub("", rest) else None


def _blocks(text: str):
    """(kind, text, date, date_only) per non-blank line; None for a blank line."""
    for raw in str(text or "").splitlines():
        if not raw.strip():
            yield None
            continue
        heading, bullet = _HEADING.match(raw), _BULLET.match(raw)
        if heading:
            body = heading.group(1).strip()
            only = _date_only(body)
            hit = dates.find(body)
            yield ("heading", body, only or (hit[0] if hit else None), only is not None)
        elif bullet:
            lead = dates.leading(bullet.group(1).strip())
            yield ("bullet", lead[1] if lead else bullet.group(1).strip(), lead[0] if lead else None, False)
        else:
            lead = dates.leading(raw.strip())
            if lead and not lead[1]:
                yield ("heading", raw.strip(), lead[0], True)
            elif lead:
                yield ("dated", lead[1], lead[0], False)
            else:
                yield ("plain", raw.strip(), None, False)


def split(text: str) -> list[tuple[str, date | None]]:
    """(entry text, date) in order of appearance."""
    out: list[tuple[str, date | None]] = []
    current: _Open | None = None
    section: date | None = None

    def close() -> None:
        nonlocal current
        if current is not None:
            out.append(("\n".join(current.lines), current.date))
        current = None

    for line in _blocks(text):
        if line is None:
            if current is not None and (current.kind != "heading" or len(current.lines) > 1):
                close()  # a heading stays open across a blank line only until it has a body
            continue
        kind, body, when, only = line
        if only:
            close()
            section = when
        elif kind in ("heading", "dated"):
            close()
            current = _Open((body,), when or section, kind)
        elif kind == "bullet" and current is not None and current.kind in ("heading", "dated"):
            current = replace(current, lines=(*current.lines, body))
        elif kind == "bullet":
            close()
            current = _Open((body,), when or section, "bullet")
        elif current is not None:
            current = replace(current, lines=(*current.lines, body))
        else:
            current = _Open((body,), section, "para")
    close()
    return [(t, d) for t, d in out if t.strip()]


def entries(text: str, competitor: str, source: str, start: int) -> list[Entry]:
    return [Entry(id=f"E{start + n}", competitor=competitor, source=source, text=t,
                  date=d.isoformat() if d else None) for n, (t, d) in enumerate(split(text))]
