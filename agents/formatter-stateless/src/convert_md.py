"""Pure: Markdown -> Slack mrkdwn and Markdown -> email HTML, both in code.

Slack: `**b**` -> `*b*`, `*i*` -> `_i_`, `# H` -> `*H*`, `[t](u)` -> `<u|t>` (http/https/mailto only),
lists -> `•`, tables -> an aligned code block. HTML: inline-styled elements only. Images and raw HTML
are removed first (md_blocks.sanitize); every text run is escaped for its target."""

from __future__ import annotations

import escape
import md_blocks
from md_blocks import INLINE, Block

FONT_MONO = "font-family:SFMono-Regular,Menlo,Consolas,monospace;"
CODE_STYLE = f"{FONT_MONO}background:#f6f8fa;padding:1px 4px;border-radius:3px;"
PRE_STYLE = f"{FONT_MONO}background:#f6f8fa;padding:8px 12px;margin:0 0 12px;white-space:pre-wrap;font-size:13px;"
LINK_STYLE = "color:#0969da;"
CELL_STYLE = "border:1px solid #d0d7de;padding:6px 8px;text-align:left;vertical-align:top;"
P_STYLE = "margin:0 0 12px;line-height:1.5;"


# --- inline ---------------------------------------------------------------------------------

def _inline(text: str, target: str) -> str:
    out: list[str] = []
    pos = 0
    for m in INLINE.finditer(text):
        out.append(_text(text[pos:m.start()], target))
        out.append(_token(m, target))
        pos = m.end()
    out.append(_text(text[pos:], target))
    return "".join(out)


def _text(text: str, target: str) -> str:
    return escape.slack_entities(text) if target == "slack" else escape.html_text(text)


def _token(m, target: str) -> str:
    slack = target == "slack"
    if m.group("code"):
        body = _text(m.group("ctext"), target)
        return f"`{body}`" if slack else f'<code style="{CODE_STYLE}">{body}</code>'
    if m.group("label") is not None:
        return _link(m.group("url"), _inline(m.group("label"), target), target)
    if m.group("auto"):
        return _link(m.group("auto"), _text(m.group("auto"), target), target)
    for name, slack_mark, tag in (("b1", "*", "strong"), ("b2", "*", "strong"), ("s", "~", "s"),
                                  ("i1", "_", "em"), ("i2", "_", "em")):
        if m.group(name) is not None:
            inner = _inline(m.group(name), target)
            return f"{slack_mark}{inner}{slack_mark}" if slack else f"<{tag}>{inner}</{tag}>"
    return _text(m.group(0), target)


def _link(url: str, label: str, target: str) -> str:
    if not escape.safe_url(url):
        return label  # an unsafe scheme is never linked; its text stays
    if target == "slack":
        return f"<{escape.slack_entities(url)}|{label}>"
    return f'<a href="{escape.html_text(url)}" rel="noopener" style="{LINK_STYLE}">{label}</a>'


# --- Slack ----------------------------------------------------------------------------------

def to_mrkdwn(markdown: str) -> str:
    return "\n\n".join(_mrkdwn_block(b) for b in md_blocks.parse(md_blocks.sanitize(markdown)))


def _mrkdwn_block(block: Block) -> str:
    if block.kind == "heading":
        return f"*{_inline(block.lines[0], 'slack')}*"
    if block.kind == "ul":
        return "\n".join(f"• {_inline(x, 'slack')}" for x in block.lines)
    if block.kind == "ol":
        return "\n".join(f"{n}. {_inline(x, 'slack')}" for n, x in enumerate(block.lines, 1))
    if block.kind == "quote":
        return "\n".join(f"> {_inline(x, 'slack')}" for x in block.lines)
    if block.kind == "code":
        return "```\n" + escape.slack_entities("\n".join(block.lines)) + "\n```"
    if block.kind == "table":
        return "```\n" + escape.slack_entities(aligned(block.rows)) + "\n```"
    if block.kind == "rule":
        return "———"
    return "\n".join(_inline(x, "slack") for x in block.lines)


def aligned(rows: tuple[tuple[str, ...], ...]) -> str:
    width = max(len(r) for r in rows)
    padded = [tuple(r) + ("",) * (width - len(r)) for r in rows]
    sizes = [max(len(r[c]) for r in padded) for c in range(width)]
    lines = [" | ".join(cell.ljust(sizes[c]) for c, cell in enumerate(r)).rstrip() for r in padded]
    return "\n".join([lines[0], "-+-".join("-" * s for s in sizes), *lines[1:]])


# --- HTML -----------------------------------------------------------------------------------

def to_html(markdown: str) -> str:
    return "\n".join(_html_block(b) for b in md_blocks.parse(md_blocks.sanitize(markdown)))


def _html_block(block: Block) -> str:
    if block.kind == "heading":
        size = {1: 22, 2: 18, 3: 16}.get(block.level, 15)
        return f'<h3 style="margin:16px 0 8px;font-size:{size}px;">{_inline(block.lines[0], "html")}</h3>'
    if block.kind in ("ul", "ol"):
        items = "".join(f'<li style="margin:0 0 4px;">{_inline(x, "html")}</li>' for x in block.lines)
        return f'<{block.kind} style="margin:0 0 12px;padding-left:24px;">{items}</{block.kind}>'
    if block.kind == "quote":
        body = "<br>".join(_inline(x, "html") for x in block.lines)
        return f'<blockquote style="margin:0 0 12px;padding-left:12px;border-left:3px solid #d0d7de;color:#57606a;">{body}</blockquote>'
    if block.kind == "code":
        return f'<pre style="{PRE_STYLE}">{escape.html_text(chr(10).join(block.lines))}</pre>'
    if block.kind == "table":
        return table_html(block.rows[0], block.rows[1:], lambda cell: _inline(cell, "html"))
    if block.kind == "rule":
        return '<hr style="border:none;border-top:1px solid #d0d7de;margin:16px 0;">'
    return f'<p style="{P_STYLE}">' + "<br>".join(_inline(x, "html") for x in block.lines) + "</p>"


def table_html(head, rows, render_cell) -> str:
    """A bordered table; `head` is already-escaped header text or raw text passed through render_cell."""
    header = "".join(f'<th style="{CELL_STYLE}background:#f6f8fa;">{render_cell(h)}</th>' for h in head)
    body = "".join("<tr>" + "".join(f'<td style="{CELL_STYLE}">{render_cell(c)}</td>' for c in row) + "</tr>"
                   for row in rows)
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;width:100%;margin:0 0 12px;font-size:14px;">'
            f"<tr>{header}</tr>{body}</table>")
