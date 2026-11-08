"""Pure rendering, all in code: the headline, one section per file with its Markdown inside a fence
(so the chat's Download button yields a real .md and the document cannot break out), the files not
converted, coverage notes, and paging across messages. No raw HTML or images outside the fences."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

import paging
import prompts
import sources
from models import Doc, Envelope, Failure

DOWNLOAD_HINT = "_Use Copy or Download on the block to get the .md file._"
_RUN = re.compile(r"`+")
_ESCAPE = re.compile(r"([\\`*_\[\]<>#|])")


@dataclass(frozen=True)
class Rendered:
    pages: tuple[str, ...]
    warnings: tuple[str, ...]


def fence_for(text: str) -> str:
    longest = max((len(m) for m in _RUN.findall(text)), default=0)
    return "`" * max(3, longest + 1)


def fenced(text: str, info: str) -> str:
    ticks = fence_for(text)
    return f"{ticks}{info}\n{text.rstrip(chr(10))}\n{ticks}"


def safe(text: str) -> str:
    return _ESCAPE.sub(r"\\\1", " ".join(str(text).split()))


def json_reply(env: Envelope) -> str:
    return fenced(json.dumps(env.model_dump(), indent=2, ensure_ascii=False), "json")


def facts(doc: Doc) -> str:
    units = [f"{n:,} {k if n != 1 else k.rstrip('s')}" for k, n in doc.units.items()]
    parts = [safe(doc.name), sources.SHORT_LABEL.get(doc.source, doc.source), doc.format, *units,
             f"{doc.chars:,} characters"]
    return "  ·  ".join(parts)


def render(env: Envelope, notes: Sequence[str] = (), beyond: bool = False,
           limit: int = paging.PAGE_CHARS) -> Rendered:
    title, _, count = env.summary.removesuffix(".").partition(" (")
    head = [f"**{title}**" + (f" ({count}" if count else "")]
    tail = _tail(env.failed, notes, beyond)
    sections = [s for doc in env.items for s in _sections(doc, limit)]
    pages = _pack(sections, limit)
    total = len(pages)
    out = []
    for i, page in enumerate(pages, start=1):
        blocks = (head + page + tail) if i == 1 else page
        if total > 1:
            nxt = f" Say 'part {i + 1}' for the next." if i < total else ""
            blocks = [*blocks, f"_Part {i} of {total}.{nxt}_"]
        out.append("\n\n".join(blocks))
    if not out:
        out = ["\n\n".join(head + tail)]
    warnings = tuple(f"{d.name}: {w}" for d in env.items for w in d.warnings)
    return Rendered(tuple(out), warnings)


def _sections(doc: Doc, limit: int) -> list[str]:
    pieces = paging.split(doc.markdown, limit) if doc.markdown.strip() else ["(no text)"]
    out = []
    for i, piece in enumerate(pieces, start=1):
        label = f"### {facts(doc)}" + (f"  ·  part {i} of {len(pieces)}" if len(pieces) > 1 else "")
        lines = [label]
        if i == 1:
            lines += [f"Warning: {safe(w)}" for w in doc.warnings]
        lines += [fenced(piece, "markdown"), DOWNLOAD_HINT]
        out.append("\n".join(lines))
    return out


def _pack(sections: list[str], limit: int) -> list[list[str]]:
    pages: list[list[str]] = []
    size = 0
    for section in sections:
        if pages and size + len(section) <= limit:
            pages[-1].append(section)
            size += len(section)
        else:
            pages.append([section])
            size = len(section)
    return pages


def _tail(failed: Sequence[Failure], notes: Sequence[str], beyond: bool) -> list[str]:
    tail = []
    if failed:
        lines = [f"- {safe(f.name)}: {safe(f.reason)}" for f in failed]
        tail.append("**Not converted:**\n" + "\n".join(lines))
    tail += [safe(n) for n in notes]
    if beyond:
        tail.append(prompts.SECOND_HALF)
    return tail
