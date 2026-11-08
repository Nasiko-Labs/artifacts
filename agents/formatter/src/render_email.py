"""Pure: the email HTML target. One table-based, inline-styled body (600 px container, system fonts, no
<style>, no images, scripts, iframes, forms or event attributes). Every data string goes through
`html.escape(quote=True)`; only whole-value http(s)/mailto URLs from the data become links."""

from __future__ import annotations

from typing import Any

import convert_md
import escape
from cells import NONE_MARK, WARNING, doc_heading, footer_parts, json_text, scalar_text
from convert_md import CODE_STYLE, LINK_STYLE, P_STYLE, PRE_STYLE, table_html
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

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
H1_STYLE = "margin:0 0 12px;font-size:22px;line-height:1.3;"
H2_STYLE = "margin:20px 0 8px;font-size:18px;line-height:1.3;"
WARN_STYLE = "margin:0 0 12px;padding:8px 12px;background:#fff8c5;border:1px solid #d4a72c;"
FOOTER_STYLE = "margin:20px 0 0;color:#6e7781;font-size:12px;"
LIST_STYLE = "margin:0 0 12px;padding-left:24px;"


def cell(value: Any) -> str:
    if isinstance(value, ScalarList):
        return ", ".join(cell(v) for v in value.items) or NONE_MARK
    if isinstance(value, Json):
        return f'<code style="{CODE_STYLE}">{escape.html_text(json_text(value.value))}</code>'
    if not isinstance(value, str):
        return escape.html_text(scalar_text(value))
    if escape.safe_url(value):
        url = escape.html_text(value)
        return f'<a href="{url}" rel="noopener" style="{LINK_STYLE}">{url}</a>'
    return "<br>".join(escape.html_text(line) for line in value.split("\n"))


def render(docs: list[Document], opts: Options) -> tuple[str, Stats]:
    body: list[str] = [f'<h1 style="{H1_STYLE}">{escape.html_text(opts.title)}</h1>'] if opts.title else []
    for index, doc in enumerate(docs, 1):
        if len(docs) > 1:
            if index > 1:
                body.append('<hr style="border:none;border-top:1px solid #d0d7de;margin:20px 0;">')
            body.append(f'<h2 style="{H2_STYLE}">{escape.html_text(doc_heading(doc, index))}</h2>')
        body += _document(doc, opts, with_lead=len(docs) == 1)
    return page("\n".join(body)), Stats()


def page(inner: str) -> str:
    """The 600 px, table-based, inline-styled container around the body markup."""
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;">'
            f'<tr><td align="center">\n'
            f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
            f'style="max-width:600px;width:100%;font-family:{FONT};font-size:15px;color:#1f2328;">'
            f'<tr><td style="padding:16px 24px;">\n{inner}\n</td></tr></table>\n</td></tr></table>')


def _document(doc: Document, opts: Options, with_lead: bool) -> list[str]:
    parts = [f'<p style="{WARN_STYLE}"><strong>{WARNING}</strong></p>'] if doc.needs_human else []
    if with_lead and doc.lead:
        parts.append(f'<p style="{P_STYLE}">{cell(doc.lead.strip())}</p>')
    parts += [_section(section) for section in doc.sections]
    footer = footer_parts(doc)
    if footer and opts.footer:
        parts.append(f'<p style="{FOOTER_STYLE}">' + " · ".join(escape.html_text(p) for p in footer) + "</p>")
    return parts


def _section(section: Section) -> str:
    return f'<h2 style="{H2_STYLE}">{escape.html_text(section.title)}</h2>\n{_body(section)}'


def _label(key: str) -> str:
    return f"<strong>{escape.html_text(header(key))}:</strong>"


def _body(section: Section) -> str:
    if section.kind == PARAGRAPH:
        if isinstance(section.text, str) and section.markdown:
            return convert_md.to_html(section.text)
        return f'<p style="{P_STYLE}">{cell(section.text)}</p>'
    if section.kind == BULLETS:
        items = "".join(f"<li>{cell(v)}</li>" for v in section.items) or "<li>—</li>"
        return f'<ul style="{LIST_STYLE}">{items}</ul>'
    if section.kind == FIELDS:
        items = "".join(f"<li>{_label(k)} {cell(v)}</li>" for k, v in section.lines)
        return f'<ul style="{LIST_STYLE}">{items}</ul>'
    if section.kind == CARDS:
        return f'<ol style="{LIST_STYLE}">' + "".join(_card(card) for card in section.cards) + "</ol>"
    if section.kind == JSON_BLOCK:
        return f'<pre style="{PRE_STYLE}">{escape.html_text(json_text(section.value, pretty=True))}</pre>'
    head = [escape.html_text(header(c)) for c in section.columns]
    rows = [[cell(c) for c in row] for row in section.rows]
    return table_html(head, rows, lambda markup: markup)


def _card(card) -> str:
    title = f"<strong>{cell(card.title)}</strong>" if card.title is not None else ""
    lines = "".join(f"<li>{_label(k)} {cell(v)}</li>" for k, v in card.lines)
    return f'<li style="margin:0 0 8px;">{title}<ul style="{LIST_STYLE}">{lines}</ul></li>'
