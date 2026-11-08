"""Pure: the Markdown target (and, with `plain=True`, the unformatted text used as the email's plain-text
alternative). GitHub tables with escaped cells; cells over 300 chars are cut (counted, and kept whole in
compact mode, which uses bullets instead of tables)."""

from __future__ import annotations

from typing import Any

import escape
import md_blocks
from cells import NONE_MARK, WARNING, doc_heading, footer_parts, json_text, scalar_text
from limits import MD_CELL_CHARS, cut
from model import (
    BULLETS,
    CARDS,
    FIELDS,
    JSON_BLOCK,
    PARAGRAPH,
    Document,
    Json,
    Options,
    ScalarList,
    Section,
    Stats,
    header,
)


class _Style:
    def __init__(self, plain: bool, stats: Stats):
        self.plain, self.stats = plain, stats

    def bold(self, text: str) -> str:
        return text if self.plain else f"**{text}**"

    def esc(self, text: str) -> str:
        return text if self.plain else escape.md_inline(text)

    def heading(self, text: str, level: int) -> str:
        if self.plain:
            return f"{text}\n{'-' * min(len(text), 60)}"
        return f"{'#' * level} {escape.md_inline(text)}"

    def cell(self, value: Any, limit: int | None = None, in_table: bool = False) -> str:
        if isinstance(value, ScalarList):
            return ", ".join(self.cell(v, limit, in_table) for v in value.items) or NONE_MARK
        if isinstance(value, Json):
            text = self._limited(json_text(value.value), limit)
            return text if self.plain else escape.md_code(text, in_table)
        if not isinstance(value, str):
            return scalar_text(value)
        text = self._limited(value, limit)
        if not self.plain and escape.safe_url(text):
            return f"<{text}>"
        return " ".join(text.split()) if self.plain else escape.md_inline(text)

    def _limited(self, text: str, limit: int | None) -> str:
        if limit is None or len(text) <= limit:
            return text
        self.stats.cut += 1
        self.stats.excluded.append(text)
        return cut(text, limit)


def render(docs: list[Document], opts: Options, plain: bool = False) -> tuple[str, Stats]:
    stats = Stats()
    style = _Style(plain, stats)
    compact = opts.compact or plain
    parts: list[str] = [style.heading(opts.title, 1)] if opts.title else []
    for index, doc in enumerate(docs, 1):
        if len(docs) > 1:
            if index > 1:
                parts.append("---" if not plain else "-" * 20)
            parts.append(style.heading(doc_heading(doc, index), 2))
        parts += _document(doc, style, opts, compact, level=3 if len(docs) > 1 else 2, with_lead=len(docs) == 1)
    return "\n\n".join(p for p in parts if p), stats


def _document(doc: Document, style: _Style, opts: Options, compact: bool, level: int, with_lead: bool) -> list[str]:
    parts = [style.bold(WARNING)] if doc.needs_human else []
    if with_lead and doc.lead:
        parts.append(_paragraph(doc.lead, style))
    parts += [_section(section, style, compact, level) for section in doc.sections]
    footer = footer_parts(doc)
    if footer and opts.footer:
        line = " · ".join(style.esc(p) for p in footer)
        parts.append(line if style.plain else f"_{line}_")
    return parts


def _paragraph(text: str, style: _Style) -> str:
    if style.plain:
        return text.strip()
    return "  \n".join(escape.md(line) for line in text.strip().split("\n"))


def _section(section: Section, style: _Style, compact: bool, level: int) -> str:
    return f"{style.heading(section.title, level)}\n\n{_body(section, style, compact)}"


def _body(section: Section, style: _Style, compact: bool) -> str:
    if section.kind == PARAGRAPH:
        if not isinstance(section.text, str):
            return style.cell(section.text)
        if section.markdown:
            source = md_blocks.sanitize(section.text)
            return md_blocks.plain_words_source(source).strip() if style.plain else source.strip()
        return _paragraph(section.text, style)
    if section.kind == BULLETS:
        return "\n".join(f"- {style.cell(item)}" for item in section.items) or "—"
    if section.kind == FIELDS:
        return "\n".join(f"- {style.bold(style.esc(header(k)) + ':')} {style.cell(v)}" for k, v in section.lines)
    if section.kind == CARDS:
        return "\n".join(_card(n, card, style) for n, card in enumerate(section.cards, 1))
    if section.kind == JSON_BLOCK:
        text = json_text(section.value, pretty=True)
        return text if style.plain else f"```json\n{text}\n```"
    return _compact_table(section, style) if compact else _table(section, style)


def _card(n: int, card, style: _Style) -> str:
    lines = [f"   - {style.bold(style.esc(header(k)) + ':')} {style.cell(v)}" for k, v in card.lines]
    title = style.bold(style.cell(card.title)) if card.title is not None else ""
    if not title and lines:
        title, lines = lines[0].strip()[2:], lines[1:]
    return "\n".join([f"{n}. {title}".rstrip(), *lines])


def _table(section: Section, style: _Style) -> str:
    head = "| " + " | ".join(style.esc(header(c)) for c in section.columns) + " |"
    rule = "| " + " | ".join("---" for _ in section.columns) + " |"
    rows = ["| " + " | ".join(style.cell(c, MD_CELL_CHARS, in_table=True) for c in row) + " |" for row in section.rows]
    return "\n".join([head, rule, *rows])


def _compact_table(section: Section, style: _Style) -> str:
    lines = []
    for n, row in enumerate(section.rows, 1):
        pairs = [f"{style.bold(style.esc(header(k)) + ':')} {style.cell(v)}" for k, v in zip(section.columns, row)]
        lines.append(f"{n}. " + " · ".join(pairs))
    return "\n".join(lines)
