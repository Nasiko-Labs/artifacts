"""Pure: reconcile the model's revised plan with the stored one. Code owns ids: a task the instruction
did not name keeps exactly its stored fields (a model that rewrote it is reverted); a task the model dropped
comes back unless the instruction removes or merges something; new tasks get fresh ids T{n+1}..."""

from __future__ import annotations

import re
from dataclasses import replace

from checks import Draft
from models import Plan

_NAMED = re.compile(r"\bT(\d+)\b", re.IGNORECASE)
_REMOVES = re.compile(r"\b(?:drop|remove|delete|merge|combine|cut|skip|without|get rid of|fold)\b", re.IGNORECASE)
_SPLITS = re.compile(r"\b(?:split|break up|break down|divide|replace)\b", re.IGNORECASE)
_FIELDS = ("title", "size", "acceptance", "owner", "source", "status")


def named_ids(instruction: str) -> set[str]:
    return {f"T{n}" for n in _NAMED.findall(instruction or "")}


def stored_task(task) -> dict:
    return {**task.model_dump(mode="json"), "depends_on": tuple(task.depends_on), "acceptance": tuple(task.acceptance)}


def reconcile(old: Plan, draft: Draft, instruction: str) -> tuple[Draft, int, tuple[str, ...]]:
    """(draft with final ids, next id number, notes)."""
    named = named_ids(instruction)
    split = named if _SPLITS.search(instruction or "") else set()
    old_by_id = {t.id: stored_task(t) for t in old.tasks}
    next_id = old.next_id
    rename: dict[str, str] = {}
    tasks: list[dict] = []
    for t in draft.tasks:
        if t["id"] in old_by_id and t["id"] not in split and t["id"] not in rename:
            rename[t["id"]] = t["id"]
        else:
            rename[t["id"]] = f"T{next_id}"
            next_id += 1
        before = old_by_id.get(rename[t["id"]])
        status = before["status"] if before and t["status"] == "pending" else t["status"]
        tasks.append({**t, "id": rename[t["id"]], "status": status})
    tasks = [{**t, "depends_on": tuple(rename.get(d, d) for d in t["depends_on"])} for t in tasks]
    remaining = {t["id"] for t in tasks if t["id"] in old_by_id}
    reverted: list[str] = []
    out: list[dict] = []
    for t in tasks:
        before = old_by_id.get(t["id"])
        if before is None or t["id"] in named or not _changed(before, t, remaining, old_by_id):
            out.append(t)
            continue
        reverted.append(t["id"])
        out.append({**before, "depends_on": _kept_deps(before, t, old_by_id)})
    present = {t["id"] for t in out}
    missing = [i for i in old_by_id if i not in present and i not in named and i not in split]
    if missing and not _REMOVES.search(instruction or ""):
        out += [old_by_id[i] for i in missing]
    notes = (f"Kept {', '.join(reverted)} as they were: the change didn't name them.",) if reverted else ()
    return replace(draft, tasks=tuple(out)), next_id, notes


def _changed(before: dict, after: dict, remaining: set[str], old_by_id: dict) -> bool:
    """A field differs, or the dependencies among stored tasks that still exist differ (wiring a new task in,
    or losing a removed one, is not a change)."""
    if any(before[f] != after[f] for f in _FIELDS):
        return True
    return {d for d in before["depends_on"] if d in remaining} != {d for d in after["depends_on"] if d in old_by_id}


def _kept_deps(before: dict, after: dict, old_by_id: dict) -> tuple[str, ...]:
    """The stored dependencies, plus any new task the model wired in (new ids are not in the stored plan)."""
    new = tuple(d for d in after["depends_on"] if d not in old_by_id and d not in before["depends_on"])
    return tuple(dict.fromkeys((*before["depends_on"], *new)))
