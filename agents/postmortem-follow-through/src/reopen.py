"""Preparing reopens. Only items closed without evidence are offered; won't-do items are never reopened
(decision 2026-11-05), "closed (not verified)" ones can't be shown to lack evidence, and open ones are
already open. The transition is chosen in code from what the tracker lists for that ticket; the comment
is built in code. The approval covers exactly two writes on that one ticket."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import step_prompts
import table_render
from actions import Action
from assess import Row, status_category
from corpus import Transition, transitions_in
from deps import TurnDeps
from discovery import Catalog, discover
from records import TicketStatus, text_of
from send_guard import WriteSpec
from steps import run_step
from tool_health import ToolHealth

log = logging.getLogger("postmortem.reopen")

MAX_REOPENS = 5
TRANSITION_CALLS = 2
TRANSITION_SECS = 45.0
FINAL = frozenset({"reopened", "denied"})
_PREFERRED = re.compile(r"reopen|to ?do|\bopen\b|backlog", re.IGNORECASE)
NO_TRANSITION_TOOL = "No tracker tool can change a ticket's status for this agent, so I can't reopen anything."
_CATEGORY_KEYS = {"new": "todo", "indeterminate": "in_progress", "done": "done",
                  "todo": "todo", "in_progress": "in_progress"}


@dataclass(frozen=True)
class Prepared:
    actions: tuple[Action, ...] = ()
    notes: tuple[str, ...] = ()


def refusal(row: Row, outcomes: Mapping[str, str]) -> str | None:
    """Why this row can't be reopened, or None."""
    if outcomes.get(f"reopen:{row.key}") in FINAL:
        return f"#{row.n} ({row.key}) was already handled here ({outcomes[f'reopen:{row.key}']}), so I won't again."
    if "no_evidence" in row.flags:
        return None
    if "not_verified" in row.flags:
        return (f"#{row.n} ({row.key}) is closed, but I couldn't read its comments to show it was closed without "
                "work, so I won't reopen it.")
    return f"#{row.n} ({row.key}) is still open, so there is nothing to reopen."


def pick_transition(transitions: Sequence[Transition]) -> Transition | None:
    """A transition into a not-done status, preferring one named like reopen / to do / open / backlog."""
    def category(t: Transition) -> str:
        known = _CATEGORY_KEYS.get(t.category_key.casefold().replace(" ", "_"))
        return known or status_category(TicketStatus(key="", status=t.to_status, created_at="", updated_at=""))
    usable = [t for t in transitions if category(t) in ("todo", "in_progress")]
    preferred = [t for t in usable if _PREFERRED.search(t.name) or _PREFERRED.search(t.to_status)]
    return (preferred or usable or [None])[0]


async def prepare(deps: TurnDeps, rows: Sequence[Row], outcomes: Mapping[str, str]) -> Prepared:
    notes = [n for n in (refusal(r, outcomes) for r in rows) if n]
    wanted = [r for r in rows if refusal(r, outcomes) is None]
    if len(wanted) > MAX_REOPENS:
        notes.append(f"I'll reopen {MAX_REOPENS} tickets per turn; ask again for the rest.")
        wanted = wanted[:MAX_REOPENS]
    if not wanted:
        return Prepared((), tuple(notes))
    catalog, failure = await discover(deps, ("transitions", "reopen"))
    if failure is not None:
        return Prepared((), (*notes, failure))
    transition_tool, comment_tool = catalog.pick("reopen", "transition"), catalog.pick("reopen", "comment")
    if transition_tool is None:
        return Prepared((), (*notes, NO_TRANSITION_TOOL))
    actions = []
    for row in wanted:
        chosen = await _transition(deps, catalog, row.key)
        if chosen is None:
            notes.append(f"No reopen transition is available on {row.key}.")
            continue
        actions.append(_action(row, chosen, transition_tool.name, comment_tool.name if comment_tool else None))
    if actions and comment_tool is None:
        notes.append("No tracker tool can add a comment for this agent, so the reopen comment is shown to copy.")
    return Prepared(tuple(actions), tuple(notes))


async def _transition(deps: TurnDeps, catalog: Catalog, key: str) -> Transition | None:
    tools = catalog.tools_for("transitions")
    if not tools:
        return None
    result = await run_step(deps, tools, ToolHealth(deps.gateway), step_prompts.transitions_system(key),
                            f"List the transitions of {key}.", TRANSITION_CALLS, TRANSITION_SECS)
    found = transitions_in(r for r in result.fenced.records if r.mentions(key))
    if not found:  # no transitions list by shape: the model's answer, each value checked verbatim
        rows = (result.data or {}).get("transitions")
        found = tuple(Transition(text_of(t.get("id")), text_of(t.get("name")), text_of(t.get("to_status")) or
                                 text_of(t.get("name")), "")
                      for t in (rows if isinstance(rows, list) else []) if isinstance(t, dict)
                      and result.corpus.contains(t.get("id")) and result.corpus.contains(t.get("name")))
    return pick_transition(found)


def _action(row: Row, transition: Transition, transition_tool: str, comment_tool: str | None) -> Action:
    writes = (WriteSpec("transition", transition_tool, (row.key, transition.id), None),)
    if comment_tool:
        writes = (*writes, WriteSpec("comment", comment_tool, (row.key,), None))
    tools = ", ".join(t for t in (transition_tool, comment_tool) if t)
    details = (f"{row.key} {row.text}\nStatus: {row.status} → {transition.to_status} "
               f"(transition \"{transition.name}\")\nWhy: {row.state}, from {row.pm_label}\nVia: {tools}")
    return Action(kind="reopen", ref=row.key, who=row.key, details=details,
                  editable=table_render.reopen_comment(row), fixed="", writes=writes, row_keys=(row.key,))
