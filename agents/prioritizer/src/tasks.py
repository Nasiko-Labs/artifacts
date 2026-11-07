"""Pure: the task list and goals in the user's message, parsed in code.

- One task per line, bullets (`-`, `*`, `•`, `1.`, `1)`, `[ ]`); checked boxes (`[x]`) are skipped as done.
- A single line splits on `;`, else on `,` when that gives >= 3 parts of <= 12 words each.
- `Goals:` / `Priorities:` / `This quarter:` start the goals section, `Tasks:` / `To do:` the tasks section;
  "goals: ..." inside a line also gives goals.
- A first line that asks for the ranking ("Prioritise these:") is the instruction; its tail after a colon is tasks.
- Another agent's envelope (`action_items` or `items`, each `task`/`text` + optional `due`) is read as tasks.
- Exact duplicates (case and spacing ignored) are merged. Fence markers are stripped from everything.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from dates import strip_as_of
from prompts import strip_markers

MAX_TASKS = 40
MAX_CHARS = 12_000
MAX_GOALS = 5
GOAL_MAX_CHARS = 200
SPLIT_MAX_WORDS = 12
ENVELOPE_LISTS = ("action_items", "items", "tasks")

_BULLET = re.compile(r"^(?:[-*•]\s+|\d{1,3}[.)]\s+)?(?:\[( |x|X)\]\s*)?(.*)$")
_LABEL = re.compile(r"^(goals?|priorities|this quarter|tasks?|to-?dos?|my tasks)\s*:\s*(.*)$", re.IGNORECASE)
_INLINE_GOALS = re.compile(r"[,;(]?\s*\b(?:goals?|priorities)\s*:\s*(.+)$", re.IGNORECASE)
_STRONG_REQUEST = re.compile(r"\b(?:prioriti[sz]e|rank|re-?rank|triage|what should i (?:do|work on|drop|delegate|"
                             r"focus on|tackle))\b", re.IGNORECASE)
_WEAK_REQUEST = re.compile(r"\b(?:these|order|sort|schedule|my (?:tasks|to-?dos?|list|week|day))\b", re.IGNORECASE)
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_EDGE_PUNCT = " ,;:.-"


@dataclass(frozen=True)
class Parsed:
    tasks: tuple[str, ...] = ()
    goals: tuple[str, ...] = ()
    done: int = 0
    merged: int = 0
    instruction: str = ""


def parse(text: str) -> Parsed:
    clean = strip_markers(text or "")
    enveloped = from_envelope(clean)
    if enveloped is not None:
        unique, merged = _dedupe(enveloped)
        return Parsed(tuple(unique), merged=merged)
    return _parse_lines(strip_as_of(clean).splitlines())


def from_envelope(text: str) -> list[str] | None:
    stripped = (text or "").strip()
    fenced = _FENCED_JSON.match(stripped)
    body = fenced.group(1) if fenced else stripped
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    entries = next((data[k] for k in ENVELOPE_LISTS if isinstance(data, dict) and isinstance(data.get(k), list)), None)
    if entries is None:
        return None
    found: list[str] = []
    for entry in entries:
        task = entry if isinstance(entry, str) else _envelope_task(entry)
        if task:
            found.append(" ".join(task.split()))
    return found


def _envelope_task(entry: object) -> str:
    if not isinstance(entry, dict):
        return ""
    task = str(entry.get("task") or entry.get("text") or entry.get("title") or "").strip()
    due = str(entry.get("due") or "").strip()
    return f"{task} (due {due})" if task and due else task


def _parse_lines(lines: list[str]) -> Parsed:
    tasks: list[str] = []
    goals: list[str] = []
    done, section, instruction = 0, "tasks", ""
    for index, raw in enumerate(line.strip() for line in lines):
        if not raw:
            continue
        label = _LABEL.match(raw)
        if label:
            section = "tasks" if label.group(1).casefold() in ("task", "tasks", "todo", "todos", "to-do", "to-dos",
                                                                "my tasks") else "goals"
            if label.group(2).strip():  # "Goals: ship v2" on one line: the lines after it are tasks again
                (goals if section == "goals" else tasks).extend(_split(label.group(2)))
                section = "tasks"
            continue
        bullet = _BULLET.match(raw)
        if bullet.group(1) in ("x", "X"):
            done += 1
            continue
        is_bullet = bullet.group(2) != raw
        body = bullet.group(2).strip()
        if not is_bullet:
            inline = _INLINE_GOALS.search(body)
            if inline:
                goals.extend(_split(inline.group(1)))
                body = body[:inline.start()].strip(_EDGE_PUNCT)
            if not _has_earlier_line(index, lines) and _is_request(body):
                instruction, body = _request(body)
        if body.strip(_EDGE_PUNCT):
            (goals if section == "goals" else tasks).append(body.strip())
    if len(tasks) == 1:
        tasks = _split(tasks[0])
    unique, merged = _dedupe(tasks)
    goal_list = [g[:GOAL_MAX_CHARS] for g in _dedupe(goals)[0]][:MAX_GOALS]
    return Parsed(tuple(unique), tuple(goal_list), done, merged, instruction)


def _has_earlier_line(index: int, lines: list[str]) -> bool:
    """Only the first non-empty line can be the request."""
    return any(line.strip() for line in lines[:index])


def _is_request(body: str) -> bool:
    return bool(_STRONG_REQUEST.search(body)) or (":" in body and bool(_WEAK_REQUEST.search(body)))


def _request(body: str) -> tuple[str, str]:
    head, colon, tail = body.partition(":")
    if colon:
        return head.strip(_EDGE_PUNCT), tail.strip()
    return body.strip(_EDGE_PUNCT), ""


def _split(line: str) -> list[str]:
    stripped = line.strip().strip(_EDGE_PUNCT)
    if ";" in stripped:
        return [p.strip(_EDGE_PUNCT) for p in stripped.split(";") if p.strip(_EDGE_PUNCT)]
    parts = [p.strip(_EDGE_PUNCT) for p in stripped.split(",") if p.strip(_EDGE_PUNCT)]
    if len(parts) >= 3 and all(len(p.split()) <= SPLIT_MAX_WORDS for p in parts):
        return parts
    return [stripped] if stripped else []


def _dedupe(items: list[str]) -> tuple[list[str], int]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        key = " ".join(item.casefold().split())
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    return unique, len(items) - len(unique)
