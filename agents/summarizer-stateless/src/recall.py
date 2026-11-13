"""Pure: what an earlier turn of this conversation still gives a follow-up, read from the platform's
history block (this agent stores nothing). Two things can be recalled:

- the most recent pasted source (a user turn with at least MIN_SOURCE_WORDS words of text);
- the most recent summary reply (an assistant turn rendered by `render.py`), cut into its sections so a view
  ("only the action items", "as a table", "who owns what") is rebuilt from it in code, with zero LLM calls.

The history is a budgeted slice, so either may be missing: callers then say so and ask for a re-paste.
Nothing here invents content: every view is made of the earlier reply's own lines."""

from __future__ import annotations

import re
from collections.abc import Sequence

import prompts
from history import ASSISTANT, USER, HistoryTurn, latest, texts
from inputs import MIN_SOURCE_WORDS, split_request, word_count
from items import NOT_STATED
from render import EMPTY

_STARTS = {"tldr": "**TL;DR**", "decisions": "**Decisions**", "actions": "**Action items**"}
_QUOTE_LINE = re.compile(r"^\s+> \"")
_NUMBERED = re.compile(r"^\d+\. (.+)$")
_LISTED_ACTION = re.compile(r"^\d+\. (.+) \(owner: (.+); due: (.+)\)$")


def source(turns: Sequence[HistoryTurn]) -> tuple[str, str]:
    """(instruction, source) of the most recent user turn that pasted a text to summarize, else ("", "")."""
    for text in texts(turns, USER):
        instruction, pasted = split_request(text)
        if word_count(pasted) >= MIN_SOURCE_WORDS:
            return prompts.strip_markers(instruction), prompts.strip_markers(pasted)
    return "", ""


def summary_reply(turns: Sequence[HistoryTurn]) -> str:
    """The most recent assistant reply that is a summary (or a section of one), else ""."""
    return latest(turns, ASSISTANT, lambda t: any(_block(t, name) for name in _STARTS))


def view_from_history(turns: Sequence[HistoryTurn], view: str, with_sources: bool) -> str | None:
    """`view` rebuilt from the most recent summary reply that has what it needs, or None."""
    for reply in texts(turns, ASSISTANT):
        shown = view_from_reply(reply, view, with_sources)
        if shown is not None:
            return shown
    return None


def view_from_reply(reply: str, view: str, with_sources: bool) -> str | None:
    if view == "table":
        return _table(reply)
    if view == "owners":
        return _owners(reply)
    names = (view,) if view in _STARTS else tuple(_STARTS)
    blocks = [_block(reply, name) for name in names]
    if not blocks[0] or (with_sources and not all('> "' in b for b in blocks if b and EMPTY not in b)):
        return None
    shown = [b if with_sources else _without_quotes(b) for b in blocks if b]
    return "\n\n".join(shown)


def _block(reply: str, name: str) -> str:
    return next((b.strip() for b in (reply or "").split("\n\n") if b.strip().startswith(_STARTS[name])), "")


def _without_quotes(block: str) -> str:
    return "\n".join(line for line in block.splitlines() if not _QUOTE_LINE.match(line))


def _decisions(reply: str) -> list[str] | None:
    block = _block(reply, "decisions")
    if not block:
        return None
    return [m.group(1) for line in block.splitlines()[1:] if (m := _NUMBERED.match(line.strip()))]


def _actions(reply: str) -> list[tuple[str, str, str]] | None:
    """(action, owner, due) rows from the actions block in either rendered form (table or listed)."""
    block = _block(reply, "actions")
    if not block:
        return None
    rows = []
    for line in block.splitlines()[1:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and len(cells) == 4 and cells[0].isdigit():
            rows.append((cells[1], cells[2], cells[3]))
        elif match := _LISTED_ACTION.match(line.strip()):
            rows.append((match.group(1), match.group(2), match.group(3)))
    return rows


def _table(reply: str) -> str | None:
    decisions, actions = _decisions(reply), _actions(reply)
    if decisions is None or actions is None:
        return None
    rows = [f"| Decision | {d} | - | - |" for d in decisions]
    rows += [f"| Action | {text} | {owner} | {due} |" for text, owner, due in actions]
    if not rows:
        return "**Decisions and action items**\n" + EMPTY
    return "**Decisions and action items**\n| Type | Item | Owner | Due |\n|---|---|---|---|\n" + "\n".join(rows)


def _owners(reply: str) -> str | None:
    actions = _actions(reply)
    if actions is None:
        return None
    if not actions:
        return "**Who owns what**\n" + EMPTY
    owners: dict[str, list[str]] = {}
    for text, owner, due in actions:
        owners.setdefault(owner or NOT_STATED, []).append(f"- {text}" + (f" (due: {due})" if due != "-" else ""))
    ordered = sorted(owners, key=lambda o: (o == NOT_STATED, o.casefold()))
    return "**Who owns what**\n\n" + "\n\n".join(f"**{o}**\n" + "\n".join(owners[o]) for o in ordered)
