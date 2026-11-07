"""Pure: the markdown description and the chat reply, built by code. Model text is cleaned first: no HTML, no
images, no emojis, no links the user didn't paste, no headings or line breaks that could restyle the reply.
Template text the user pasted (headings, checkboxes, comments) is kept verbatim; a checkbox is never ticked."""

from __future__ import annotations

import json
import re

from template import Heading

WHY_PLACEHOLDER = "Why: [not provided — add the motivation or ticket link]"
FOOTER = "_Copy the block above into the PR. Fill the placeholders first. Draft only: nothing was posted._"
_TAG = re.compile(r"<[^>\n]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)\)")
_URL = re.compile(r"https?://[^\s)>\]]+")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_LEADING_MARKUP = re.compile(r"^[#>*\-\s]+")
_HEADING = re.compile(r"#{1,6}(?=\s)")


def clean(text: str, allowed: str = "") -> str:
    """One line of plain text. Links and bare URLs survive only when they appear in `allowed` (the input)."""
    plain = _TAG.sub("", _IMAGE.sub("", text or ""))
    plain = _LINK.sub(lambda m: m.group(0) if m.group(2) and m.group(2) in allowed else m.group(1), plain)
    plain = _URL.sub(lambda m: m.group(0) if m.group(0) in allowed else "", plain)
    plain = _HEADING.sub(" ", _EMOJI.sub("", plain))
    return _LEADING_MARKUP.sub("", " ".join(plain.split())).replace("**", "").strip()


def size_text(facts) -> str:
    return f"{facts.files} file{'s' if facts.files != 1 else ''}, +{facts.added} −{facts.removed}"


def _change(change: dict, allowed: str) -> str:
    text = clean(change["text"], allowed).rstrip(".")
    missing = [f"`{p}`" for p in change["files"] if p not in text]
    where = f" ({', '.join(missing)})" if missing else ""
    return f"- **{clean(change['area'], allowed) or 'change'}:** {text}{where}"


def sections(draft: dict, facts, why: str | None, steps: list[str], tested: str, allowed: str) -> dict[str, str]:
    """Body text per section key, ready to place under a heading."""
    summary = clean(draft["summary"], allowed) + f" ({size_text(facts)})"
    why_lines = [why[:1].upper() + why[1:] if why else WHY_PLACEHOLDER]
    refs = [r for r in facts.tickets if r not in facts.closes]
    if refs:
        why_lines.append("Refs: " + ", ".join(refs))
    why_lines += [f"Closes {ref}" for ref in facts.closes]
    changes = [_change(c, allowed) for c in draft["changes"]]
    risk_lines = [f"- {clean(n['text'], allowed)}" for n in draft["risk_notes"]]
    risk_lines += [f"- {note}" for note in (facts.size_note, facts.split_note) if note]
    out = {
        "summary": summary,
        "why": "\n\n".join(why_lines),
        "changes": "\n".join(changes) or "- [add the main changes]",
        "test_plan": "\n".join([*steps, "", tested]),
        "risk": "\n".join(risk_lines) or "- No risk surface found in code: low by default.",
        "reviewers": "\n".join(f"- {clean(n, allowed)}" for n in draft["reviewer_notes"]),
        "related": "\n".join(f"- {r}" for r in facts.tickets),
    }
    if facts.breaking:
        names = ", ".join(f"`{name}`" for name in facts.breaking)
        out["breaking"] = (f"**Breaking change:** removes {names}. Callers of {names} must be updated before this "
                           "merges.")
    return out


def description(draft: dict, facts, body: dict[str, str], headings: tuple[Heading, ...], allowed: str) -> str:
    extras = [f"## {clean(s['heading'], allowed)}\n{clean(s['text'], allowed)}"
              for s in draft.get("extra_sections") or [] if clean(s["text"], allowed)]
    if headings:
        return "\n\n".join([*_templated(headings, body, facts), *extras])
    blocks = [f"## Summary\n{body['summary']}", f"## Why\n{body['why']}"]
    if "breaking" in body:
        blocks.append(f"## Breaking change\n{body['breaking']}")
    blocks += [f"## Changes\n{body['changes']}", f"## Test plan\n{body['test_plan']}",
               f"## Risk — {facts.risk}\n{body['risk']}"]
    if body["reviewers"]:
        blocks.append(f"## Notes for reviewers\n{body['reviewers']}")
    return "\n\n".join(blocks + extras)


def _templated(headings: tuple[Heading, ...], body: dict[str, str], facts) -> list[str]:
    blocks = []
    keys = {h.key for h in headings}
    for h in headings:
        head = f"{'#' * h.level} {h.title}"
        filled = body.get(h.key or "", "") if h.key != "related" or facts.tickets else ""
        if h.key == "summary" and "breaking" in body and "breaking" not in keys:
            filled += "\n\n" + body["breaking"]
        kept = [line for line in h.body if line.lstrip().startswith(("- [", "* [", "<!--"))]
        if filled:
            blocks.append("\n".join([head, *kept, filled]) if kept else f"{head}\n{filled}")
        elif not h.blank:
            blocks.append("\n".join([head, *h.body]))
        else:
            blocks.append(f"{head}\n[add {h.title.casefold()}]")
    if "risk" not in keys:
        blocks.append(f"## Risk — {facts.risk}\n{body['risk']}")
    return blocks


def reply(envelope: dict, notes: list[str], as_json: bool) -> str:
    if as_json:
        return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"
    blocks = [f"**Title:** `{envelope['title']}`", envelope["description_markdown"], FOOTER]
    return "\n\n".join(blocks + [n for n in notes if n])
