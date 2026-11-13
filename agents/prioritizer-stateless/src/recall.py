"""Pure: the earlier ranking, re-read from the conversation history (this agent keeps no state of its own).

Newest turn first, the first of these wins:
- an assistant reply holding a full ranked table ("**Ranked (weights: ...)**"): tasks, impact, effort,
  delegation, goals, weights, pins and as-of date are read back from it, so a re-rank needs no model call;
- a user message holding a list of 2+ tasks: the tasks and goals, which must be rated again (`rated` False).
A partial view ("Top 3 of 9", the numbers, JSON) is skipped. Nothing found (history trimmed, compressed or
absent) gives None, and the caller asks for the list again; nothing is ever guessed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import history
import scoring
import tasks as tasks_mod
from history import Turn

MIN_TASKS = 2

_TITLE = re.compile(r"^\*\*Ranked \(weights: (impact \d+, urgency \d+, effort \d+)\)\*\*(?: goals: (.*))?$",
                    re.MULTILINE)
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_IMPACT = re.compile(r"Impact ([1-5]): (.*?)\. (?:Due |Blocking, so|No deadline, so)")
_EFFORT = re.compile(r"Effort ([1-5])(?: \(your estimate\))?\.\s*$")
_DELEGATE = re.compile(r"Delegate to (.+?)\?")
_AS_OF = re.compile(r"_Urgency as of (\d{4}-\d{2}-\d{2})\._")
_DEFAULTED = re.compile(r"_I couldn't rate (\d+) task")
_PINNED = "(pinned by you)"
_SOMEONE = "someone else"


def latest(turns: Sequence[Turn]) -> dict | None:
    """The newest ranking or task list in the history as stored state, or None."""
    return history.newest(turns, _read)


def _read(turn: Turn) -> dict | None:
    return from_table(turn.text) if turn.role == history.ASSISTANT else from_paste(turn.text)


def from_paste(text: str) -> dict | None:
    parsed = tasks_mod.parse(text)
    if len(parsed.tasks) < MIN_TASKS:
        return None
    kept = parsed.tasks[:tasks_mod.MAX_TASKS]
    task_list = [{"id": f"t{n}", "text": t} for n, t in enumerate(kept, start=1)]
    return {"tasks": task_list, "ratings": {}, "goals": list(parsed.goals), "preset": "default", "pins": [],
            "as_of": "", "defaulted": 0, "failed": 0, "instruction": parsed.instruction, "rated": False}


def from_table(text: str) -> dict | None:
    title = _TITLE.search(text or "")
    if title is None:
        return None
    rows = [r for r in (_row(line) for line in text[title.end():].splitlines()) if r is not None]
    if len(rows) < MIN_TASKS:
        return None
    tasks, ratings, pinned = [], {}, []
    for n, (task_text, rating, is_pinned) in enumerate(rows, start=1):
        task_id = f"t{n}"
        tasks.append({"id": task_id, "text": task_text})
        ratings[task_id] = rating
        if is_pinned:
            pinned.append(task_id)
    goals = [_unescape(g).strip() for g in (title.group(2) or "").split("; ") if g.strip()]
    as_of = _AS_OF.search(text)
    defaulted = _DEFAULTED.search(text)
    return {"tasks": tasks, "ratings": ratings, "goals": goals, "preset": _preset(title.group(1)),
            "pins": list(reversed(pinned)), "as_of": as_of.group(1) if as_of else "",
            "defaulted": int(defaulted.group(1)) if defaulted else 0,
            "failed": int("One rating call failed" in text), "instruction": "", "rated": True}


def _row(line: str) -> tuple[str, dict, bool] | None:
    cells = [c.strip() for c in _CELL_SPLIT.split(line.strip())]
    if len(cells) != 6 or cells[0] or cells[-1] or not cells[1].isdigit() or not cells[3].isdigit():
        return None
    why_cell = cells[4]
    impact, effort = _IMPACT.search(why_cell), _EFFORT.search(why_cell)
    if impact is None or effort is None:
        return None
    delegate = _DELEGATE.search(why_cell)
    target = _unescape(delegate.group(1)) if delegate else None
    rating = {"impact": int(impact.group(1)), "impact_why": _unescape(impact.group(2)),
              "effort": int(effort.group(1)), "delegable": delegate is not None,
              "delegate_to": None if target in (None, _SOMEONE) else target, "defaulted": False}
    return _unescape(cells[2]), rating, why_cell.startswith(_PINNED)


def _preset(label: str) -> str:
    return next((name for name in scoring.PRESETS if scoring.label(name) == label), scoring.DEFAULT)


def _unescape(text: str) -> str:
    return text.replace("\\|", "|")
