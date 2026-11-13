"""Pure: turn a (repaired or not) draft into the checked Plan: phases renumbered 1..k, task ids assigned in
code, code fixes applied, assumptions kept only when tied to the user's own words, risks tied to tasks,
constraints checked against the text, and the flags confidence is computed from."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import fixes
from checks import MAX_ASSUMPTIONS, MAX_RISKS, Draft, policy_expects_task, two_verbs
from grounding import is_verbatim, mostly_grounded, norm
from models import Assumption, Phase, Plan, Risk, Task
from taskgraph import id_key


@dataclass(frozen=True)
class Context:
    goal: str
    source: str                       # the user's words: goal + spec (+ revision instructions)
    band: tuple[int, int]
    policy: tuple[str, ...] = ()
    renumber: bool = True             # a new plan: ids T1..Tn in code; a revision keeps its reconciled ids
    next_id: int = 1
    known_constraints: tuple[str, ...] = ()
    deadline: str = ""
    team_size: int | None = None
    rate: int | None = None
    chunks: int = 0
    vague_used: bool = False
    notes: tuple[str, ...] = ()


def finalize(draft: Draft, ctx: Context) -> Plan:
    phases, tasks = _phases(draft)
    tasks, mapping = _ids(tasks, ctx)
    fixed = fixes.apply_all(tuple(tasks), ctx.source, ctx.band[1])
    # "policy" means an org rule required the task: with no org rules the model's label is wrong (and would
    # shield the task from being deferred to fit a timebox).
    plan_tasks = tuple(Task(**{**t, "source": t["source"] if ctx.policy or t["source"] != "policy" else "derived"})
                       for t in fixed.tasks)
    used = sorted({t.phase for t in plan_tasks})
    phase_rows = tuple(Phase(number=n, name=name) for n, name in phases if n in used)
    assumptions, dropped = _assumptions(draft, ctx.source)
    notes = list(ctx.notes) + list(fixed.notes)
    notes += [f"{t.id} is still XL: split it when you can." for t in plan_tasks if t.size == "XL"]
    notes += [f"{t.id} may hold two deliverables ({t.title})." for t in plan_tasks if two_verbs(t.title)]
    if dropped:
        notes.append(f"Left out {dropped} assumption(s) not tied to your own words.")
    if len(plan_tasks) < ctx.band[0]:
        notes.append(f"{len(plan_tasks)} tasks, fewer than the usual {ctx.band[0]} for this level of detail.")
    policy_missing = policy_expects_task(ctx.policy) and not any(t.source == "policy" for t in plan_tasks)
    if policy_missing:
        notes.append("Your organization's rules ask for a task I could not place: add it by hand.")
    next_id = max([ctx.next_id, *(id_key(t.id)[1] + 1 for t in plan_tasks)])
    return Plan(goal=ctx.goal, constraints=_constraints(draft, ctx), phases=phase_rows, tasks=plan_tasks,
                risks=_risks(draft, mapping, {t.id for t in plan_tasks}), assumptions=assumptions,
                notes=tuple(dict.fromkeys(notes)), deadline=ctx.deadline, team_size=ctx.team_size, rate=ctx.rate,
                next_id=next_id, code_fixes=fixed.fixes, cycle_broken=fixed.cycle_broken, clamped=fixed.clamped,
                chunks=ctx.chunks, vague_used=ctx.vague_used, policy_missing=policy_missing, source=ctx.source)


def _phases(draft: Draft) -> tuple[list[tuple[int, str]], list[dict]]:
    """Phases with tasks, renumbered 1..k in their given order; a task in an unknown phase joins the last."""
    named = dict(draft.phases)
    numbers = sorted({*named, *(t["phase"] for t in draft.tasks)})
    last = max(named) if named else (numbers[-1] if numbers else 1)
    tasks = [t if t["phase"] in named or not named else {**t, "phase": last} for t in draft.tasks]
    used = sorted({t["phase"] for t in tasks})
    renumber = {old: i + 1 for i, old in enumerate(used)}
    phases = [(renumber[old], named.get(old) or f"Phase {renumber[old]}") for old in used]
    return phases, [{**t, "phase": renumber[t["phase"]]} for t in tasks]


def _ids(tasks: list[dict], ctx: Context) -> tuple[list[dict], dict[str, str]]:
    if not ctx.renumber:
        return tasks, {t["id"]: t["id"] for t in tasks}
    ordered = sorted(enumerate(tasks), key=lambda pair: (pair[1]["phase"], pair[0]))
    mapping = {t["id"]: f"T{i + 1}" for i, (_, t) in enumerate(ordered)}
    out = [{**t, "id": mapping[t["id"]], "depends_on": tuple(mapping.get(d, d) for d in t["depends_on"])}
           for _, t in ordered]
    return out, mapping


def _assumptions(draft: Draft, source: str) -> tuple[tuple[Assumption, ...], int]:
    kept = [a for a in draft.assumptions if is_verbatim(a["based_on"], source)]
    rows = tuple(Assumption(id=f"A{i + 1}", text=a["text"], based_on=a["based_on"])
                 for i, a in enumerate(kept[:MAX_ASSUMPTIONS]))
    return rows, len(draft.assumptions) - len(kept)


def _risks(draft: Draft, mapping: dict[str, str], ids: set[str]) -> tuple[Risk, ...]:
    kept = [(mapping.get(r["task"], r["task"]), r["text"]) for r in draft.risks]
    kept = [(task, text) for task, text in kept if task in ids]
    return tuple(Risk(id=f"R{i + 1}", task=task, text=text) for i, (task, text) in enumerate(kept[:MAX_RISKS]))


def _constraints(draft: Draft, ctx: Context) -> tuple[str, ...]:
    own: Sequence[str] = [c for c in draft.constraints if mostly_grounded(c, ctx.source)]
    code = [*ctx.known_constraints]
    if ctx.team_size:
        code.append(f"Team of {ctx.team_size}")
    if ctx.deadline:
        code.append(f"Deadline: {ctx.deadline} (as you wrote it)")
    unique: dict[str, str] = {}
    for text in (*code, *own):
        unique.setdefault(norm(text), text)
    return tuple(unique.values())
