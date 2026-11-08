"""Pure: the chat text of a rendering. MAF reads text only, so the text part is `formatted` itself (the
Markdown as is; Slack JSON and HTML in fenced blocks, so raw HTML is never injected into the chat page)
plus a short status line; "as json" gives the envelope instead."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import escape
from envelope import Rendering
from model import EMAIL, SLACK

_BACKTICKS = re.compile(r"`{3,}")


@dataclass(frozen=True)
class Reply:
    text: str
    envelope: dict | None = None


def fence(lang: str, body: str) -> str:
    """A fenced block that cannot be closed early by backticks inside `body`."""
    longest = max((len(run) for run in _BACKTICKS.findall(body)), default=2)
    ticks = "`" * (longest + 1)
    return f"{ticks}{lang}\n{body}\n{ticks}"


def reply(rendering: Rendering, as_json: bool = False, plain_version: bool = False) -> Reply:
    envelope = rendering.to_envelope()
    if as_json:
        return Reply(fence("json", json.dumps(envelope, ensure_ascii=False, indent=2)), envelope)
    if plain_version and rendering.plain_text is not None:
        text = "Plain-text version:\n\n" + fence("text", rendering.plain_text)
        return Reply(_join(text, *_notes(rendering), _status(rendering, plain_version=True)), envelope)
    body = fence(rendering.lang, rendering.formatted) if rendering.lang else rendering.formatted
    subject = f"Suggested subject: {escape.md_inline(rendering.subject)}" if rendering.subject else ""
    return Reply(_join(subject, body, *_notes(rendering), _status(rendering)), envelope)


def _notes(rendering: Rendering) -> list[str]:
    return [f"_{escape.md_inline(note)}_" for note in rendering.notes]


def _status(rendering: Rendering, plain_version: bool = False) -> str:
    cut = rendering.stats.cut
    if rendering.target == SLACK:
        return (f"_Rendered as Slack blocks: {rendering.stats.blocks} blocks, {cut} cut. "
                "Say \"as email\" or \"as markdown\"._")
    if rendering.target == EMAIL:
        ask = "\"as email\" for the HTML" if plain_version else "\"show the plain-text version\""
        return f"_Rendered as email HTML with a plain-text version. Say {ask}, \"as slack\" or \"as markdown\"._"
    sections = len(rendering.items)
    return (f"_Rendered as Markdown: {sections} section{'s' if sections != 1 else ''}, {cut} shortened. "
            "Say \"as slack\" or \"as email\"._")


def _join(*parts: str) -> str:
    return "\n\n".join(p for p in parts if p)
