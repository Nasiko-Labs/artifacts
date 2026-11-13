"""Pure: what an earlier turn of this conversation still gives a follow-up, read from the platform's
history block (this agent stores nothing). Three things can be recalled:

- the most recent pasted transcript (a user turn with at least MIN_SOURCE_WORDS words of meeting text);
- the most recent notes reply (an assistant turn rendered by `render.py`), cut into its sections so a
  view ("only decisions", "only mine") is served from it in code, with zero LLM calls;
- the user's name ("I'm Raj"), best-effort.

The history is a budgeted slice, so any of these may be missing: callers then say so and ask for a
re-paste. Nothing here invents content: a view is a subset of the earlier reply's own lines."""

from __future__ import annotations

from collections.abc import Sequence

import prompts
from history import ASSISTANT, USER, HistoryTurn, latest, texts
from inputs import (
    MIN_SOURCE_WORDS,
    from_envelope,
    my_name_in,
    name_only,
    split_request,
    word_count,
)
from items import NOT_STATED

SECTION_VIEWS = ("notes", "decisions", "actions", "followups")
REPLY_VIEWS = (*SECTION_VIEWS, "default", "mine")
_STARTS = {"notes": "**Notes**", "decisions": "**Decisions**", "actions": "**Action items**",
           "followups": "**Follow-ups**"}


def source_of_text(text: str) -> tuple[str, str]:
    """(instruction, transcript) of one message's text, fence markers stripped from both."""
    enveloped = from_envelope(text)
    if enveloped is not None:
        return "", prompts.strip_markers(enveloped)
    instruction, source = split_request(text)
    return prompts.strip_markers(instruction), prompts.strip_markers(source)


def transcript(turns: Sequence[HistoryTurn]) -> tuple[str, str]:
    """(instruction, transcript) of the most recent user turn that pasted a meeting, else ("", "")."""
    for text in texts(turns, USER):
        instruction, source = source_of_text(text)
        if word_count(source) >= MIN_SOURCE_WORDS:
            return instruction, source
    return "", ""


def my_name(turns: Sequence[HistoryTurn]) -> str:
    """The most recent "I'm Raj" the user said in a short message or a request line, else ""."""
    for text in texts(turns, USER):
        instruction, source = source_of_text(text)
        said = name_only(text) or my_name_in(instruction if word_count(source) >= MIN_SOURCE_WORDS else text)
        if said:
            return said
    return ""


def notes_reply(turns: Sequence[HistoryTurn]) -> str:
    """The most recent assistant reply that is a set of meeting notes, else ""."""
    return latest(turns, ASSISTANT, lambda t: any(_block(t, view) for view in SECTION_VIEWS))


def view_from_history(turns: Sequence[HistoryTurn], view: str, name: str) -> str | None:
    """`view` cut from the most recent notes reply that has what it needs, or None."""
    if view not in REPLY_VIEWS:
        return None
    for reply in texts(turns, ASSISTANT):
        shown = view_from_reply(reply, view, name)
        if shown is not None:
            return shown
    return None


def view_from_reply(reply: str, view: str, name: str) -> str | None:
    head = _header(reply)
    if view == "default":
        return reply.strip() if head and _block(reply, "notes") else None
    if view == "mine":
        return _mine(reply, head, name)
    block = _block(reply, view)
    return "\n\n".join(p for p in (head, block) if p) if block else None


def _blocks(reply: str) -> list[str]:
    return [b.strip() for b in (reply or "").split("\n\n") if b.strip()]


def _header(reply: str) -> str:
    first = next(iter(_blocks(reply)), "")
    return first if first.startswith("**") and "Attendees:" in first else ""


def _block(reply: str, view: str) -> str:
    start = _STARTS[view]
    for block in _blocks(reply):
        if block.startswith(start) and not block.startswith("**Decisions and"):
            return block
    return ""


def _mine(reply: str, head: str, name: str) -> str | None:
    tables = [(block, kind) for view, kind in (("actions", "action"), ("followups", "follow-up"))
              if (block := _block(reply, view))]
    if not name or not tables:
        return None
    lines = [_mine_line(cells, kind) for table, kind in tables for cells in _rows(table) if _is_name(cells[1], name)]
    numbered = [f"{n}. {line}" for n, line in enumerate(lines, start=1)] or ["None recorded."]
    return "\n\n".join(p for p in (head, f"**Yours ({name})**\n" + "\n".join(numbered)) if p)


def _rows(table: str) -> list[list[str]]:
    rows = []
    for line in table.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and len(cells) >= 4 and cells[0].isdigit():
            rows.append(cells)
    return rows


def _mine_line(cells: list[str], kind: str) -> str:
    owner, task, due = cells[1].removesuffix("*").strip(), cells[2], cells[3]
    return f"{task} ({kind}; owner: {owner or NOT_STATED}; due: {due or '-'})"


def _is_name(owner_cell: str, name: str) -> bool:
    owner = owner_cell.removesuffix("*").strip().casefold()
    wanted = name.strip().casefold()
    if not owner or owner == NOT_STATED.casefold():
        return False
    return owner == wanted or owner.split()[0] == wanted.split()[0]
