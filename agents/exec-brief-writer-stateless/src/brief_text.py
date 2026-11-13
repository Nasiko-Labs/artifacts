"""Pure: read a brief back from its rendered chat text (render.py), since this agent stores nothing.

What comes back: the three parts with their section tags (each tag becomes a cite with no snippet, or the
quoted snippet when the reply was rendered with sources), the notes, and the "Sources:" key as label-only
sections. A reply that does not have the rendered shape (cut off, compressed, anything else) reads as None.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import render
from brief import Brief, Cite, Line
from sections import Section

_HEADLINE = re.compile(r"^\*\*Headline:\*\* (.*)$")
_NUMBERED = re.compile(r"^(\d+)\. (.*)$")
_RISK = re.compile(r"^\((High|Medium|Low)\) (.*)$", re.IGNORECASE)
_TAGS = re.compile(r"\s*((?:\[S\d+\])+)$")
_TAG = re.compile(r"\[(S\d+)\]")
_QUOTE = re.compile(r'^\s+> "(.*)" \((S\d+)\)$')
_OWNER = re.compile(r"^(.*) — (?:owner: (.*?)|" + re.escape(render.NO_OWNER) + r")(?: · by: (.*))?$")
_KEY_ITEM = re.compile(r"^(S\d+) (.*)$")
_NOTE = re.compile(r"^_(.+)_$")
_PARTS = ("**What's at risk**", "**Decisions needed**")


@dataclass(frozen=True)
class Shown:
    brief: Brief
    key: tuple[Section, ...]      # label-only sections from the "Sources:" line
    key_line: str                 # the "Sources:" line as shown


def looks_like_brief(text: str) -> bool:
    return any(p in (text or "") for p in (*_PARTS, "**Headline:**", render.NOTHING_FLAGGED, render.NOTHING_TRACED))


def _split_tags(text: str) -> tuple[str, tuple[Cite, ...]]:
    match = _TAGS.search(text)
    if not match:
        return text.strip(), ()
    return text[:match.start()].strip(), tuple(Cite(s, "") for s in _TAG.findall(match.group(1)))


def _risk(text: str) -> Line | None:
    match = _RISK.match(text)
    if not match:
        return None
    body, cites = _split_tags(match.group(2))
    return Line(body, cites, severity=match.group(1).lower())


def _decision(text: str) -> Line | None:
    body, cites = _split_tags(text)
    match = _OWNER.match(body)
    if not match:
        return None
    return Line(match.group(1).strip(), cites, owner=(match.group(2) or "").strip(), by=(match.group(3) or "").strip())


def _with_quote(line: Line, snippet: str, section: str) -> Line:
    """A quoted snippet replaces the first empty cite of its section (or is added)."""
    cites = list(line.cites)
    for i, cite in enumerate(cites):
        if cite.section == section and not cite.snippet:
            cites[i] = Cite(section, snippet)
            return replace(line, cites=tuple(cites))
    return replace(line, cites=(*cites, Cite(section, snippet)))


def _key(line: str) -> tuple[Section, ...]:
    items = line.removeprefix("Sources: ").split(" · ")
    matches = [_KEY_ITEM.match(i.strip()) for i in items]
    return tuple(Section(m.group(1), m.group(2), "") for m in matches if m)


def read(text: str) -> Shown | None:
    headline: Line | None = None
    rows: dict[str, list[Line]] = {"risk": [], "decision": []}
    part, last, key_line, notes = "", None, "", []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        quote = _QUOTE.match(line)
        if quote and last is not None:
            updated = _with_quote(last[1], quote.group(1), quote.group(2))
            if last[0] == "headline":
                headline = updated
            else:
                rows[last[0]][-1] = updated
            last = (last[0], updated)
            continue
        if line in _PARTS:
            part, last = ("risk" if line == _PARTS[0] else "decision"), None
            continue
        match = _HEADLINE.match(line)
        if match:
            body, cites = _split_tags(match.group(1))
            headline, last = Line(body, cites), ("headline", Line(body, cites))
            continue
        if line.startswith("Sources: "):
            key_line, part, last = line, "", None
            continue
        note = _NOTE.match(line)
        if note and key_line:
            notes.append(note.group(1))
            continue
        numbered = _NUMBERED.match(line)
        if numbered and part:
            parsed = _risk(numbered.group(2)) if part == "risk" else _decision(numbered.group(2))
            if parsed is None or int(numbered.group(1)) != len(rows[part]) + 1:
                return None
            rows[part].append(parsed)
            last = (part, parsed)
    if not key_line or (headline is None and not rows["risk"] and not rows["decision"]):
        return None
    status = "ok" if rows["risk"] or rows["decision"] else "nothing_to_brief"
    brief = Brief(status, headline, tuple(rows["risk"]), tuple(rows["decision"]), tuple(notes))
    return Shown(brief, _key(key_line), key_line)
