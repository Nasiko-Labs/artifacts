"""Pure: follow-ups answered in code from the stored plan, with zero LLM calls: the critical path, what can
start now, what depends on a task, counts per phase, one task's details, lanes for a team size, weeks at a
stated rate, marking a task done, the json envelope, the files, and "put this in my tracker"."""

from __future__ import annotations

import re
from dataclasses import dataclass

from inputs import NUMBER_WORDS, team_size, word_count
from models import Plan

MAX_QUERY_WORDS = 25
_NUM = r"(\d{1,3}|" + "|".join(NUMBER_WORDS) + r")"
_TRACKER = re.compile(r"\b(?:put|create|file|add|push|open|send|make)\b.*\b(?:jira|linear|asana|trello|clickup|"
                      r"monday|tracker|tickets?|issues?|cards?|board)\b", re.IGNORECASE)
_PATTERNS = (
    ("done", re.compile(r"\bT(\d+)\s+(?:is|was|has been)\s+(?:done|finished|complete|completed)\b|"
                        r"\bmark\s+T(\d+)\s+(?:as\s+)?(?:done|finished|complete|completed)\b", re.IGNORECASE)),
    ("rate", re.compile(rf"\b{_NUM}\s+units?\s+(?:per|a|each|every)\s+week\b", re.IGNORECASE)),
    ("dependents", re.compile(r"\b(?:depends? on|waits? (?:on|for)|blocked by|needs?|after)\s+T(\d+)\b|\bT(\d+)'?s? "
                              r"dependents\b", re.IGNORECASE)),
    ("critical", re.compile(r"critical path|longest (?:chain|path)|bottleneck", re.IGNORECASE)),
    ("phase_count", re.compile(rf"how many tasks.*\bphase\s+{_NUM}\b", re.IGNORECASE)),
    ("count", re.compile(r"how many tasks|how big is (?:the|this) plan", re.IGNORECASE)),
    ("start", re.compile(r"start (?:now|first|today)|can (?:we|i) start|what(?:'s| is) (?:ready|next)|"
                         r"in parallel|at the same time", re.IGNORECASE)),
    ("task", re.compile(r"^(?:what(?:'s| is)|show(?: me)?|details? (?:of|for|on)|explain|tell me about)\s+T(\d+)\b",
                        re.IGNORECASE)),
    ("file", re.compile(r"task_plan(?:\.md)?|plan file|(?:show|give me|repeat) (?:me )?(?:the )?(?:whole |full )?plan",
                        re.IGNORECASE)),
    ("progress", re.compile(r"progress(?:\.md| log| template| file)", re.IGNORECASE)),
)
_REVISE_START = re.compile(r"^\s*(?:please\s+)?(?:split|merge|add|drop|remove|delete|rename|move|change|make|replace|"
                           r"combine|insert|re-?scope|shrink|break|reorder|swap|turn|update|cut|rewrite)\b", re.IGNORECASE)
_JSON_ONLY = re.compile(r"^\s*(?:(?:show|give me|send|return)\s+)?(?:it|the plan|this)?\s*(?:as json|json only|in json)"
                        r"\s*[.!]?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str
    number: int = 0
    task: str = ""


def _num(raw: str) -> int:
    return NUMBER_WORDS.get(raw.casefold()) or int(raw)


def parse(text: str) -> Command | None:
    """The follow-up this message asks for, or None (a revision or a new goal)."""
    if _TRACKER.search(text or ""):
        return Command("tracker")
    if word_count(text) > MAX_QUERY_WORDS or _REVISE_START.match(text or ""):
        return None
    if _JSON_ONLY.match(text or ""):
        return Command("json")
    people = team_size(text)
    for kind, rx in _PATTERNS:
        match = rx.search(text or "")
        if not match:
            continue
        groups = [g for g in match.groups() if g]
        if kind in ("done", "dependents", "task"):
            return Command(kind, task=f"T{groups[0]}")
        if kind in ("rate", "phase_count"):
            return Command(kind, number=_num(groups[0]))
        if kind == "start" and people:
            return Command("team", number=people)
        return Command(kind)
    if people and word_count(text) <= 8:
        return Command("team", number=people)
    return None


def with_change(plan: Plan, command: Command) -> Plan | None:
    """The plan a command changes (team size, rate, a task done), or None when it only reads."""
    if command.kind == "team":
        constraints = tuple(c for c in plan.constraints if not c.startswith("Team of "))
        return plan.model_copy(update={"team_size": command.number,
                                       "constraints": (f"Team of {command.number}", *constraints)})
    if command.kind == "rate" and command.number > 0:
        return plan.model_copy(update={"rate": command.number})
    if command.kind == "done" and plan.task(command.task):
        tasks = tuple(t.model_copy(update={"status": "done"}) if t.id == command.task else t for t in plan.tasks)
        return plan.model_copy(update={"tasks": tasks})
    return None
