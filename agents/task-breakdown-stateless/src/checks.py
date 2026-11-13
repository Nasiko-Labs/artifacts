"""Pure: read the model's plan JSON into a normalised draft, and list exactly what is wrong with it (the
text of the one repair call). Nothing here fixes anything; `fixes.py` does that when the repair fails."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from models import OWNER_PLACEHOLDER
from taskgraph import UNITS, find_cycle

MAX_TITLE_CHARS = 80
MAX_ACCEPTANCE = 3
PHASE_RANGE = (3, 6)
MAX_RISKS = 5
MAX_ASSUMPTIONS = 6
SOURCES = ("user", "derived", "policy")
VERBS = frozenset(
    ["add", "analyse", "analyze", "announce", "approve", "audit", "build", "buy", "choose", "clean", "collect", "configure", "create", "decide", "define", "deploy", "design", "document", "draft", "estimate", "evaluate", "fix", "gather", "hire", "implement", "integrate", "interview", "launch", "measure", "migrate", "monitor", "onboard", "plan", "prepare", "present", "publish", "prototype", "recruit", "refactor", "release", "remove", "research", "review", "run", "schedule", "secure", "select", "send", "set", "ship", "sign", "specify", "test", "train", "translate", "update", "upgrade", "validate", "verify", "write"])
_VERIFY = re.compile(r"verif|test|validat|integrat|review|check|\bqa\b|accept|sign[- ]off|rehears|dry[- ]run",
                     re.IGNORECASE)
_POLICY_TASK = re.compile(r"(?:every|each|all|any) (?:work )?(?:plan|project)s? (?:needs?|must (?:have|include|contain)|"
                          r"should (?:have|include)|requires?|has to (?:have|include)) (?:a |an )?([^.\n]{3,60})",
                          re.IGNORECASE)


@dataclass(frozen=True)
class Draft:
    """The model's plan, normalised; ids are still the model's own keys."""

    phases: tuple[tuple[int, str], ...] = ()
    tasks: tuple[dict, ...] = ()
    risks: tuple[dict, ...] = ()
    assumptions: tuple[dict, ...] = ()
    constraints: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return {"phases": [{"id": n, "name": name} for n, name in self.phases],
                "tasks": [{k: list(v) if isinstance(v, tuple) else v for k, v in t.items()} for t in self.tasks],
                "risks": list(self.risks), "assumptions": list(self.assumptions),
                "constraints": list(self.constraints)}


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strs(value: Any) -> tuple[str, ...]:
    return tuple(s for s in (_str(v) for v in value) if s) if isinstance(value, list) else ()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize(raw: Mapping[str, Any]) -> Draft:
    phases = tuple((_int(p.get("id", p.get("number")), i + 1), _str(p.get("name")) or f"Phase {i + 1}")
                   for i, p in enumerate(raw.get("phases") or []) if isinstance(p, Mapping))
    default_phase = phases[-1][0] if phases else 1
    tasks: list[dict] = []
    seen: set[str] = set()
    for i, item in enumerate(raw.get("tasks") or []):
        if not isinstance(item, Mapping) or not _str(item.get("title")):
            continue
        key = _str(item.get("id")) or f"X{i + 1}"
        key = key if key not in seen else f"X{i + 1}"
        seen.add(key)
        source = _str(item.get("source")).casefold()
        tasks.append({"id": key, "phase": _int(item.get("phase"), default_phase), "title": _str(item.get("title")),
                      "size": _str(item.get("size")).upper(), "depends_on": _strs(item.get("depends_on")),
                      "acceptance": _strs(item.get("acceptance")), "owner": _str(item.get("owner")) or OWNER_PLACEHOLDER,
                      "source": source if source in SOURCES else "derived",
                      "status": "done" if _str(item.get("status")).casefold() == "done" else "pending"})
    risks = tuple({"task": _str(r.get("task")), "text": _str(r.get("text"))} for r in raw.get("risks") or []
                  if isinstance(r, Mapping) and _str(r.get("text")))
    assumptions = tuple({"text": _str(a.get("text")), "based_on": _str(a.get("based_on"))}
                        for a in raw.get("assumptions") or [] if isinstance(a, Mapping) and _str(a.get("text")))
    return Draft(phases, tuple(tasks), risks, assumptions, _strs(raw.get("constraints")))


def two_verbs(title: str) -> bool:
    words = re.findall(r"[a-z]+", title.casefold())
    return any(w == "and" and 0 < i < len(words) - 1 and words[i + 1] in VERBS for i, w in enumerate(words))


def policy_expects_task(policy: Sequence[str]) -> bool:
    return any(_POLICY_TASK.search(p) for p in policy)


def problems(draft: Draft, band: tuple[int, int], policy: Sequence[str] = ()) -> list[str]:
    """Every rule the draft breaks, worded for the repair call."""
    found: list[str] = []
    ids = {t["id"] for t in draft.tasks}
    for t in draft.tasks:
        found.extend(_task_problems(t, ids))
    cycle = find_cycle({t["id"]: t["depends_on"] for t in draft.tasks})
    if cycle:
        found.append(f"dependency cycle {' -> '.join(cycle)}: remove one dependency so no task waits on itself")
    low, high = band
    if not low <= len(draft.tasks) <= high:
        found.append(f"{len(draft.tasks)} tasks: the plan needs between {low} and {high} tasks")
    used = {t["phase"] for t in draft.tasks}
    if len(draft.tasks) >= PHASE_RANGE[0] and not PHASE_RANGE[0] <= len(used) <= PHASE_RANGE[1]:
        found.append(f"{len(used)} phases: use {PHASE_RANGE[0]}-{PHASE_RANGE[1]} phases named by outcome")
    last = max(used, default=0)
    if draft.tasks and not any(_VERIFY.search(t["title"]) for t in draft.tasks if t["phase"] == last):
        found.append("the last phase has no verification or integration task: add one")
    if policy_expects_task(policy) and not any(t["source"] == "policy" for t in draft.tasks):
        found.append("the ORG RULES require a task that is missing: add it with source \"policy\"")
    return found


def _task_problems(task: dict, ids: set[str]) -> list[str]:
    tid, out = task["id"], []
    if task["size"] not in UNITS:
        out.append(f"{tid}: size {task['size'] or '(none)'!r} is not one of S, M, L, XL")
    elif task["size"] == "XL":
        out.append(f"{tid}: size XL is too big: split it into smaller tasks")
    if not task["acceptance"]:
        out.append(f"{tid}: no acceptance criteria: add 1-3 observable checks")
    elif len(task["acceptance"]) > MAX_ACCEPTANCE:
        out.append(f"{tid}: {len(task['acceptance'])} acceptance criteria: keep at most {MAX_ACCEPTANCE}")
    if len(task["title"]) > MAX_TITLE_CHARS:
        out.append(f"{tid}: title is longer than {MAX_TITLE_CHARS} characters")
    if two_verbs(task["title"]):
        out.append(f"{tid}: title {task['title']!r} joins two verbs: one deliverable per task, split it")
    for dep in task["depends_on"]:
        if dep not in ids or dep == tid:
            out.append(f"{tid}: depends_on {dep!r} is not another task id in the plan")
    return out
