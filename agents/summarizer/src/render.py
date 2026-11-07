"""Pure: the reply, in fixed sections, from checked items only. Re-render views cost no LLM call."""

from __future__ import annotations

from collections.abc import Sequence

from checks import clean_text
from items import NOT_STATED, Item, Summary

EMPTY = "None recorded in the text."
VIEWS = ("default", "actions", "decisions", "tldr", "table", "owners")


def render(summary: Summary, view: str = "default", with_sources: bool = False, words: int = 0, parts: int = 1,
           extra_notes: Sequence[str] = ()) -> str:
    show_sources = with_sources
    if view == "table":
        body = [_combined_table(summary)]
    elif view == "owners":
        body = [_by_owner(summary.actions)]
    else:
        wanted = {"actions": ("actions",), "decisions": ("decisions",), "tldr": ("tldr",)}.get(
            view, ("tldr", "decisions", "actions"))
        body = [_section(name, summary, show_sources) for name in wanted]
    return "\n\n".join(part for part in (*body, footer(summary, words, parts, extra_notes)) if part)


def footer(summary: Summary, words: int, parts: int, extra_notes: Sequence[str] = ()) -> str:
    lines = []
    if words:
        unit = "part" if parts == 1 else "parts"
        lines.append(f"Summarized {words:,} words in {parts} {unit}.")
    if summary.dropped:
        lines.append(f"{_count(summary.dropped)} left out because I couldn't find them in your text.")
    if summary.capped:
        lines.append(f"{_count(summary.capped)} more left out to keep the lists short.")
    meta = f"_({' '.join(lines)})_" if lines else ""
    notes = [n for n in (*summary.notes, *extra_notes) if n]
    return "\n".join(part for part in (meta, *notes) if part)


def _count(n: int) -> str:
    return f"{n} item" if n == 1 else f"{n} items"


def _section(name: str, summary: Summary, show_sources: bool) -> str:
    if name == "tldr":
        lines = [f"- {i.text}" + _quote(i, show_sources) for i in summary.tldr]
        return "**TL;DR**\n" + ("\n".join(lines) or EMPTY)
    if name == "decisions":
        lines = [f"{n}. {i.text}" + _quote(i, show_sources) for n, i in enumerate(summary.decisions, start=1)]
        return "**Decisions**\n" + ("\n".join(lines) or EMPTY)
    if not summary.actions:
        return "**Action items**\n" + EMPTY
    if show_sources:
        lines = [f"{n}. {i.text} (owner: {i.owner or NOT_STATED}; due: {i.due or '-'})" + _quote(i, True)
                 for n, i in enumerate(summary.actions, start=1)]
        return "**Action items**\n" + "\n".join(lines)
    rows = [f"| {n} | {i.text} | {i.owner or NOT_STATED} | {i.due or '-'} |"
            for n, i in enumerate(summary.actions, start=1)]
    return "**Action items**\n| # | Action | Owner | Due |\n|---|---|---|---|\n" + "\n".join(rows)


def _quote(item: Item, show: bool) -> str:
    # The quote is the user's own text, but images and HTML in it are still never rendered.
    return f'\n  > "{clean_text(item.snippet, item.snippet)}"' if show else ""


def _combined_table(summary: Summary) -> str:
    rows = [f"| Decision | {i.text} | - | - |" for i in summary.decisions]
    rows += [f"| Action | {i.text} | {i.owner or NOT_STATED} | {i.due or '-'} |" for i in summary.actions]
    if not rows:
        return "**Decisions and action items**\n" + EMPTY
    return "**Decisions and action items**\n| Type | Item | Owner | Due |\n|---|---|---|---|\n" + "\n".join(rows)


def _by_owner(actions: Sequence[Item]) -> str:
    if not actions:
        return "**Who owns what**\n" + EMPTY
    owners: dict[str, list[Item]] = {}
    for item in actions:
        owners.setdefault(item.owner or NOT_STATED, []).append(item)
    ordered = sorted(owners, key=lambda o: (o == NOT_STATED, o.casefold()))
    blocks = [f"**{owner}**\n" + "\n".join(f"- {i.text}" + (f" (due: {i.due})" if i.due else "") for i in owners[owner])
              for owner in ordered]
    return "**Who owns what**\n\n" + "\n\n".join(blocks)
