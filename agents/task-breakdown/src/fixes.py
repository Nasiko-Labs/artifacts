"""Pure: what code repairs when the one repair call did not. Each fix makes new task dicts (never edits
in place) and leaves a note the reply shows. Tasks here already carry their final ids."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from itertools import pairwise

from checks import MAX_ACCEPTANCE, MAX_TITLE_CHARS
from grounding import is_verbatim, norm
from models import OWNER_PLACEHOLDER
from taskgraph import UNITS, find_cycle, id_key

DEFAULT_SIZE = "M"
WHEN_PLACEHOLDER = "[when?]"
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_TIME = re.compile(rf"\b(?:\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}(?:\s+\d{{4}})?|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?"
                   r"(?:,?\s+\d{4})?|\d{4}-\d{2}-\d{2}|(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
                   r"(?:\d+|a|one|two|three|four|five|six|half an?)[- ](?:hours?|days?|weeks?|months?|sprints?))\b",
                   re.IGNORECASE)


@dataclass(frozen=True)
class Fixed:
    tasks: tuple[dict, ...]
    notes: tuple[str, ...] = ()
    fixes: int = 0
    cycle_broken: bool = False
    clamped: bool = False

    def add(self, tasks, note: str, **flags) -> Fixed:
        return replace(self, tasks=tuple(tasks), notes=(*self.notes, note), fixes=self.fixes + 1, **flags)


def apply_all(tasks: tuple[dict, ...], source: str, max_tasks: int) -> Fixed:
    fixed = Fixed(tasks)
    for step in (_dedupe, _sizes, _acceptance, _titles, _owners, _times, _dangling):
        fixed = step(fixed, source)
    return _break_cycles(_clamp(_break_cycles(fixed), max_tasks))


def _redirect(tasks, gone: str, keep: str | None) -> list[dict]:
    out = []
    for t in tasks:
        deps = [keep if d == gone else d for d in t["depends_on"] if (keep if d == gone else d) not in (None, t["id"])]
        out.append({**t, "depends_on": tuple(dict.fromkeys(deps))})
    return out


def _dedupe(fixed: Fixed, source: str) -> Fixed:
    seen: dict[str, str] = {}
    for t in fixed.tasks:
        key = norm(t["title"])
        if key in seen:
            kept = [x for x in fixed.tasks if x["id"] != t["id"]]
            return _dedupe(fixed.add(_redirect(kept, t["id"], seen[key]),
                                     f"{t['id']} repeated {seen[key]} and was merged into it."), source)
        seen[key] = t["id"]
    return fixed


def _sizes(fixed: Fixed, source: str) -> Fixed:
    bad = [t["id"] for t in fixed.tasks if t["size"] not in UNITS]
    if not bad:
        return fixed
    tasks = [{**t, "size": DEFAULT_SIZE} if t["id"] in bad else t for t in fixed.tasks]
    return fixed.add(tasks, f"{', '.join(bad)}: no valid size, set to {DEFAULT_SIZE}.")


def _acceptance(fixed: Fixed, source: str) -> Fixed:
    empty = [t["id"] for t in fixed.tasks if not t["acceptance"]]
    long = [t["id"] for t in fixed.tasks if len(t["acceptance"]) > MAX_ACCEPTANCE]
    tasks = [{**t, "acceptance": t["acceptance"][:MAX_ACCEPTANCE]} for t in fixed.tasks if t["id"] not in empty]
    for gone in empty:
        tasks = _redirect(tasks, gone, None)
    if long:
        fixed = fixed.add(tasks, f"{', '.join(long)}: kept the first {MAX_ACCEPTANCE} acceptance criteria.")
    if empty:
        fixed = fixed.add(tasks, f"{', '.join(empty)}: removed, no acceptance criteria.")
    return fixed


def _titles(fixed: Fixed, source: str) -> Fixed:
    long = [t["id"] for t in fixed.tasks if len(t["title"]) > MAX_TITLE_CHARS]
    if not long:
        return fixed
    tasks = [{**t, "title": t["title"][:MAX_TITLE_CHARS - 1].rstrip() + "…"} if t["id"] in long else t
             for t in fixed.tasks]
    return fixed.add(tasks, f"{', '.join(long)}: title shortened to {MAX_TITLE_CHARS} characters.")


def _owners(fixed: Fixed, source: str) -> Fixed:
    bad = [t["id"] for t in fixed.tasks if t["owner"] != OWNER_PLACEHOLDER and not is_verbatim(t["owner"], source)]
    if not bad:
        return fixed
    tasks = [{**t, "owner": OWNER_PLACEHOLDER} if t["id"] in bad else t for t in fixed.tasks]
    return fixed.add(tasks, f"{', '.join(bad)}: owner you didn't name replaced with {OWNER_PLACEHOLDER}.")


def strip_times(text: str, source: str) -> str:
    """Dates and calendar durations the user did not write are replaced with a placeholder."""
    return _TIME.sub(lambda m: m.group(0) if is_verbatim(m.group(0), source) else WHEN_PLACEHOLDER, text)


def _times(fixed: Fixed, source: str) -> Fixed:
    tasks = [{**t, "title": strip_times(t["title"], source),
              "acceptance": tuple(strip_times(a, source) for a in t["acceptance"])} for t in fixed.tasks]
    changed = [t["id"] for t, old in zip(tasks, fixed.tasks, strict=True) if t != old]
    return fixed.add(tasks, f"{', '.join(changed)}: removed dates or durations you didn't give.") if changed else fixed


def _dangling(fixed: Fixed, source: str) -> Fixed:
    ids = {t["id"] for t in fixed.tasks}
    dropped = [(t["id"], d) for t in fixed.tasks for d in t["depends_on"] if d not in ids or d == t["id"]]
    if not dropped:
        return fixed
    tasks = [{**t, "depends_on": tuple(d for d in t["depends_on"] if d in ids and d != t["id"])} for t in fixed.tasks]
    edges = ", ".join(f"{a} needs {b}" for a, b in dropped)
    return fixed.add(tasks, f"Dropped dependencies on tasks that don't exist ({edges}).")


def _break_cycles(fixed: Fixed) -> Fixed:
    units = {t["id"]: UNITS[t["size"]] for t in fixed.tasks}
    cycle = find_cycle({t["id"]: t["depends_on"] for t in fixed.tasks})
    if not cycle:
        return fixed
    edges = list(pairwise(cycle))
    needer, needed = min(edges, key=lambda e: (units[e[1]], id_key(e[1])))
    tasks = [{**t, "depends_on": tuple(d for d in t["depends_on"] if d != needed)} if t["id"] == needer else t
             for t in fixed.tasks]
    note = f"Dependency loop {' -> '.join(cycle)}: I removed '{needer} needs {needed}'. Check that order."
    return _break_cycles(fixed.add(tasks, note, cycle_broken=True))


def _clamp(fixed: Fixed, max_tasks: int) -> Fixed:
    if len(fixed.tasks) <= max_tasks:
        return fixed
    by_phase: dict[int, list[dict]] = {}
    for t in fixed.tasks:
        by_phase.setdefault(t["phase"], []).append(t)
    phase = min(by_phase, key=lambda p: (-len(by_phase[p]), p))
    if len(by_phase[phase]) < 2:
        return fixed
    keep, gone = by_phase[phase][-2], by_phase[phase][-1]
    total = UNITS[keep["size"]] + UNITS[gone["size"]]
    merged = {**keep, "size": next((s for s, u in UNITS.items() if u >= total), "XL"),
              "acceptance": (keep["acceptance"] + gone["acceptance"])[:MAX_ACCEPTANCE],
              "depends_on": tuple(dict.fromkeys(d for d in keep["depends_on"] + gone["depends_on"]
                                                if d not in (keep["id"], gone["id"])))}
    tasks = [merged if t["id"] == keep["id"] else t for t in fixed.tasks if t["id"] != gone["id"]]
    note = f"{gone['id']} ({gone['title']}) merged into {keep['id']} to stay within {max_tasks} tasks."
    return _clamp(fixed.add(_redirect(tasks, gone["id"], keep["id"]), note, clamped=True), max_tasks)
