"""Pure: split pasted material into numbered sections S1..Sn, each with a short label.

Split order: explicit separator lines (---, ===, ***); then headings (markdown #..###, or a line
that is only `Name:`, `From name:` or `[name]`); with neither anywhere, blank-line paragraphs are
grouped up to PARAGRAPH_GROUP_CHARS. Fence markers are stripped from every text, so pasted text can
never close or open a data fence."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

MAX_SECTIONS = 20
MIN_SECTION_CHARS = 40
MAX_LABEL_CHARS = 60
PARAGRAPH_GROUP_CHARS = 3_000

_MARKER_RUN = re.compile(r"<{3,}|>{3,}")
_SEPARATOR = re.compile(r"^\s*(?:-{3,}|={3,}|\*{3,})\s*$")
_MD_HEADING = re.compile(r"^#{1,3}\s+(\S.*)$")
_NAME_HEADING = re.compile(r"^(?:From\s+)?([A-Za-z][\w .&'()/-]{0,58}):\s*$")
_BRACKET_HEADING = re.compile(r"^\[([^\]\n]{1,60})\]\s*$")
_NAME_HEADING_MAX_WORDS = 6
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_LABEL_NOISE = re.compile(r"[*_`#<>\[\]]+")


@dataclass(frozen=True)
class Section:
    id: str
    label: str
    text: str
    titled: bool = True  # False for the "Pasted text N" fallback label


def strip_markers(text: str) -> str:
    return _MARKER_RUN.sub("", text or "")


def split(text: str) -> tuple[Section, ...]:
    """Sections of one pasted text, numbered from S1 (renumber when appending)."""
    blocks = [b for b in _separator_blocks(strip_markers(text)) if b.strip()]
    chunks = [chunk for block in blocks for chunk in _heading_chunks(block)]
    if len(blocks) <= 1 and not any(label for label, _ in chunks):
        chunks = [(None, p) for p in _paragraph_groups(strip_markers(text))]
    return number(_cap(_merge_small(_label(chunks))))


def file_section(filename: str, text: str) -> Section:
    return Section("", _clean_label(filename) or "Attached file", strip_markers(text).strip())


def number(sections: Iterable[Section], start: int = 1) -> tuple[Section, ...]:
    return tuple(replace(s, id=f"S{n}") for n, s in enumerate(sections, start))


def append(stored: Sequence[Section], new: Sequence[Section]) -> tuple[Section, ...]:
    return (*stored, *number(new, len(stored) + 1))


def total_chars(sections: Iterable[Section]) -> int:
    return sum(len(s.text) for s in sections)


def first_that_fit(sections: Sequence[Section], limit: int) -> tuple[Section, ...]:
    """Sections in order while the running total stays within `limit`; an over-long first section is cut."""
    kept: list[Section] = []
    used = 0
    for section in sections:
        room = limit - used
        if len(section.text) > room:
            if not kept:
                kept.append(replace(section, text=section.text[:limit]))
            break
        kept.append(section)
        used += len(section.text)
    return number(kept)


def _separator_blocks(text: str) -> list[str]:
    blocks, current = [], []
    for line in text.splitlines():
        if _SEPARATOR.match(line):
            blocks.append("\n".join(current))
            current = []
        else:
            current.append(line)
    blocks.append("\n".join(current))
    return blocks


def _heading_of(line: str) -> str | None:
    for pattern in (_MD_HEADING, _BRACKET_HEADING):
        match = pattern.match(line)
        if match:
            return match.group(1)
    match = _NAME_HEADING.match(line)
    if match and len(match.group(1).split()) <= _NAME_HEADING_MAX_WORDS:
        return match.group(1)
    return None


def _heading_chunks(block: str) -> list[tuple[str | None, str]]:
    chunks: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in block.splitlines():
        heading = _heading_of(line)
        if heading is not None:
            chunks.append((heading, [line]))
        else:
            chunks[-1][1].append(line)
    return [(label, "\n".join(lines).strip()) for label, lines in chunks if "\n".join(lines).strip()]


def _paragraph_groups(text: str) -> list[str]:
    groups: list[str] = []
    for paragraph in (p.strip() for p in _PARAGRAPH_BREAK.split(text)):
        if not paragraph:
            continue
        if groups and len(groups[-1]) + len(paragraph) + 2 <= PARAGRAPH_GROUP_CHARS:
            groups[-1] = f"{groups[-1]}\n\n{paragraph}"
        else:
            groups.append(paragraph)
    return groups


def _label(chunks: Sequence[tuple[str | None, str]]) -> list[Section]:
    return [Section("", _clean_label(label) if label else "", text, bool(label and _clean_label(label)))
            for label, text in chunks]


def _merge_small(sections: list[Section]) -> list[Section]:
    """A section under MIN_SECTION_CHARS joins the next one (the last joins the previous)."""
    merged: list[Section] = []
    carry: Section | None = None
    for section in sections:
        if carry is not None:
            label, titled = (section.label, True) if section.titled else (carry.label, carry.titled)
            section = Section("", label, f"{carry.text}\n{section.text}", titled)
            carry = None
        if len(section.text) < MIN_SECTION_CHARS:
            carry = section
        else:
            merged.append(section)
    if carry is not None:
        if merged:
            last = merged.pop()
            merged.append(replace(last, text=f"{last.text}\n{carry.text}"))
        else:
            merged.append(carry)
    return merged


def _cap(sections: list[Section]) -> list[Section]:
    if len(sections) <= MAX_SECTIONS:
        return _fill_labels(sections)
    head, tail = sections[:MAX_SECTIONS - 1], sections[MAX_SECTIONS - 1:]
    joined = "\n\n".join(s.text for s in tail)
    return _fill_labels([*head, Section("", tail[0].label, joined, tail[0].titled)])


def _fill_labels(sections: list[Section]) -> list[Section]:
    out, unnamed = [], 0
    for section in sections:
        if not section.titled or not section.label:
            unnamed += 1
            section = Section("", f"Pasted text {unnamed}", section.text, False)
        out.append(section)
    return out


def _clean_label(label: str) -> str:
    clean = " ".join(_LABEL_NOISE.sub(" ", label or "").split()).strip(" :-")
    return clean[:MAX_LABEL_CHARS].rstrip()
