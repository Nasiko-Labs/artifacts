"""The zero-call side of a turn: recompute the ranking from stored tasks + ratings, apply a follow-up command
(preset, pin, as-of) to the stored state, and render the asked-for view (or the envelope as JSON)."""

from __future__ import annotations

from datetime import date
from typing import Any

import envelope
import ranking
import render
from checks import content_tokens
from followup import Command

VIEWS = ("top", "actions", "quick", "numbers", "json")


def as_of_of(stored: dict, scope: Any) -> date:
    explicit = stored.get("as_of")
    return date.fromisoformat(explicit) if explicit else scope.deps.today()


def rows_of(stored: dict, scope: Any) -> list[ranking.Row]:
    return ranking.compute(stored.get("tasks") or [], stored.get("ratings") or {}, stored.get("goals") or [],
                           stored.get("preset") or "default", stored.get("pins") or [], as_of_of(stored, scope))


def _notes(stored: dict, extra: tuple[str, ...]) -> list[str]:
    notes = list(extra)
    defaulted = stored.get("defaulted") or 0
    if defaulted:
        verb = "it uses" if defaulted == 1 else "they use"
        notes.append(f"_I couldn't rate {defaulted} task{'s' if defaulted != 1 else ''}, so {verb} impact 3 and "
                     "effort 3._")
    if stored.get("failed"):
        notes.append("_One rating call failed, so part of the list uses impact 3 and effort 3; say \"rate again\"._")
    if stored.get("as_of"):
        notes.append(f"_Urgency as of {stored['as_of']}._")
    return notes


def result(stored: dict, scope: Any, notes: tuple[str, ...] = (), view: tuple[str, int] = ("default", 0)) -> dict:
    rows = rows_of(stored, scope)
    goals = stored.get("goals") or []
    env = envelope.build(rows, goals, stored.get("defaulted") or 0, stored.get("failed") or 0)
    kind, number = view
    shown = env
    if kind == "top":
        top = max(1, min(number, len(rows)))
        shown = {**env, "ranked": env["ranked"][:top], "items": env["ranked"][:top]}
    if kind == "json" or scope.incoming.wants_json:
        return {"reply": render.render_json(shown), "data": shown}
    preset = stored.get("preset") or "default"
    if kind == "numbers":
        reply = render.render_numbers(rows, preset)
    elif kind == "actions":
        reply = render.render_actions(rows)
    elif kind == "quick":
        reply = render.render_quick_wins(rows)
    else:
        top = max(1, min(number, len(rows))) if kind == "top" else None
        reply = render.render(rows, env, preset, goals, _notes(stored, notes), top=top)
    return {"reply": reply, "data": shown}


def view_of(command: Command) -> tuple[str, int]:
    return (command.kind, command.number) if command.kind in VIEWS else ("default", 0)


def apply(state: dict, command: Command, scope: Any) -> dict:
    """The state change a command makes, or {"reply": ...} when it can't be applied."""
    if command.kind == "preset":
        return {"preset": command.text}
    if command.kind == "unpin":
        return {"pins": []}
    if command.kind == "as_of":
        return {"as_of": command.text}
    if command.kind in ("pin", "pin_name"):
        rows = rows_of(state, scope)
        row = _by_number(rows, command.number) if command.kind == "pin" else _by_name(rows, command.text)
        if row is None:
            return {"reply": _no_such(rows, command)}
        pins = [p for p in state.get("pins") or [] if p != row.id]
        return {"pins": [*pins, row.id]}
    return {}


def _by_number(rows: list[ranking.Row], number: int) -> ranking.Row | None:
    return rows[number - 1] if 1 <= number <= len(rows) else None


def _by_name(rows: list[ranking.Row], name: str) -> ranking.Row | None:
    wanted = content_tokens(name)
    scored = sorted(((len(wanted & content_tokens(r.text)), r.rank, r) for r in rows), key=lambda s: (-s[0], s[1]))
    if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return scored[0][2]


def _no_such(rows: list[ranking.Row], command: Command) -> str:
    if command.kind == "pin":
        return f"There's no #{command.number}; this list has {len(rows)} tasks."
    return "I couldn't tell which task you mean. Say its number, like \"move 3 up\"."
