"""Pure: the reply, from the checked envelope only. Re-render views cost no LLM call. Markdown only: no raw
HTML, no images, links only when their URL is in the user's input."""

from __future__ import annotations

import json
from collections.abc import Sequence

import prompts
from checks import cell, clean_text

EMPTY = "None found."


def render(envelope: dict, view: str = "default", source: str = "", extra_notes: Sequence[str] = ()) -> str:
    if view == "numbers":
        body = numbers_view(envelope)
    elif view == "headline":
        body = f"**Headline:** {clean_text(envelope.get('headline') or EMPTY, source)}"
    else:
        story = clean_text(envelope.get("body") or "", source)
        body = "\n\n".join(p for p in (f"**Headline:** {clean_text(envelope.get('headline') or EMPTY, source)}",
                                       story) if p)
    return "\n\n".join([body, footer(envelope, extra_notes)])


def as_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, indent=2, ensure_ascii=False, default=str) + "\n```"


def numbers_view(envelope: dict) -> str:
    facts = envelope.get("items") or []
    if not facts:
        return "**The numbers**\n" + EMPTY
    rows = [f"| {cell(f.get('id', ''))} | {cell(f.get('text', ''))} |" for f in facts]
    return "\n".join(["**The numbers** (computed from your table)", "| id | fact |", "|---|---|", *rows])


def footer(envelope: dict, extra_notes: Sequence[str] = ()) -> str:
    meta = [prompts.FOOTER]
    if envelope.get("template_used"):
        meta.append("This wording comes straight from the computed facts.")
    if envelope.get("dropped"):
        meta.append(f"{envelope['dropped']} item(s) dropped by my checks.")
    meta.append(f"Confidence: {envelope.get('confidence', 'low')}.")
    if envelope.get("needs_human"):
        meta.append("Worth a human look before you share it.")
    notes = [n for n in dict.fromkeys([*(envelope.get("caveats") or []), *extra_notes]) if n]
    return "\n".join([f"_({' '.join(meta)})_", *(f"- {n}" for n in notes)])
