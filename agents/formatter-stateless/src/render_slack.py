"""Pure: the Slack Block Kit target. Output `{"blocks": [...], "text": "<plain fallback>"}`.

All data text is entity-escaped (`&`, `<`, `>`), so data can never mention anyone or disguise a link;
only whole-value http(s)/mailto URLs from the data become `<url|label>` links. Limits (header 150, section
3000, 10 fields of 2000, 50 blocks) are enforced in code; the tail beyond 50 blocks is collapsed into a
note."""

from __future__ import annotations

from typing import Any

import convert_md
import escape
import limits
from cells import NONE_MARK, WARNING, doc_heading, footer_parts, json_text, scalar_text
from model import (
    BULLETS,
    CARDS,
    FIELDS,
    JSON_BLOCK,
    PARAGRAPH,
    TABLE,
    Document,
    Json,
    Options,
    ScalarList,
    Section,
    Stats,
    header,
)

SMALL_TABLE_ROWS = 5
COLLAPSE_NOTE = "{n} more blocks not shown; ask for compact or as markdown."


def cell(value: Any) -> str:
    if isinstance(value, ScalarList):
        return ", ".join(cell(v) for v in value.items) or NONE_MARK
    if isinstance(value, Json):
        return f"`{escape.slack_entities(json_text(value.value))}`"
    if not isinstance(value, str):
        return scalar_text(value)
    if escape.safe_url(value):
        return f"<{escape.slack_entities(value)}|{escape.slack_entities(value)}>"
    return escape.slack(" ".join(value.split()) if "\n" in value else value)


def prose(text: str) -> str:
    """Multi-line data text: each line escaped (and code-spanned when it has formatting characters)."""
    return "\n".join(escape.slack(line) for line in text.strip().split("\n"))


def section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def render(docs: list[Document], opts: Options) -> tuple[dict, Stats]:
    stats = Stats()
    blocks: list[dict] = []
    first = docs[0] if docs else None
    head = opts.title or (first.lead if first and len(docs) == 1 else "") or ""
    if head:
        text = limits.cut(escape.slack_entities(" ".join(head.split())), limits.SLACK_HEADER_CHARS)
        blocks.append({"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}})
    for index, doc in enumerate(docs, 1):
        if len(docs) > 1:
            blocks += [{"type": "divider"}, section(f"*{escape.slack_entities(doc_heading(doc, index))}*")]
        blocks += _document(doc, opts, stats, with_lead=len(docs) == 1)
    blocks = _capped(blocks, stats)
    stats.blocks = len(blocks)
    fallback = (first.lead if first and first.lead else "") or opts.title or "Formatted output"
    return {"blocks": blocks, "text": escape.slack_entities(fallback)}, stats


def _document(doc: Document, opts: Options, stats: Stats, with_lead: bool) -> list[dict]:
    blocks: list[dict] = []
    if doc.needs_human:
        blocks.append(section(f"*{WARNING}*"))
    if with_lead and doc.lead:
        blocks += _sections(prose(doc.lead))
    for item in doc.sections:
        blocks.append({"type": "divider"})
        blocks += _section_blocks(item, opts, stats)
    footer = footer_parts(doc)
    if footer and opts.footer:
        text = " · ".join(escape.slack_entities(p) for p in footer)
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": text}]})
    return blocks


def _sections(text: str) -> list[dict]:
    return [section(chunk) for chunk in limits.split_text(text, limits.SLACK_SECTION_CHARS)]


def _section_blocks(item: Section, opts: Options, stats: Stats) -> list[dict]:
    title = f"*{escape.slack_entities(item.title)}*"
    if item.kind == TABLE and not opts.compact and len(item.rows) <= SMALL_TABLE_ROWS \
            and len(item.columns) <= limits.SLACK_FIELDS_PER_BLOCK:
        return [section(title), *(_fields_block(item.columns, row, stats) for row in item.rows)]
    return _sections(f"{title}\n{_body(item, opts)}")


def _fields_block(columns: tuple, row: tuple, stats: Stats) -> dict:
    fields = []
    for key, value in zip(columns, row):
        text = f"*{escape.slack_entities(header(key))}*\n{cell(value)}"
        if len(text) > limits.SLACK_FIELD_CHARS:
            stats.cut += 1
            stats.excluded.append(escape.slack_unescape(text))
            text = limits.cut(text, limits.SLACK_FIELD_CHARS)
        fields.append({"type": "mrkdwn", "text": text})
    return {"type": "section", "fields": fields}


def _body(item: Section, opts: Options) -> str:
    if item.kind == PARAGRAPH:
        if not isinstance(item.text, str):
            return cell(item.text)
        if item.markdown:
            return convert_md.to_mrkdwn(item.text)
        return prose(item.text)
    if item.kind == BULLETS:
        return "\n".join(f"• {cell(v)}" for v in item.items) or "—"
    if item.kind == FIELDS:
        return "\n".join(f"*{escape.slack_entities(header(k))}:* {cell(v)}" for k, v in item.lines)
    if item.kind == CARDS:
        return "\n".join(_card(n, card) for n, card in enumerate(item.cards, 1))
    if item.kind == JSON_BLOCK:
        return "```\n" + escape.slack_entities(json_text(item.value, pretty=True)) + "\n```"
    head = " — ".join(f"*{escape.slack_entities(header(c))}*" for c in item.columns)
    rows = [f"*{n}.* " + " — ".join(cell(c) for c in row) for n, row in enumerate(item.rows, 1)]
    return "\n".join([head, *rows])


def _card(n: int, card) -> str:
    title = f"*{n}. {cell(card.title)}*" if card.title is not None else f"*{n}.*"
    lines = [f"    {escape.slack_entities(header(k))}: {cell(v)}" for k, v in card.lines]
    return "\n".join([title, *lines])


def _capped(blocks: list[dict], stats: Stats) -> list[dict]:
    if len(blocks) <= limits.SLACK_MAX_BLOCKS:
        return blocks
    keep = blocks[: limits.SLACK_MAX_BLOCKS - 1]
    dropped = blocks[limits.SLACK_MAX_BLOCKS - 1:]
    stats.collapsed = sum(1 for b in dropped if b["type"] != "divider")
    stats.excluded += [escape.slack_unescape(t) for t in block_texts(dropped)]
    note = COLLAPSE_NOTE.format(n=stats.collapsed)
    return [*keep, {"type": "context", "elements": [{"type": "mrkdwn", "text": note}]}]


def block_texts(blocks: list[dict]) -> list[str]:
    """Every text string in a list of blocks (header, section text and fields, context elements)."""
    texts: list[str] = []
    for block in blocks:
        if "text" in block:
            texts.append(block["text"]["text"])
        texts += [f["text"] for f in block.get("fields", ())]
        texts += [e["text"] for e in block.get("elements", ()) if "text" in e]
    return texts
