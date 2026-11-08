"""Pure text helpers for page facts: the raw HTML of a tag (verbatim evidence), visible text, question
headings, citable facts, and the verbatim test every quote must pass."""

from __future__ import annotations

import html as htmllib
import re

from bs4 import BeautifulSoup, Tag

EVIDENCE_MAX_CHARS = 160
HIDDEN_TAGS = ("script", "style", "noscript", "template", "svg", "head", "nav", "footer")
_QUESTION_START = re.compile(r"^(?:how|what|why|when|where|who|which|can|does|do|is|are|should|will)\b",
                             re.IGNORECASE)
_FACT = re.compile(
    r"[$€£]\s?\d[\d,.]*\d?"
    r"|\b\d[\d,.]*\s?(?:%|percent|million|billion|users|customers|teams|hours|days|minutes|seconds|ms|countries)"
    r"|\b(?:19|20)\d{2}\b"
    r"|[Aa]ccording to (?:the )?[A-Z][\w&.-]*(?: [A-Z][\w&.-]*)*")
_SPACES = re.compile(r"\s+")


def raw_tag(html: str, tag: Tag) -> str:
    """The tag exactly as written in the HTML: the whole element when it is short, else its start tag."""
    line, col = getattr(tag, "sourceline", None), getattr(tag, "sourcepos", None)
    if line is None or col is None:
        return ""
    offset = _line_offset(html, line) + col
    end = html.find(">", offset)
    raw = html[offset:end + 1] if end != -1 else html[offset:offset + EVIDENCE_MAX_CHARS]
    close = html.casefold().find(f"</{tag.name}>", offset, offset + EVIDENCE_MAX_CHARS)
    if close != -1:  # a short element is quoted whole: <title>...</title>
        raw = html[offset:close + len(tag.name) + 3]
    return raw[:EVIDENCE_MAX_CHARS]


def _line_offset(html: str, line: int) -> int:
    offset = 0
    for _ in range(line - 1):
        offset = html.index("\n", offset) + 1
    return offset


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(HIDDEN_TAGS):
        tag.decompose()
    return _SPACES.sub(" ", soup.get_text(" ")).strip()


def is_question(text: str) -> bool:
    stripped = text.strip()
    return stripped.endswith("?") or bool(_QUESTION_START.match(stripped))


def citable_facts(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).strip() for m in _FACT.finditer(text or "")))[:20]


def normalise(text: str) -> str:
    return _SPACES.sub(" ", htmllib.unescape(text or "")).strip().casefold()


def verbatim(excerpt: str, *sources: str) -> bool:
    """True when the excerpt appears in one of the sources (entity-decoded, whitespace and case folded)."""
    needle = normalise(excerpt)
    return bool(needle) and any(needle in normalise(source) for source in sources)
