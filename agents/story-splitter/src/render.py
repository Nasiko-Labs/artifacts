"""Pure: the markdown reply, rendered by code from the envelope. Model text is cleaned first: no HTML, no
images, no links, no headings or line breaks that could restyle the reply."""

from __future__ import annotations

import json
import re

_TAG = re.compile(r"<[^>\n]{0,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_SPACES = re.compile(r"\s+")
_LEADING_MARKUP = re.compile(r"^[#>*\-\s]+")
_HEADING = re.compile(r"#{1,6}(?=\s)")
TITLE_CHARS = 70


def clean(text: str) -> str:
    """One line of plain text: tags, images and link targets removed, markdown openers dropped."""
    plain = _HEADING.sub(" ", _LINK.sub(r"\1", _IMAGE.sub("", _TAG.sub("", text or ""))))
    return _LEADING_MARKUP.sub("", _SPACES.sub(" ", plain)).replace("**", "").strip()


def epic_title(epic: str) -> str:
    first = clean(re.split(r"(?<=[.!?:])\s|\n", (epic or "").strip(), maxsplit=1)[0]).rstrip(".:")
    return first if len(first) <= TITLE_CHARS else first[:TITLE_CHARS].rsplit(" ", 1)[0] + "..."


def _depends(story: dict) -> str:
    return ", ".join(str(n) for n in story["depends_on"]) or "none"


def story_block(story: dict) -> str:
    tag = "  (walking skeleton)" if story.get("walking_skeleton") else ""
    tag += "  (spike)" if story["slice_pattern"] == "spike" else ""
    lines = [f"**{story['n']}. {clean(story['title'])}**  [{story['size']}]{tag}",
             f"As a {clean(story['as_a'])}, I want to {clean(story['i_want'])}, so that {clean(story['so_that'])}."]
    lines += [f"- {clean(c)}" for c in story["acceptance"]]
    lines.append(f"Depends on: {_depends(story)}")
    lines += [f"_Note: {clean(n)}_" for n in story.get("notes") or ()]
    return "\n".join(lines)


def render(envelope: dict, epic: str, notes: tuple[str, ...] = ()) -> str:
    stories = envelope["stories"]
    start = next((s["n"] for s in stories if s.get("walking_skeleton")), 1)
    head = f"**Stories for: {epic_title(epic)}** ({len(stories)} stories; start with {start})"
    blocks = [head, *(story_block(s) for s in stories)]
    blocks += _footer(envelope)
    blocks += [n for n in notes if n]
    return "\n\n".join(blocks)


def _footer(envelope: dict) -> list[str]:
    lines: list[str] = []
    if envelope["uncovered"]:
        lines.append("**Not covered by any story:** " + "; ".join(f"\"{clean(u)}\"" for u in envelope["uncovered"]))
    if envelope["open_questions"]:
        lines.append("**Open questions:**\n" + "\n".join(f"- {clean(q)}" for q in envelope["open_questions"]))
    checks = []
    if envelope["dropped"]:
        checks.append("left out " + "; ".join(envelope["dropped"]))
    if envelope["merged"]:
        checks.append("; ".join(envelope["merged"]))
    if envelope["capped"]:
        checks.append(f"capped at {len(envelope['stories'])} stories, so these were cut: "
                      + "; ".join(f"\"{clean(t)}\"" for t in envelope["capped"]))
    if envelope["actors_replaced"]:
        checks.append(f"{envelope['actors_replaced']} story actor(s) the epic doesn't name became [user type?]")
    if envelope["dropped_criteria"]:
        checks.append(f"{envelope['dropped_criteria']} acceptance criteria used numbers or names the epic doesn't "
                      "give and were left out")
    if envelope["chunks"] > 1:
        checks.append(f"the epic was long, so I read it in {envelope['chunks']} parts and merged them")
    if checks:
        lines.append("_Checks: " + ". ".join(clean(c) for c in checks) + "._")
    review = " A person should review these before they go into the backlog." if envelope["needs_human"] else ""
    lines.append(f"_Sizes are computed from reported facts (S/M/L, not days). Confidence: "
                 f"{envelope['confidence']}.{review}_")
    return lines


def render_first(envelope: dict, chain_numbers: list[int]) -> str:
    """'Which can ship first?': the walking skeleton and its dependency chain, then the other roots."""
    by_n = {s["n"]: s for s in envelope["stories"]}
    roots = [s["n"] for s in envelope["stories"] if not s["depends_on"]]
    lines = ["**What can ship first**"]
    lines += [f"{i}. Story {n}: {clean(by_n[n]['title'])} [{by_n[n]['size']}]" for i, n in
              enumerate(chain_numbers, start=1)]
    others = [n for n in roots if n not in chain_numbers]
    if others:
        lines.append("Also independent (no dependencies): " + ", ".join(
            f"story {n} ({clean(by_n[n]['title'])})" for n in others))
    blocked = [s for s in envelope["stories"] if s["depends_on"]]
    if blocked:
        lines.append("Then, in order: " + "; ".join(f"{s['n']} after {_depends(s)}" for s in blocked))
    return "\n\n".join(lines)


def render_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, indent=2, ensure_ascii=False) + "\n```"
