"""Pure: postmortem documents. Which titles count, the action-item section of a document's text, the
ticket references in it, and the check of the items the model extracted from it."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from corpus import Record, norm
from records import TICKET_KEY, ActionItem, text_of

SECTION_CHARS = 4_000
FALLBACK_CHARS = 3_000
MAX_DOCS = 8
DOCS_PER_CALL = 4

POSTMORTEM_TITLE = re.compile(r"post[- ]?mortem|incident review|\bRCA\b", re.IGNORECASE)
_SECTION_TITLE = re.compile(r"action items?|follow[- ]ups?|remediation|next steps", re.IGNORECASE)
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_HTML_HEADING = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
_HTML_BREAK = re.compile(r"<\s*(?:br|/p|/li|/tr|/div)\s*/?>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")
_BROWSE_URL = re.compile(r"https?://[^\s\"'<>)]+/browse/([A-Z][A-Z0-9]+-\d+)")
# Look like ticket keys but are standards and encodings.
_NOT_TICKETS = frozenset({"UTF", "SHA", "ISO", "RFC", "HTTP", "TLS", "SSL", "MD", "AES", "RSA", "IPV", "CVE", "COVID"})


@dataclass(frozen=True)
class Doc:
    id: str
    title: str
    url: str | None
    created_at: str
    section: str  # the action-item section (or the opening of the document), read by code


def is_postmortem_title(title: str) -> bool:
    return bool(POSTMORTEM_TITLE.search(title or ""))


def content_of(doc_id: str, records: Sequence[Record]) -> str | None:
    """The longest text a call made for this document returned: its content."""
    bodies = [_plain(r.body) for r in records if r.mentions(doc_id)]
    bodies = [b for b in bodies if b.strip()]
    return max(bodies, key=len) if bodies else None


def _plain(body: str) -> str:
    """The document text inside a tool result: the longest string of a JSON body, else the body."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return body
    strings = list(_strings(parsed))
    return max(strings, key=len) if strings else body


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def to_lines(text: str) -> list[str]:
    """Markdown or HTML as lines, with HTML headings turned into markdown headings."""
    marked = _HTML_HEADING.sub(lambda m: f"\n{'#' * int(m.group(1))} {_HTML_TAG.sub('', m.group(2))}\n", text)
    plain = html.unescape(_HTML_TAG.sub(" ", _HTML_BREAK.sub("\n", marked)))
    return [line.rstrip() for line in plain.splitlines()]


def action_section(text: str) -> str:
    """From the heading that names action items (or follow-ups, remediation, next steps) to the next
    heading of the same or a higher level, at most 4k chars; without one, the first 3k chars."""
    lines = to_lines(text or "")
    for i, line in enumerate(lines):
        heading = _MD_HEADING.match(line.strip())
        if heading and _SECTION_TITLE.search(heading.group(2)):
            level = len(heading.group(1))
            end = next((j for j in range(i + 1, len(lines)) if _heading_level(lines[j]) and
                        _heading_level(lines[j]) <= level), len(lines))
            return "\n".join(lines[i:end]).strip()[:SECTION_CHARS]
    return "\n".join(lines).strip()[:FALLBACK_CHARS]


def _heading_level(line: str) -> int:
    match = _MD_HEADING.match(line.strip())
    return len(match.group(1)) if match else 0


def ticket_refs(text: str) -> tuple[str, ...]:
    """Ticket keys in the text, in order: bare keys and tracker browse links."""
    found = [m.group(1) for m in _BROWSE_URL.finditer(text)] + TICKET_KEY.findall(text)
    return tuple(dict.fromkeys(k for k in found if k.split("-")[0] not in _NOT_TICKETS))


def key_of(ref: str | None) -> str | None:
    """`OPS-412` or a browse link to it → `OPS-412`."""
    if not ref:
        return None
    match = _BROWSE_URL.search(ref) or TICKET_KEY.search(ref)
    if not match:
        return None
    key = match.group(1) if match.re is _BROWSE_URL else match.group(0)
    return None if key.split("-")[0] in _NOT_TICKETS else key


def check_extracted(raw: Any, docs: Sequence[Doc], pm_for_doc: Mapping[str, str]) -> tuple[list[ActionItem], int]:
    """Items whose quote (and ticket ref, if any) appear verbatim in their document's section, then
    one item per ticket key code found in a section that the model missed. Returns (items, dropped)."""
    by_id = {d.id: d for d in docs}
    rows = [r for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []
    items: list[ActionItem] = []
    for row in rows:
        doc = by_id.get(text_of(row.get("doc_id")))
        quote, ref = text_of(row.get("text_quote")), text_of(row.get("ticket_ref")) or None
        if doc is None or not quote or norm(quote) not in norm(doc.section):
            continue
        if ref and norm(ref) not in norm(doc.section):
            ref = None
        owner = text_of(row.get("owner_text")) or None
        owner = owner if owner and norm(owner) in norm(doc.section) else None
        items.append(ActionItem(id=f"doc-{len(items) + 1}", postmortem_id=pm_for_doc[doc.id],
                                ticket_key=key_of(ref), text=quote, from_doc=True, owner_text=owner))
    dropped = len(rows) - len(items)
    return [*items, *_missed_refs(docs, items, pm_for_doc)], dropped


def _missed_refs(docs: Sequence[Doc], items: Sequence[ActionItem], pm_for_doc: Mapping[str, str]) -> list[ActionItem]:
    have = {i.ticket_key for i in items if i.ticket_key}
    added: list[ActionItem] = []
    for doc in docs:
        for key in ticket_refs(doc.section):
            if key in have:
                continue
            have.add(key)
            line = next((ln.strip(" -*\t") for ln in doc.section.splitlines() if key in ln), key)
            added.append(ActionItem(id=f"doc-ref-{len(added) + 1}", postmortem_id=pm_for_doc[doc.id],
                                    ticket_key=key, text=line[:200] or key, from_doc=True))
    return added
