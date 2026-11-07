"""Pure: one paste split into header lines, last meeting's notes and recent updates.

    Person: Priya / Role: ... / Last 1:1: 2026-10-21 / Next 1:1: ... / Topics: promotion, on-call
    Notes: (or "Last 1:1 notes:")   ...
    Updates: (or "Since then:")     ...

Unlabeled: split at a heading line that mentions updates / since / recent / this week / last two weeks; with
no such line the whole text is notes and updates mixed (every item is marked `pasted`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from inputs import envelope_updates
from prompts import strip_markers

HEADING_MAX_CHARS = 60

_HEADER = re.compile(r"^\s*(person|report|role|last 1:1|last one-on-one|next 1:1|next one-on-one|topics|agenda)"
                     r"\s*:\s*(.+?)\s*$", re.IGNORECASE)
_NOTES = re.compile(r"^\s*(?:last (?:1:1|one-on-one|meeting)(?:'s)? notes|(?:1:1 |meeting )?notes|last time)\s*:"
                    r"\s*(.*)$", re.IGNORECASE)
_UPDATES = re.compile(r"^\s*(?:recent updates|updates|since then|since last time|what happened since)\s*:\s*(.*)$",
                      re.IGNORECASE)
_HEADING = re.compile(r"\b(?:updates?|since|recent|this week|last two weeks)\b", re.IGNORECASE)
_REQUEST = re.compile(r"\b(?:prep|prepare|1:1|one-on-one)\b", re.IGNORECASE)
_DATE_HEADER = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")
_INLINE_LABEL = re.compile(r"(?<=[.!?;])[ \t]+(?=(?:recent updates|updates|since then|notes)\s*:)", re.IGNORECASE)
_INLINE_HEADER = re.compile(r"(?<=[.!?])[ \t]+(?=(?:person|role|topics|last 1:1|next 1:1)\s*:)", re.IGNORECASE)
HEADING_MAX_WORDS = 6


@dataclass(frozen=True)
class Paste:
    notes: str = ""
    updates: str = ""
    mixed: bool = False
    person: str = ""
    role: str = ""
    last_date: str = ""
    next_date: str = ""
    topics: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"notes": self.notes, "updates": self.updates, "mixed": self.mixed, "person": self.person,
                "role": self.role, "last_date": self.last_date, "next_date": self.next_date,
                "topics": list(self.topics)}

    @staticmethod
    def from_dict(data: dict | None) -> Paste:
        d = data or {}
        return Paste(str(d.get("notes") or ""), str(d.get("updates") or ""), bool(d.get("mixed")),
                     str(d.get("person") or ""), str(d.get("role") or ""), str(d.get("last_date") or ""),
                     str(d.get("next_date") or ""), tuple(str(t) for t in d.get("topics") or ()))

    def text(self) -> str:
        return "\n".join(p for p in (self.notes, self.updates) if p)


def _topics(value: str) -> tuple[str, ...]:
    return tuple(t.strip() for t in re.split(r"[;,]", value) if t.strip())


def _headers(lines: list[str]) -> tuple[dict, list[str]]:
    found: dict = {}
    rest: list[str] = []
    for line in lines:
        match = _HEADER.match(line)
        if not match:
            rest.append(line)
            continue
        key, value = match.group(1).casefold(), match.group(2)
        if key in ("person", "report"):
            found["person"] = value[:60]
        elif key == "role":
            found["role"] = value[:80]
        elif key.startswith("last"):
            found["last_date"] = value[:40]
        elif key.startswith("next"):
            found["next_date"] = value[:40]
        else:
            found["topics"] = found.get("topics", ()) + _topics(value)
    return found, rest


def _labeled(lines: list[str]) -> tuple[str, str, bool]:
    """(notes, updates, labeled?): the text after a Notes: label and after an Updates: label."""
    notes: list[str] = []
    updates: list[str] = []
    current = None
    for line in lines:
        n, u = _NOTES.match(line), _UPDATES.match(line)
        if n or u:
            current = notes if n else updates
            tail = (n or u).group(1)
            if tail.strip():
                current.append(tail)
        elif current is not None:
            current.append(line)
    if notes or updates:
        return "\n".join(notes).strip(), "\n".join(updates).strip(), True
    return "", "", False


def _unlabeled(lines: list[str]) -> tuple[str, str, bool]:
    for i, line in enumerate(lines):
        bare = line.strip().rstrip(":")
        short = len(bare) <= HEADING_MAX_CHARS and len(bare.split()) <= HEADING_MAX_WORDS
        if i and short and _HEADING.search(bare) and not bare.endswith("."):
            return "\n".join(lines[:i]).strip(), "\n".join(lines[i + 1:]).strip(), False
    return "\n".join(lines).strip(), "", True


def _drop_request_line(lines: list[str]) -> tuple[list[str], dict]:
    """A first line like "Prep my 1:1 with Priya" is the request, not notes; it may name the person."""
    if not lines:
        return lines, {}
    first = lines[0]
    if any(rx.match(first) for rx in (_HEADER, _NOTES, _UPDATES)) or not _REQUEST.search(first) or len(first) > 200:
        return lines, {}
    named = re.search(r"\bwith ([A-Z][a-z]+)\b", first)
    return lines[1:], ({"person": named.group(1)} if named else {})


def _break_inline_labels(text: str) -> str:
    """Puts each inline label ("... Priya. Notes: ...") on its own line."""
    text = _INLINE_LABEL.sub("\n", text)
    return _INLINE_HEADER.sub("\n", text)


def split(text: str, stored_notes: str = "") -> Paste:
    """Headers, notes and updates from one message. An envelope is updates; `stored_notes` (an earlier paste
    in this conversation) fill in the notes when only updates arrive."""
    clean = strip_markers(text or "")
    enveloped = envelope_updates(clean)
    if enveloped is not None:
        return Paste(notes=stored_notes, updates=enveloped)
    raw = _break_inline_labels(clean).splitlines()
    lines, named = _drop_request_line([ln for ln in raw if not _DATE_HEADER.match(ln)])
    found, rest = _headers(lines)
    notes, updates, flag = _labeled(rest)
    if not flag:
        notes, updates, mixed = _unlabeled(rest)
    else:
        mixed = False
    if mixed and not notes:
        mixed = False
    if not notes and updates and stored_notes:
        notes = stored_notes
    paste = Paste(notes=notes, updates=updates, mixed=mixed and bool(notes))
    return replace(paste, **{**named, **found})
