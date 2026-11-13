"""Pure: the render pipeline (detect shapes -> render the target -> limits -> fidelity check) and the
output envelope. A fidelity failure falls back to Markdown (then to the JSON as given), never to
partial output. `confidence` reflects only rendering loss; `needs_human` is copied from the input."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import convert_md
import escape
import fidelity
import limits
import render_email
import render_md
import render_slack
import replies
import shapes
from model import EMAIL, MARKDOWN, SLACK, TARGET_LABELS, Document, Options, Stats

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rendering:
    target: str
    formatted: str
    lang: str                     # fence language in chat: "" = shown as is
    stats: Stats
    notes: tuple[str, ...] = ()
    plain_text: str | None = None
    subject: str | None = None
    items: tuple[dict, ...] = ()
    confidence: str = "high"
    needs_human: bool = False
    summary: str = ""

    def to_envelope(self) -> dict:
        return {"summary": self.summary, "items": list(self.items), "formatted": self.formatted,
                "target": self.target, "plain_text": self.plain_text, "notes": list(self.notes),
                "confidence": self.confidence, "needs_human": self.needs_human}


def _draw(docs: list[Document], opts: Options) -> tuple[str, str, Stats, str, str | None]:
    """(formatted, fence lang, stats, visible text with escaping undone, plain text)."""
    if opts.target == SLACK:
        payload, stats = render_slack.render(docs, opts)
        visible = "\n".join([*render_slack.block_texts(payload["blocks"]), payload["text"]])
        return json.dumps(payload, ensure_ascii=False, indent=2), "json", stats, escape.slack_unescape(visible), None
    if opts.target == EMAIL:
        markup, stats = render_email.render(docs, opts)
        plain, _ = render_md.render(docs, opts, plain=True)
        return markup, "html", stats, escape.html_unescape(markup), plain
    text, stats = render_md.render(docs, opts)
    return text, "", stats, escape.md_unescape(text), None


def render_values(values: list[Any], opts: Options) -> Rendering:
    docs = [shapes.build_document(v) for v in values]
    formatted, lang, stats, visible, plain = _draw(docs, opts)
    target, fallback_note = opts.target, None
    if not fidelity.check(values, visible, stats.excluded).ok:
        logger.error("fidelity check failed for target %s; falling back", opts.target)
        target, fallback_note, plain = MARKDOWN, replies.NOT_FAITHFUL.format(target=TARGET_LABELS[opts.target]), None
        formatted, lang, stats, visible, _ = _draw(docs, Options(MARKDOWN, opts.compact, opts.footer, opts.title))
        if opts.target == MARKDOWN or not fidelity.check(values, visible, stats.excluded).ok:
            formatted, lang, stats = _as_given(values), "json", Stats()
            fallback_note = replies.NOT_FAITHFUL_ANY
    notes = _notes(docs, stats, fallback_note)
    loss = bool(stats.cut or stats.collapsed or fallback_note or any(d.deep for d in docs))
    generic = any(d.template is None for d in docs)
    items = tuple({"section": s.title, "rows": s.size} for d in docs for s in d.sections)
    rows = sum(i["rows"] for i in items)
    count = len(docs)
    summary = (f"Formatted {count} envelope{'s' if count != 1 else ''} ({rows} item{'s' if rows != 1 else ''}) "
               f"as {TARGET_LABELS[target]}.")
    lead = docs[0].lead if docs and docs[0].lead else ""
    return Rendering(target=target, formatted=formatted, lang=lang, stats=stats, notes=notes, plain_text=plain,
                     subject=limits.cut(lead, limits.SUBJECT_CHARS) if target == EMAIL and lead else None,
                     items=items, confidence="medium" if loss or generic else "high",
                     needs_human=any(d.needs_human for d in docs), summary=summary)


def render_markdown(source: str, opts: Options) -> Rendering:
    """Pasted Markdown (not JSON): converted for Slack or email; a Markdown target returns it unchanged."""
    if opts.target == SLACK:
        chunks = limits.split_text(convert_md.to_mrkdwn(source).strip(), limits.SLACK_SECTION_CHARS)
        blocks = [render_slack.section(c) for c in chunks][: limits.SLACK_MAX_BLOCKS]
        payload = {"blocks": blocks, "text": escape.slack_entities(limits.cut(" ".join(source.split()), 150))}
        formatted, lang, plain = json.dumps(payload, ensure_ascii=False, indent=2), "json", None
        stats = Stats(blocks=len(blocks))
        visible = escape.slack_unescape("\n".join(render_slack.block_texts(blocks)))
    elif opts.target == EMAIL:
        formatted = render_email.page(convert_md.to_html(source))
        lang, plain, stats = "html", source.strip(), Stats()
        visible = escape.html_unescape(formatted)
    else:
        formatted, lang, plain, stats = source.strip(), "", None, Stats()
        visible = formatted
    if lang and not fidelity.words_kept(source, visible):
        logger.error("markdown conversion lost words for target %s", opts.target)
        return Rendering(target=MARKDOWN, formatted=source.strip(), lang="", stats=Stats(),
                         notes=(replies.NOT_FAITHFUL.format(target=TARGET_LABELS[opts.target]),),
                         items=({"section": "Markdown", "rows": 1},), confidence="medium",
                         summary="Returned the Markdown unchanged.")
    summary = f"Converted Markdown to {TARGET_LABELS[opts.target]}."
    return Rendering(target=opts.target, formatted=formatted, lang=lang, stats=stats, plain_text=plain,
                     items=({"section": "Markdown", "rows": 1},), summary=summary)


def _as_given(values: list[Any]) -> str:
    return "\n\n".join(json.dumps(v, ensure_ascii=False, indent=2) for v in values)


def _notes(docs: list[Document], stats: Stats, fallback: str | None) -> tuple[str, ...]:
    notes = [fallback] if fallback else []
    if stats.cut:
        notes.append(f"{stats.cut} long value{'s' if stats.cut != 1 else ''} shortened (say \"compact\" to see "
                     "them whole)")
    if stats.collapsed:
        notes.append(f"{stats.collapsed} Slack blocks over the 50-block limit collapsed into a note")
    deep = sum(d.deep for d in docs)
    if deep:
        notes.append(f"{deep} field{'s' if deep != 1 else ''} nested deeper than {shapes.MAX_DEPTH} levels shown as JSON")
    return tuple(notes)
