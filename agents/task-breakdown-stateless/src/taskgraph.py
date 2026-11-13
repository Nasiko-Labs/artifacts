"""Pure graph maths over the task dependencies. The model never orders, counts or adds: this module does.

`deps` maps each task id to the ids whose output it needs. Effort units come from the size label
(S/M/L/XL = 1/2/3/5): relative units, never days."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

UNITS = {"S": 1, "M": 2, "L": 3, "XL": 5}
_ID = re.compile(r"T(\d+)$")

Deps = Mapping[str, Sequence[str]]


def id_key(task_id: str) -> tuple:
    match = _ID.match(task_id)
    return (0, int(match.group(1)), task_id) if match else (1, 0, task_id)


def ordered(ids) -> list[str]:
    return sorted(ids, key=id_key)


def find_cycle(deps: Deps) -> list[str] | None:
    """A cycle as a path [a, b, ..., a] (a needs b needs ... needs a), or None. Deterministic: ids in order."""
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for dep in ordered(d for d in deps.get(node, ()) if d in deps):
            if state.get(dep) == 1:
                return [*stack[stack.index(dep):], dep]
            if dep not in state:
                found = visit(dep)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in ordered(deps):
        if node not in state:
            found = visit(node)
            if found:
                return found
    return None


def topo(deps: Deps) -> list[str]:
    """Kahn's algorithm, lowest id first among the ready. Assumes no cycle."""
    remaining = {n: {d for d in deps[n] if d in deps} for n in deps}
    out: list[str] = []
    while remaining:
        ready = ordered(n for n, need in remaining.items() if not need)
        if not ready:
            raise ValueError("cycle")
        for node in ready:
            out.append(node)
            del remaining[node]
        for need in remaining.values():
            need.difference_update(ready)
    return out


def waves(deps: Deps) -> dict[str, int]:
    """Wave k = tasks whose dependencies are all in waves < k. Wave 1 can start now."""
    level: dict[str, int] = {}
    for node in topo(deps):
        level[node] = 1 + max((level[d] for d in deps[node] if d in deps), default=0)
    return level


def wave_lists(deps: Deps) -> list[list[str]]:
    level = waves(deps)
    return [ordered(n for n, w in level.items() if w == k) for k in range(1, max(level.values(), default=0) + 1)]


def critical_path(deps: Deps, units: Mapping[str, int]) -> tuple[list[str], int]:
    """The longest chain by summed units; ties go to the lowest id. Returns (chain start->end, units)."""
    best: dict[str, int] = {}
    prev: dict[str, str | None] = {}
    for node in topo(deps):
        inputs = ordered(d for d in deps[node] if d in deps)
        pick = min(inputs, key=lambda d: (-best[d], id_key(d)), default=None)
        prev[node] = pick
        best[node] = units[node] + (best[pick] if pick else 0)
    if not best:
        return [], 0
    end = min(best, key=lambda n: (-best[n], id_key(n)))
    chain = [end]
    while prev[chain[-1]]:
        chain.append(prev[chain[-1]])
    return chain[::-1], best[end]


def dependents(deps: Deps, task_id: str) -> tuple[list[str], list[str]]:
    """(direct dependents, every task downstream) of one task."""
    direct = ordered(n for n, need in deps.items() if task_id in need)
    seen: set[str] = set()
    frontier = list(direct)
    while frontier:
        node = frontier.pop()
        if node not in seen:
            seen.add(node)
            frontier.extend(n for n, need in deps.items() if node in need)
    return direct, ordered(seen)


def lanes(deps: Deps, units: Mapping[str, int], people: int) -> tuple[list[list[str]], int]:
    """Greedy list scheduling for N people by critical-path priority. Returns (per-lane tasks, makespan units)."""
    priority = _bottom_levels(deps, units)
    lane_free = [0] * max(1, people)
    plan: list[list[str]] = [[] for _ in lane_free]
    finish: dict[str, int] = {}
    now = 0
    left = set(deps)
    while left:
        ready = sorted((n for n in left if all(finish.get(d, now + 1) <= now for d in deps[n] if d in deps)),
                       key=lambda n: (-priority[n], id_key(n)))
        free = [i for i, t in enumerate(lane_free) if t <= now]
        for lane, node in zip(free, ready, strict=False):
            plan[lane].append(node)
            finish[node] = now + units[node]
            lane_free[lane] = finish[node]
            left.discard(node)
        future = [t for t in (*lane_free, *finish.values()) if t > now]
        if left and not future:
            raise ValueError("cycle")
        now = min(future) if future else now
    return plan, max(finish.values(), default=0)


def _bottom_levels(deps: Deps, units: Mapping[str, int]) -> dict[str, int]:
    level: dict[str, int] = {}
    for node in reversed(topo(deps)):
        children = [n for n, need in deps.items() if node in need]
        level[node] = units[node] + max((level[c] for c in children), default=0)
    return level
