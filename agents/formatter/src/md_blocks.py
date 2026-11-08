"""Pure: a small Markdown reader (the subset agents and people write: headings, paragraphs, lists,
code fences, blockquotes, tables, rules, and inline bold/italic/strike/code/links) and the sanitiser
that removes images and raw HTML. Used by convert_md.py to produce Slack mrkdwn and email HTML."""

from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE = re.compile(r"^\s*(```+|~~~+)\s*([\w+-]*)\s*$")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_RULE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_RAW_TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^<>]*)?/?>")
_CODE_SPAN = re.compile(r"(`+[^`]*?`+)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

INLINE = re.compile(
    r"(?P<code>`+)(?P<ctext>.+?)(?P=code)"
    r"|\[(?P<label>[^\]]+)\]\((?P<url>[^)\s]+)\)"
    r"|<(?P<auto>(?:https?://|mailto:)[^>\s]+)>"
    r"|\*\*(?P<b1>.+?)\*\*|__(?P<b2>.+?)__"
    r"|~~(?P<s>.+?)~~"
    r"|\*(?P<i1>[^*\s](?:[^*]*[^*\s])?)\*"
    r"|(?<![\w])_(?P<i2>[^_\s](?:[^_]*[^_\s])?)_(?![\w])"
)


@dataclass(frozen=True)
class Block:
    kind: str                  # heading | para | ul | ol | code | quote | table | rule
    lines: tuple[str, ...] = ()
    level: int = 0             # heading level
    lang: str = ""             # code
    rows: tuple[tuple[str, ...], ...] = ()  # table: header row first


def sanitize(markdown: str) -> str:
    """Images and raw HTML removed outside code (fences and inline spans); everything else kept."""
    lines: list[str] = []
    in_fence = False
    for line in markdown.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            lines.append(line)
            continue
        lines.append(line if in_fence else "".join(
            piece if piece.startswith("`") else _RAW_TAG.sub("", _IMAGE.sub("", piece))
            for piece in _CODE_SPAN.split(line)))
    return "\n".join(lines)


def plain_words_source(markdown: str) -> str:
    """The text a reader sees: sanitised, with link targets dropped (used by the fidelity check)."""
    return _LINK.sub(r"\1", sanitize(markdown))


def parse(markdown: str) -> list[Block]:
    lines = markdown.replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        block, i = _next_block(lines, i)
        if block is not None:
            blocks.append(block)
    return blocks


def _next_block(lines: list[str], i: int) -> tuple[Block | None, int]:
    line = lines[i]
    if not line.strip():
        return None, i + 1
    fence = _FENCE.match(line)
    if fence:
        end = next((j for j in range(i + 1, len(lines)) if lines[j].strip().startswith(fence.group(1))), len(lines))
        return Block("code", tuple(lines[i + 1:end]), lang=fence.group(2)), end + 1
    heading = _HEADING.match(line)
    if heading:
        return Block("heading", (heading.group(2),), level=len(heading.group(1))), i + 1
    if _RULE.match(line):
        return Block("rule"), i + 1
    if "|" in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
        return _table(lines, i)
    for kind, pattern in (("ul", _BULLET), ("ol", _NUMBERED), ("quote", _QUOTE)):
        if pattern.match(line):
            return _run(lines, i, kind, pattern)
    end = i
    while end < len(lines) and lines[end].strip() and not _starts_block(lines, end):
        end += 1
    return Block("para", tuple(x.strip() for x in lines[i:max(end, i + 1)])), max(end, i + 1)


def _starts_block(lines: list[str], i: int) -> bool:
    line = lines[i]
    return bool(_FENCE.match(line) or _HEADING.match(line) or _RULE.match(line) or _BULLET.match(line)
                or _NUMBERED.match(line) or _QUOTE.match(line))


def _run(lines: list[str], i: int, kind: str, pattern: re.Pattern) -> tuple[Block, int]:
    items: list[str] = []
    while i < len(lines) and pattern.match(lines[i]):
        items.append(pattern.match(lines[i]).group(1))
        i += 1
    return Block(kind, tuple(items)), i


def _table(lines: list[str], i: int) -> tuple[Block, int]:
    rows = [_cells(lines[i])]
    i += 2
    while i < len(lines) and "|" in lines[i] and lines[i].strip():
        rows.append(_cells(lines[i]))
        i += 1
    return Block("table", rows=tuple(rows)), i


def _cells(line: str) -> tuple[str, ...]:
    body = line.strip()
    body = body.removeprefix("|")
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return tuple(cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", body))
