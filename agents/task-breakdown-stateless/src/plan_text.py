"""Pure: read a plan back from text this agent rendered earlier (render.py tables, planfile.py task_plan.md, or
the fenced json envelope), so a follow-up can work on it without any stored state.

Each reader returns plain parts (see `build`) or None. A reader refuses a partial plan: the table form must
hold exactly the task and phase counts its header states, task_plan.md must reach its closing sections. A
trimmed or garbled reply is therefore "not readable", never a smaller plan."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from models import POLICY_MARK, Assumption, Phase, Plan, Risk, Task
from taskgraph import find_cycle, id_key

_HEAD = re.compile(r"^\*\*Plan: (.*?)\*\*\s+·\s+(\d+) tasks · (\d+) phases", re.MULTILINE)
_PHASE = re.compile(r"^\*\*Phase (\d+): (.*?)\*\* \(\d+ tasks, \d+ units\)\s*$")
_ROW = re.compile(r"^\|\s*(T\d+)\s*\|(.*)\|\s*$")
_PIPE = re.compile(r"(?<!\\)\|")
_DONE = re.compile(r"^~~(.*)~~ \(done\)$")
_LABEL = re.compile(r"^\*\*(Constraints|Assumptions|Risks):\*\* (.*)$")
_ASSUMPTION = re.compile(r'^(A\d+) (.*) \("(.*)"\)$')
_RISK = re.compile(r"^(R\d+) \((T\d+)\) (.*)$")
_LANES = re.compile(r"\*\*Lanes for (\d+) (?:person|people)\*\*")
_FILE_HEAD = re.compile(r"^# Task plan: (.*)$", re.MULTILINE)
_FILE_PHASE = re.compile(r"^### Phase (\d+): (.*?)\s+\[[ x]\] (?:done|pending)\s*$")
_FILE_TASK = re.compile(r"^- \[( |x)\] (T\d+) (.*) \((S|M|L|XL)\) — owner: (.*?) — needs: (.*?) — done when: (.*)$")
_FILE_ASSUMPTION = re.compile(r'^- (A\d+) (.*) \(from: "(.*)"\)$')
_FILE_RISK = re.compile(r"^- (R\d+) risk on (T\d+): (.*)$")
_TEAM = re.compile(r"^Team of (\d+)$")
_DEADLINE = re.compile(r"^Deadline: (.*) \(as you wrote it\)$")
_JSON = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def _ids(text: str) -> tuple[str, ...]:
    return tuple(i.strip() for i in text.split(",") if i.strip() and i.strip() != "none")


def _task(tid: str, phase: int, title: str, size: str, needs: str, owner: str, done_when: str, done: bool) -> dict:
    title = title.strip()
    policy = title.endswith(POLICY_MARK)
    return {"id": tid, "phase": phase, "title": title.removesuffix(POLICY_MARK).strip(), "size": size.strip(),
            "depends_on": _ids(needs), "owner": owner.strip(), "status": "done" if done else "pending",
            "source": "policy" if policy else "derived",
            "acceptance": tuple(a.strip() for a in done_when.split("; ") if a.strip())}


def _row(line: str, phase: int) -> dict | None:
    match = _ROW.match(line)
    cells = [c.strip().replace("\\|", "|") for c in _PIPE.split(match.group(2))] if match else []
    if len(cells) != 5:
        return None
    done = _DONE.match(cells[0])
    return _task(match.group(1), phase, done.group(1) if done else cells[0], cells[1], cells[2], cells[3], cells[4],
                 bool(done))


def from_table(text: str) -> dict | None:
    head = _HEAD.search(text or "")
    if not head:
        return None
    parts: dict[str, Any] = {"goal": head.group(1), "phases": [], "tasks": [], "constraints": (), "assumptions": [],
                             "risks": []}
    phase = 0
    for line in text[head.end():].splitlines():
        if match := _PHASE.match(line):
            phase = int(match.group(1))
            parts["phases"].append((phase, match.group(2)))
        elif line.startswith("|"):
            row = _row(line, phase)
            parts["tasks"] += [row] if row else []
        elif match := _LABEL.match(line):
            _labelled(parts, match.group(1), match.group(2).split(" · "))
    lanes = _LANES.search(text)
    parts["team"] = int(lanes.group(1)) if lanes else None
    complete = len(parts["tasks"]) == int(head.group(2)) and len(parts["phases"]) == int(head.group(3))
    return parts if complete else None


def _labelled(parts: dict, label: str, items: list[str]) -> None:
    if label == "Constraints":
        parts["constraints"] = tuple(i.strip() for i in items if i.strip())
    pattern = _ASSUMPTION if label == "Assumptions" else _RISK if label == "Risks" else None
    key = label.casefold()
    for item in items if pattern else ():
        if match := pattern.match(item.strip()):
            parts[key].append(match.groups())


def from_file(text: str) -> dict | None:
    head = _FILE_HEAD.search(text or "")
    if not head or "## Critical path" not in text[head.end():]:
        return None
    parts: dict[str, Any] = {"goal": head.group(1), "phases": [], "tasks": [], "assumptions": [], "risks": [],
                             "constraints": (), "team": None}
    section, phase, constraints = "", 0, []
    for line in text[head.end():].splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        elif match := _FILE_PHASE.match(line):
            phase = int(match.group(1))
            parts["phases"].append((phase, match.group(2)))
        elif match := _FILE_TASK.match(line):
            box, tid, title, size, owner, needs, done_when = match.groups()
            parts["tasks"].append(_task(tid, phase, title, size, needs, owner, done_when, box == "x"))
        elif section == "Constraints" and line.startswith("- ") and line != "- none stated":
            constraints.append(line[2:].strip())
        elif match := _FILE_ASSUMPTION.match(line):
            parts["assumptions"].append(match.groups())
        elif match := _FILE_RISK.match(line):
            parts["risks"].append(match.groups())
    parts["constraints"] = tuple(constraints)
    return parts if parts["tasks"] and parts["phases"] else None


def from_json(text: str) -> dict | None:
    match = _JSON.search(text or "")
    try:
        env = json.loads(match.group(1)) if match else None
    except ValueError:
        return None
    if not isinstance(env, dict) or not isinstance(env.get("items"), list) or not env["items"]:
        return None
    try:
        tasks = [{**_task(i["id"], int(i["phase"]), i["title"], i["size"], ", ".join(i.get("depends_on") or []),
                          i.get("owner") or "[owner?]", "; ".join(i.get("acceptance") or []), i.get("status") == "done"),
                  "source": i.get("source") if i.get("source") in ("user", "derived", "policy") else "derived"}
                 for i in env["items"]]
        phases = [(int(p["number"]), str(p["name"])) for p in env.get("phases") or []]
        assumptions = [(a["id"], a["text"], a["based_on"]) for a in env.get("assumptions") or []]
        risks = [(r["id"], r["task"], r["text"]) for r in env.get("risks") or []]
    except (KeyError, TypeError, ValueError):
        return None
    lanes = env.get("lanes") if isinstance(env.get("lanes"), dict) else {}
    return {"goal": str(env.get("goal") or ""), "phases": phases, "tasks": tasks, "assumptions": assumptions,
            "risks": risks, "constraints": tuple(str(c) for c in env.get("constraints") or []),
            "team": lanes.get("people")}


def read(text: str) -> dict | None:
    """The first reader that finds a complete plan in the text, or None."""
    for reader in (from_table, from_file, from_json):
        parts = reader(text)
        if parts:
            return parts
    return None


def build(parts: dict, source: str) -> Plan | None:
    """The Plan, or None when the parts do not make a valid, acyclic plan."""
    team = next((int(m.group(1)) for c in parts["constraints"] if (m := _TEAM.match(c))), None) or parts.get("team")
    deadline = next((m.group(1) for c in parts["constraints"] if (m := _DEADLINE.match(c))), "")
    ids = {t["id"] for t in parts["tasks"]}
    tasks = [{**t, "depends_on": tuple(d for d in t["depends_on"] if d in ids)}
             for t in parts["tasks"]]
    if len(ids) != len(tasks) or find_cycle({t["id"]: t["depends_on"] for t in tasks}):
        return None
    try:
        return Plan(goal=parts["goal"], constraints=parts["constraints"], deadline=deadline, team_size=team,
                    phases=tuple(Phase(number=n, name=name) for n, name in parts["phases"]),
                    tasks=tuple(Task(**t) for t in tasks),
                    assumptions=tuple(Assumption(id=a, text=t, based_on=b) for a, t, b in parts["assumptions"]),
                    risks=tuple(Risk(id=r, task=t, text=x) for r, t, x in parts["risks"] if t in ids),
                    next_id=max(id_key(i)[1] for i in ids) + 1,
                    source=_grounding(source, parts))
    except (ValidationError, TypeError, ValueError):
        return None


def _grounding(source: str, parts: dict) -> str:
    """What a revision checks owners, assumptions and constraints against: the user's words still in the
    history, plus what the earlier plan already grounded (those words may have been trimmed since)."""
    owners = (t["owner"] for t in parts["tasks"])
    return "\n".join([source, *(b for _, _, b in parts["assumptions"]), *owners, *parts["constraints"]])
