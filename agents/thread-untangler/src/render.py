"""Pure: the markdown reply, in fixed sections, from checked records only. Re-render views cost no call.
`mine` is computed here from the stored owner and the user's name."""

from __future__ import annotations

from collections.abc import Sequence

import prompts
from checks import clean_text
from model import NOT_STATED, Action, Untangled
from owners import is_mine

EMPTY = "None found in the thread."
VIEWS = ("default", "mine", "decisions", "questions", "reversals", "owners")


def render(result: Untangled, view: str = "default", with_sources: bool = False, my_name: str = "",
           speakers: Sequence[str] = (), extra_notes: Sequence[str] = ()) -> str:
    if view == "mine" and not my_name:
        return prompts.NAME_HINT
    sections = {
        "decisions": lambda: [_decisions(result, with_sources)],
        "questions": lambda: [_questions(result, with_sources)],
        "reversals": lambda: [_reversals(result)],
        "owners": lambda: [_by_owner(result.actions)],
        "mine": lambda: [_actions(result.actions, with_sources, my_name, speakers, only_mine=True)],
    }.get(view, lambda: [f"**TL;DR** {clean_text(result.tldr, result.tldr)}", _decisions(result, with_sources),
                         _questions(result, with_sources),
                         _actions(result.actions, with_sources, my_name, speakers)])()
    hint = [prompts.NAME_HINT] if view == "default" and not my_name and result.actions else []
    return "\n\n".join(part for part in (*sections, footer(result, extra_notes), *hint) if part)


def footer(result: Untangled, extra_notes: Sequence[str] = ()) -> str:
    lines = [f"Thread: {_n(result.messages, 'message')}, {_n(result.people, 'person', 'people')}."]
    if result.chunks > 1:
        lines.append(f"Read in {result.chunks} parts.")
    if result.dropped:
        lines.append(f"{_n(result.dropped, 'item')} left out: no supporting quote found.")
    if result.capped:
        lines.append(f"{result.capped} more not shown.")
    notes = [n for n in (*result.notes, *extra_notes) if n]
    return "\n".join([f"_({' '.join(lines)})_", *notes])


def _decisions(result: Untangled, with_sources: bool) -> str:
    if not result.decisions:
        return "**Decisions**\n" + EMPTY
    quotes = {r.decision_key: r.quote for r in result.reversals}
    lines = []
    for n, d in enumerate(result.decisions, start=1):
        label = f"{d.text} ({d.speaker}, {d.message_id})"
        if d.status == "reversed":
            line = f'{n}. ~~{label}~~ reversed in {d.reversed_in}: "{_q(quotes.get(d.key, ""))}"'
        else:
            line = f"{n}. {label}"
        lines.append(line + _source(d.quote, d.message_id, with_sources))
    return "**Decisions**\n" + "\n".join(lines)


def _questions(result: Untangled, with_sources: bool) -> str:
    lines = [f"- {q.text} (asked by {q.asked_by}, {q.message_id})" + _source(q.quote, q.message_id, with_sources)
             for q in result.questions]
    return "**Open questions**\n" + ("\n".join(lines) or EMPTY)


def _actions(actions: Sequence[Action], with_sources: bool, my_name: str, speakers: Sequence[str],
             only_mine: bool = False) -> str:
    current = [a for a in actions if a.status != "superseded"]
    if only_mine:
        current = [a for a in current if is_mine(a.owner, my_name, speakers)]
    title = "**Your action items**" if only_mine else "**Action items**" + ("  (yours marked with *)" if my_name else "")
    if not current:
        body = EMPTY
    else:
        rows = [f"| {n} | {a.owner} | {a.task} | {a.due or '-'} | {'*' if is_mine(a.owner, my_name, speakers) else ''} |"
                for n, a in enumerate(current, start=1)]
        body = "| # | Owner | Task | Due | |\n|---|---|---|---|---|\n" + "\n".join(rows)
        if with_sources:
            body += "\n\n" + "\n".join(f'{n}. {a.message_id}: "{_q(a.quote)}"' for n, a in enumerate(current, 1))
    superseded = [a for a in actions if a.status == "superseded"] if not only_mine else []
    if superseded:
        body += "\n\n_Superseded by a reversal:_\n" + "\n".join(f"- ~~{a.task}~~ ({a.owner}, {a.message_id})"
                                                              for a in superseded)
    return f"{title}\n{body}"


def _reversals(result: Untangled) -> str:
    if not result.reversals:
        return "**Reversals**\n" + EMPTY
    lines = [f'- ~~{r.decision_text}~~ ({r.decision_message}) reversed in {r.later_message_id} by {r.speaker}'
             + (f": {r.what_changed}" if r.what_changed else "") + f'\n  > "{_q(r.quote)}"'
             for r in result.reversals]
    return "**Reversals**\n" + "\n".join(lines)


def _by_owner(actions: Sequence[Action]) -> str:
    current = [a for a in actions if a.status != "superseded"]
    if not current:
        return "**Who owns what**\n" + EMPTY
    owners: dict[str, list[Action]] = {}
    for action in current:
        owners.setdefault(action.owner, []).append(action)
    ordered = sorted(owners, key=lambda o: (o == NOT_STATED, o.casefold()))
    blocks = [f"**{owner}**\n" + "\n".join(f"- {a.task}" + (f" (due: {a.due})" if a.due else "") for a in owners[owner])
              for owner in ordered]
    return "**Who owns what**\n\n" + "\n\n".join(blocks)


def _source(quote: str, message_id: str, show: bool) -> str:
    return f'\n   > "{_q(quote)}" ({message_id})' if show else ""


def _q(quote: str) -> str:
    # The quote is the user's own text, but images and HTML in it are still never rendered.
    return clean_text(quote, quote)


def _n(count: int, noun: str, plural: str = "") -> str:
    return f"{count} {noun if count == 1 else (plural or noun + 's')}"
