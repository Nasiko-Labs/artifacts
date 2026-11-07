"""Pure: the markdown replies, rendered by code. Model text is cleaned first: no HTML, no images, no links, no
headings or line breaks that could restyle the reply."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from envelope import Review, by_id, confidence
from evidence import Evidence
from verify import Statement

QUOTE_CHARS = 300
_TAG = re.compile(r"<[^>\n]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_SPACES = re.compile(r"\s+")
_LEADING_MARKUP = re.compile(r"^[#>*\-\s]+")
_HEADING = re.compile(r"#{1,6}(?=\s)")
SECTION_TITLES = (("summary", "Summary"), ("strengths", "Strengths"), ("growth", "Growth areas"))
FOOTER = ("Say \"show the evidence\", \"only the gaps\", \"shorter\", \"remove e5\", \"add: <example>\" or "
          "\"as json\".")


def clean(text: str) -> str:
    plain = _HEADING.sub(" ", _LINK.sub(r"\1", _IMAGE.sub("", _TAG.sub("", text or ""))))
    return _LEADING_MARKUP.sub("", _SPACES.sub(" ", plain)).replace("**", "").strip()


def _tags(ids: Sequence[str]) -> str:
    return f"[{', '.join(ids)}]"


def _line(s: Statement) -> str:
    own = " (self-reported)" if s.self_reported else ""
    return f"{clean(s.text)}{own} {_tags(s.evidence)}"


def title(review: Review) -> str:
    about = ", ".join(p for p in (review.role, review.period) if p)
    about = f" ({clean(about)})" if about else ""
    return f"**DRAFT performance review: {clean(review.person)}{about}** (for the manager to edit; no rating given)"


def _sections(statements: Sequence[Statement]) -> list[str]:
    blocks: list[str] = []
    for key, name in SECTION_TITLES:
        chosen = [s for s in statements if s.section == key]
        if not chosen:
            continue
        if key == "summary":
            blocks.append(f"**{name}**\n" + " ".join(_line(s) for s in chosen))
        else:
            blocks.append(f"**{name}**\n" + "\n".join(f"- {s.competency.capitalize()}: {_line(s)}" for s in chosen))
    return blocks


def gaps_block(review: Review) -> str:
    if not review.gaps:
        return "**Evidence gaps**\n- None: every required competency has at least 2 usable items."
    lines = []
    known = by_id(review.evidence)
    for g in review.gaps:
        noun = "item" if g["have"] == 1 else "items"
        need = ("all self-reported; need one from someone else" if g["have"] >= g["needed"]
                else f"need {g['needed']}")
        line = (f"- {g['competency'].capitalize()}: {g['have']} usable {noun} ({need}). "
                f"Prompt: \"{g['suggested_prompt']}\"")
        general = [f"{i} \"{clean(known[i].text)[:60]}\"" for i in g["unusable"] if i in known]
        if general:
            line += f" ({'; '.join(general)}: too general to use)"
        lines.append(line)
    return "**Evidence gaps**\n" + "\n".join(lines)


def flags_block(review: Review) -> str:
    lines = [f"- {clean(f['text'])}" for f in review.flags]
    if review.left_out:
        ids = ", ".join(x["id"] for x in review.left_out)
        lines.append(f"- Left out, never used ({'personal characteristics'}): {ids}.")
    return "**Flags**\n" + "\n".join(lines) if lines else ""


def _footer(review: Review) -> str:
    dropped = len(review.dropped)
    drop_note = f"{dropped} drafted statement{'s' if dropped != 1 else ''} dropped (no evidence or failed checks). " \
        if dropped else ""
    return (f"_Draft for your review: {len(review.usable)} usable of {len(review.evidence)} items. {drop_note}"
            f"Confidence: {confidence(review)}. {FOOTER}_")


def render(review: Review, statements: Sequence[Statement] | None = None) -> str:
    chosen = review.statements if statements is None else statements
    blocks = [title(review), *_sections(chosen), gaps_block(review), flags_block(review), _footer(review),
              *review.notes]
    return "\n\n".join(b for b in blocks if b)


def render_evidence(review: Review, section: str = "") -> str:
    known = by_id(review.evidence)
    chosen = [s for s in review.statements if not section or s.section == section]
    if not chosen:
        return f"There are no {section or 'drafted'} statements in this draft."
    lines = [f"**Evidence behind the {section or 'draft'}: {clean(review.person)}**"]
    for s in chosen:
        lines.append(f"- {s.competency.capitalize() or 'Summary'}: {_line(s)}")
        lines += [f"  - {_quote(known[i])}" for i in s.evidence if i in known]
    return "\n".join(lines)


def _quote(e: Evidence) -> str:
    when = f", {e.date}" if e.date else ""
    who = f" ({clean(e.author)})" if e.author else ""
    return f"{e.id} ({e.source}{who}{when}): \"{clean(e.text)[:QUOTE_CHARS]}\""


def render_gaps(review: Review) -> str:
    return "\n\n".join([gaps_block(review), "_Draft for your review. Fill a gap with \"add: <example>\"._"])


def render_table(review: Review, names: Sequence[str]) -> str:
    """The deadline fallback: the evidence by competency, no prose."""
    usable = review.usable
    blocks = [f"**Evidence by competency: {clean(review.person)}** (no prose; for the manager to write from)"]
    for name in names:
        rows = [e for e in usable if name in e.competencies]
        if rows:
            blocks.append(f"**{name.capitalize()}**\n" + "\n".join(f"- {_quote(e)}" for e in rows))
    untagged = [e for e in review.evidence if not e.tagged and not e.excluded]
    if untagged:
        blocks.append("**Not yet tagged**\n" + "\n".join(f"- {_quote(e)}" for e in untagged))
    blocks += [gaps_block(review), flags_block(review), _footer(review), *review.notes]
    return "\n\n".join(b for b in blocks if b)


def render_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"
