"""Pure: split one pasted message into the research question and its sources.

Separators, tried in order (the first style present wins): `--- Source: <label> ---`, `=== label ===`,
`[S1] label`, `### label`, `Source N: label`; else 3+ blank lines; else the text is one source. Sources get
ids S1..Sn in order and a label (given, else the first 6 words). Optional header lines `Date:`, `Author:`,
`Type:` at the top of a source are parsed and removed from its text. A URL on its own line is only a label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime

import prompts
from model import SOURCE_TYPES, Source

QUESTION_MAX_CHARS = 300
LABEL_WORDS = 6
LABEL_MAX_CHARS = 120

_STYLES = (
    re.compile(r"^[ \t]*-{3,}[ \t]*source[ \t]*:[ \t]*(.*?)[ \t]*-{3,}[ \t]*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[ \t]*={3,}[ \t]*(.+?)[ \t]*={3,}[ \t]*$", re.MULTILINE),
    re.compile(r"^[ \t]*\[S\d{1,3}\][ \t]*(.*)$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^###[ \t]+(.+)$", re.MULTILINE),
    re.compile(r"^[ \t]*source[ \t]+\d{1,3}[ \t]*:[ \t]*(.*)$", re.IGNORECASE | re.MULTILINE),
)
_BLANK_SPLIT = re.compile(r"\n[ \t]*\n[ \t]*\n[ \t]*\n\s*")
_PARAGRAPH = re.compile(r"\n\s*\n")
_QUESTION_LINE = re.compile(r"^[ \t]*(?:question|q|research question)[ \t]*:[ \t]*(.+)$", re.IGNORECASE | re.MULTILINE)
_HEADER = re.compile(r"^[ \t]*(date|author|type)[ \t]*:[ \t]*(.*?)[ \t]*$", re.IGNORECASE)
_URL_LINE = re.compile(r"^[ \t]*<?(https?://\S+?)>?[ \t]*$", re.MULTILINE)
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d %B %Y", "%B %d, %Y", "%B %d %Y", "%d %b %Y", "%b %d, %Y", "%Y-%m",
                 "%B %Y", "%b %Y")


@dataclass(frozen=True)
class Parsed:
    question: str
    sources: tuple[Source, ...]
    has_links: bool = False
    has_headers: bool = False      # sources were marked explicitly (a separator style, or a named file)


def parse(text: str, files: tuple[tuple[str, str], ...] = (), first_id: int = 1) -> Parsed:
    clean = prompts.strip_markers(text or "").replace("\r\n", "\n").strip()
    question, clean = _question_line(clean)
    pieces, preamble, marked = _split(clean)
    if not question and preamble and len(preamble) <= QUESTION_MAX_CHARS:
        question, preamble = " ".join(preamble.split()), ""
    raw = ([("", preamble)] if preamble else []) + pieces
    raw += [(name, prompts.strip_markers(body)) for name, body in files if body.strip()]
    sources = []
    for label, body in raw:
        source = _source(f"S{first_id + len(sources)}", label, body)
        if source is not None:
            sources.append(source)
    has_links = bool(_URL_LINE.search(clean))
    return Parsed(question=question[:QUESTION_MAX_CHARS], sources=tuple(sources), has_links=has_links,
                  has_headers=marked or any(body.strip() for _, body in files))


def renumber(sources: tuple[Source, ...], first_id: int) -> tuple[Source, ...]:
    return tuple(replace(s, id=f"S{first_id + n}") for n, s in enumerate(sources))


def _question_line(text: str) -> tuple[str, str]:
    """A `Question:` / `Q:` line in the first paragraph (an interview's own "Q:" lines further down stay)."""
    first = _PARAGRAPH.search(text)
    heads = [m.start() for m in (style.search(text) for style in _STYLES) if m]
    match = _QUESTION_LINE.search(text, 0, min([first.start() if first else len(text), *heads]))
    if not match:
        return "", text
    return " ".join(match.group(1).split()), (text[:match.start()] + text[match.end():]).strip()


def _split(text: str) -> tuple[list[tuple[str, str]], str, bool]:
    """([(label, body)], preamble before the first separator, whether separators were found)."""
    for style in _STYLES:
        heads = list(style.finditer(text))
        if heads:
            pieces = [(m.group(1).strip(), text[m.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)])
                      for i, m in enumerate(heads)]
            return pieces, text[:heads[0].start()].strip(), True
    head, rest = _leading_paragraph(text)
    blocks = [b for b in _BLANK_SPLIT.split(rest) if b.strip()]
    return [("", b) for b in blocks], head, False


def _leading_paragraph(text: str) -> tuple[str, str]:
    """A short first paragraph before the rest is the question (when nothing marks the sources)."""
    match = _PARAGRAPH.search(text)
    if match:
        head, rest = text[:match.start()].strip(), text[match.end():].strip()
        if len(head) <= QUESTION_MAX_CHARS and rest and (head.endswith("?") or len(rest) > 2 * len(head)):
            return head, rest
    return "", text


def _source(source_id: str, label: str, body: str) -> Source | None:
    headers, text = _headers(body.strip("\n"))
    text = text.strip()
    url = _URL_LINE.search(text)
    if not text or (url and not _URL_LINE.sub("", text).strip()):
        return None  # empty, or only a link: nothing to read
    name = label or (url.group(1) if url else " ".join(text.split()[:LABEL_WORDS]))
    kind = headers.get("type", "").casefold()
    return Source(id=source_id, label=" ".join(name.split())[:LABEL_MAX_CHARS], text=text,
                  type=kind if kind in SOURCE_TYPES else "", date=parse_date(headers.get("date", "")),
                  author=headers.get("author", ""), words=len(text.split()))


def _headers(body: str) -> tuple[dict[str, str], str]:
    lines = body.split("\n")
    found: dict[str, str] = {}
    i = 0
    while i < len(lines):
        if not lines[i].strip() and not found:
            i += 1
            continue
        match = _HEADER.match(lines[i])
        if not match or match.group(1).casefold() in found:
            break
        found[match.group(1).casefold()] = match.group(2)
        i += 1
    return found, "\n".join(lines[i:])


def parse_date(raw: str) -> str:
    """An ISO date for a stated date (a month alone becomes its first day), else ""."""
    text = " ".join((raw or "").replace(",", ", ").split()).replace(" ,", ",")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def months_between(older: str, now: date) -> int:
    then = date.fromisoformat(older)
    return (now.year - then.year) * 12 + now.month - then.month
