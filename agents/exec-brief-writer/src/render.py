"""Pure: the brief as chat text. Exactly three parts (headline, what's at risk, decisions needed),
inline [S#] tags, one "Sources:" key, then any notes. Every model-written string is cleaned first:
no markdown image, link, HTML tag, bare URL or fence marker survives."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence

from brief import Brief, Line
from sections import Section, strip_markers

NONE_FLAGGED = "None flagged in the inputs."
NOTHING_FLAGGED = "Nothing in these outputs is flagged as a risk or waiting on a decision."
NOTHING_TRACED = "I couldn't trace any risk or decision to your pasted text, so there is no brief to show."
NO_OWNER = "owner not stated"
MAX_SNIPPET_SHOWN = 200

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML = re.compile(r"<[^>\n]{0,200}>")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_TAG = re.compile(r"\[\s*S\d+\s*\]", re.IGNORECASE)
_SPACE = re.compile(r"\s+")


def clean(text: str) -> str:
    text = _IMAGE.sub(" ", strip_markers(text or ""))
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("(link removed)", _HTML.sub(" ", text))
    text = _TAG.sub(" ", text).replace("**", "").replace("`", "'")
    return _SPACE.sub(" ", text).strip()


def _tags(line: Line) -> str:
    seen: list[str] = []
    for cite in line.cites:
        if cite.section not in seen:
            seen.append(cite.section)
    return "".join(f"[{s}]" for s in seen)


def _with_tags(text: str, line: Line, tags: bool) -> str:
    return f"{text} {_tags(line)}" if tags and line.cites else text


def _quotes(line: Line) -> list[str]:
    return [f'   > "{clean(c.snippet)[:MAX_SNIPPET_SHOWN]}" ({c.section})' for c in line.cites]


def _decision_text(line: Line) -> str:
    owner = f"owner: {clean(line.owner)}" if line.owner else NO_OWNER
    by = f" · by: {clean(line.by)}" if line.by else ""
    return f"{clean(line.text)} — {owner}{by}"


def _numbered(lines: Sequence[Line], word: Callable[[Line], str], tags: bool, sources: bool) -> list[str]:
    if not lines:
        return [NONE_FLAGGED]
    out = []
    for n, line in enumerate(lines, 1):
        out.append(f"{n}. {_with_tags(word(line), line, tags)}")
        if sources:
            out.extend(_quotes(line))
    return out


def body(brief: Brief, *, tags: bool = True, sources: bool = False) -> str:
    """The three parts only (no source key, no notes)."""
    headline = brief.headline
    parts = []
    if headline is not None:
        parts.append(f"**Headline:** {_with_tags(clean(headline.text), headline, tags)}")
        if sources:
            parts.extend(_quotes(headline))
    risks = _numbered(brief.risks, lambda r: f"({(r.severity or 'medium').capitalize()}) {clean(r.text)}", tags, sources)
    decisions = _numbered(brief.decisions, _decision_text, tags, sources)
    return "\n".join([*parts, "", "**What's at risk**", *risks, "", "**Decisions needed**", *decisions]).strip()


def body_words(brief: Brief) -> int:
    return len(body(brief, tags=False).replace("**", "").split())


def source_key(sections: Iterable[Section]) -> str:
    return "Sources: " + " · ".join(f"{s.id} {clean(s.label)}" for s in sections)


def render(brief: Brief, sections: Sequence[Section], *, sources: bool = False,
           extra_notes: Sequence[str] = ()) -> str:
    if brief.status == "nothing_to_brief" or not (brief.risks or brief.decisions):
        return _nothing(brief, sections, extra_notes)
    blocks = [body(brief, sources=sources), source_key(sections)]
    notes = [*brief.notes, *extra_notes]
    if notes:
        blocks.append("\n".join(f"_{n}_" for n in notes))
    return "\n\n".join(blocks)


def _nothing(brief: Brief, sections: Sequence[Section], extra_notes: Sequence[str]) -> str:
    blocks = [NOTHING_TRACED if brief.status != "nothing_to_brief" and brief.notes else NOTHING_FLAGGED]
    if brief.headline is not None:
        blocks.append(f"**Headline:** {_with_tags(clean(brief.headline.text), brief.headline, True)}")
        blocks.append(source_key(sections))
    notes = [*brief.notes, *extra_notes]
    if notes:
        blocks.append("\n".join(f"_{n}_" for n in notes))
    return "\n\n".join(blocks)
