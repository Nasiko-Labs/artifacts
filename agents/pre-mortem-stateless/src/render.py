"""Pure: the markdown reply, rendered by code from the envelope. Model text is cleaned first: no HTML, no
images, no links the user didn't paste, no headings or line breaks that could restyle the reply."""

from __future__ import annotations

import json
import re

from rank import MIN_COUNT

MAX_LINES_SHOWN = 400
_TAG = re.compile(r"<[^>\n]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_SPACES = re.compile(r"\s+")
_LEADING_MARKUP = re.compile(r"^[#>*\-\s]+")
_HEADING = re.compile(r"#{1,6}(?=\s)")


def clean(text: str) -> str:
    """One line of plain text: tags, images and link targets removed, markdown openers dropped."""
    plain = _HEADING.sub(" ", _LINK.sub(r"\1", _IMAGE.sub("", _TAG.sub("", text or ""))))
    return _LEADING_MARKUP.sub("", _SPACES.sub(" ", plain)).replace("**", "").strip()


def _lens_names(lenses: list[str]) -> str:
    return ", ".join(name.capitalize() for name in lenses)


def _story(mode: dict) -> str:
    head = (f"**{mode['rank']}. {clean(mode['title'])}** (likelihood {mode['likelihood']}, impact "
            f"{mode['impact']}; raised by {_lens_names(mode['raised_by'])})")
    breaks = f"Breaks: \"{clean(mode['root_assumption'])}\" " if mode["root_assumption"] else ""
    return "\n".join([
        head,
        f"Story: {clean(mode['story'])}",
        f"{breaks}(L{mode['plan_line']}: \"{clean(mode['plan_ref'])}\")",
        f"Early warning: {clean(mode['early_warning'])} | Mitigation: {clean(mode['mitigation'])}",
    ])


def render(envelope: dict, top: int | None = None, notes: tuple[str, ...] = ()) -> str:
    modes = envelope["failure_modes"][:top] if top else envelope["failure_modes"]
    total = len(envelope["failure_modes"])
    title = (f"**Pre-mortem: {total} ways this plan fails** (read {envelope['plan_lines']} lines)" if not top else
             f"**Pre-mortem: the top {len(modes)} of {total} ways this plan fails**")
    blocks = [title, *(_story(m) for m in modes)]
    if envelope["unstated_assumptions"] and not top:
        blocks.append("**Unstated assumptions**\n" + "\n".join(
            f"- {clean(a['text'])} (L{a['plan_line']})" for a in envelope["unstated_assumptions"]))
    if envelope["questions_for_the_team"] and not top:
        blocks.append("**Questions for the team**\n" + "\n".join(
            f"{n}. {clean(q)}" for n, q in enumerate(envelope["questions_for_the_team"], start=1)))
    blocks += _footer(envelope, total, bool(top))
    blocks += [n for n in notes if n]
    return "\n\n".join(blocks)


def _footer(envelope: dict, total: int, is_view: bool) -> list[str]:
    lines: list[str] = []
    if 0 < total < MIN_COUNT and not is_view:
        lines.append(f"Only {total} failure modes were specific enough to tie to your plan; the rest were generic "
                     "and left out.")
    left = envelope.get("left_out") or 0
    noun = "candidate" if left == 1 else "candidates"
    left_note = f"{left} {noun} left out: too generic to tie to your plan, or not grounded in it. " if left else ""
    lines.append(f"_({left_note}Say \"go deeper on 2\", \"only the top 3\", \"show the plan lines\" or "
                 "\"as json\".)_")
    if envelope.get("needs_human"):
        lines.append("_Someone should review this before you proceed: "
                     + ("at least one failure is both likely and high impact._" if envelope["confidence"] != "low"
                        else "confidence is low (few specific failures, few lenses, or a short plan)._"))
    return lines


def render_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"


def render_lines(lines: list[str] | tuple[str, ...]) -> str:
    shown = [f"L{n}: {clean(line)}" for n, line in enumerate(lines[:MAX_LINES_SHOWN], start=1)]
    more = [f"_(and {len(lines) - MAX_LINES_SHOWN} more lines)_"] if len(lines) > MAX_LINES_SHOWN else []
    return "\n".join([f"**The plan as I numbered it ({len(lines)} lines)**", "", *shown, *more])


def render_deep(mode: dict, causes: list[dict]) -> str:
    head = f"**Deeper on {mode['rank']}. {clean(mode['title'])}**"
    body = [f"{n}. {clean(c['text'])} (L{c['plan_line']}: \"{clean(c['plan_ref'])}\")"
            for n, c in enumerate(causes, start=1)]
    return "\n".join([head, *body])
