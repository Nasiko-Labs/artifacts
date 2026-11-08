"""Pure: a checklist the user pasted for an area this agent can't read. A section starts with a line
`marketing:` or `engineering:`; its lines `- [x] done` / `- [ ] open due 2026-11-18 @owner` become the same
rows a tool read would give, with source `pasted`."""

from __future__ import annotations

import re
from collections.abc import Mapping

import dates
from records import Issue, Task

AREAS = ("engineering", "marketing")
_HEADER = re.compile(r"^\s*(engineering|eng|marketing)\s*:\s*(.*)$", re.IGNORECASE)
_ITEM = re.compile(r"^\s*[-*]\s*\[(?P<mark>[ xX])\]\s*(?P<rest>.+?)\s*$")
_DUE = re.compile(r"\bdue (?P<d>\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_OWNER = re.compile(r"(?:^|\s)@(?P<o>[\w.-]+)")
_PRIORITY = re.compile(r"\b(urgent|high|medium|low)\b", re.IGNORECASE)


def split_sections(text: str) -> tuple[str, dict[str, str]]:
    """(the message without pasted sections, area -> pasted lines)."""
    head: list[str] = []
    sections: dict[str, list[str]] = {}
    area: str | None = None
    for line in text.splitlines():
        header = _HEADER.match(line)
        if header:
            area = "engineering" if header.group(1).casefold().startswith("eng") else "marketing"
            sections = {**sections, area: [*sections.get(area, []), header.group(2)]}
            continue
        if area is not None and (_ITEM.match(line) or not line.strip()):
            sections = {**sections, area: [*sections[area], line]}
            continue
        area = None
        head = [*head, line]
    pasted = {k: "\n".join(v).strip() for k, v in sections.items() if any(_ITEM.match(x) for x in v)}
    return "\n".join(head), pasted


def _items(text: str) -> list[tuple[bool, str]]:
    return [(m["mark"].lower() == "x", m["rest"]) for m in map(_ITEM.match, text.splitlines()) if m]


def tasks(text: str) -> tuple[Task, ...]:
    rows = []
    for n, (done, rest) in enumerate(_items(text), start=1):
        due, owner = _DUE.search(rest), _OWNER.search(rest)
        title = _OWNER.sub(" ", _DUE.sub(" ", rest)).strip()
        rows.append(Task(f"pasted-{n}", None, " ".join(title.split()), done,
                         dates.tool_date(due["d"]) if due else None, owner["o"] if owner else None, "pasted"))
    return tuple(rows)


def issues(text: str) -> tuple[Issue, ...]:
    rows = []
    for n, (done, rest) in enumerate(_items(text), start=1):
        owner, priority = _OWNER.search(rest), _PRIORITY.search(rest)
        title = " ".join(_OWNER.sub(" ", rest).split())
        blocked = "block" in title.casefold()
        rows.append(Issue(f"pasted-{n}", None, title, "Blocked" if blocked and not done else
                          ("Done" if done else "Open"), "completed" if done else "started",
                          priority.group(1).casefold() if priority else "none", owner["o"] if owner else None,
                          (), None, "pasted"))
    return tuple(rows)


def rows_for(pasted: Mapping[str, str]) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    if pasted.get("engineering"):
        out = {**out, "engineering": issues(pasted["engineering"])}
    if pasted.get("marketing"):
        out = {**out, "marketing": tasks(pasted["marketing"])}
    return out
