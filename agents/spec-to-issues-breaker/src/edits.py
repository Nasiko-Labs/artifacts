"""Edit rounds before any write. Commands are parsed in code first (`drop 3`, `estimate 4 = 5`,
`retitle 2: New title`, `merge 2,3`, `team Platform`, `project Name`); anything else is one revise call whose
list is re-checked like a fresh split (verbatim quotes, numbers, dependencies, estimates in code)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

import prompts
from deps import TurnDeps
from llmcall import ask_once, load_json_object
from proposal import clean_dependencies, with_estimates
from records import ProposedIssue
from split import MAX_TITLE_CHARS, check_issues, parse_split

_COMMANDS = re.compile(
    r"(?P<drop>\bdrop\s+#?\d+(?:\s*(?:,|and)\s*#?\d+)*)"
    r"|(?P<estimate>\bestimate\s+#?\d+\s*(?:=|to|:)\s*\d+)"
    r"|(?P<retitle>\bretitle\s+#?\d+\s*[:=]\s*[^;\n]+)"
    r"|(?P<merge>\bmerge\s+#?\d+\s*(?:,|and|\+|into)\s*#?\d+)"
    r"|(?P<team>\bteam\s+[^,;\n]+)"
    r"|(?P<project>\bproject\s+[^,;\n]+)", re.IGNORECASE)
_SEPARATORS = re.compile(r"^[\s,;.]*(?:(?:and|then)[\s,;.]*)*$", re.IGNORECASE)
_NUMBER = re.compile(r"\d+")
MAX_ESTIMATE = 21


@dataclass(frozen=True)
class EditResult:
    issues: tuple[ProposedIssue, ...]
    team: str = ""                # a team name the user asked for (resolved by the caller)
    project: str = ""
    notes: tuple[str, ...] = ()
    off_topic: bool = False


def commands_only(text: str) -> bool:
    """True when the whole text is edit commands (and separators)."""
    rest = _COMMANDS.sub(" ", text or "")
    return bool(_COMMANDS.search(text or "")) and bool(_SEPARATORS.match(rest))


def apply_commands(issues: Sequence[ProposedIssue], text: str) -> EditResult:
    current, notes = tuple(issues), []
    team = project = ""
    for match in _COMMANDS.finditer(text):
        kind, body = match.lastgroup, match.group(0)
        ids = [int(n) for n in _NUMBER.findall(body.split(":")[0].split("=")[0])]
        known = {i.local_id for i in current}
        wanted = ids[:2] if kind == "merge" else ids[:1] if kind in ("estimate", "retitle") else ids
        if kind in ("drop", "estimate", "retitle", "merge") and not set(wanted) <= known:
            notes.append(f"Ignored \"{body.strip()}\": no such issue number.")
            continue
        if kind == "drop":
            current = _drop(current, set(ids))
        elif kind == "estimate":
            points = int(_NUMBER.findall(body)[-1])
            current = tuple(replace(i, estimate=min(points, MAX_ESTIMATE), user_estimate=True)
                            if i.local_id == ids[0] else i for i in current)
        elif kind == "retitle":
            title = re.split(r"[:=]", body, maxsplit=1)[1].strip()[:MAX_TITLE_CHARS]
            current = tuple(replace(i, title=title) if i.local_id == ids[0] else i for i in current)
        elif kind == "merge":
            current = _merge(current, ids[0], ids[1])
        elif kind == "team":
            team = body.split(None, 1)[1].strip()
        else:
            project = body.split(None, 1)[1].strip()
    return EditResult(current, team, project, tuple(notes))


def _drop(issues: tuple[ProposedIssue, ...], ids: set[int]) -> tuple[ProposedIssue, ...]:
    return tuple(replace(i, depends_on=tuple(d for d in i.depends_on if d not in ids))
                 for i in issues if i.local_id not in ids)


def _merge(issues: tuple[ProposedIssue, ...], keep: int, gone: int) -> tuple[ProposedIssue, ...]:
    if keep == gone:
        return issues
    other = next(i for i in issues if i.local_id == gone)
    merged = []
    for issue in issues:
        if issue.local_id == gone:
            continue
        if issue.local_id == keep:
            issue = replace(issue, description=f"{issue.description}\n\n{other.description}".strip(),
                            acceptance_criteria=tuple(dict.fromkeys((*issue.acceptance_criteria,
                                                                     *other.acceptance_criteria))),
                            signals={k: bool(issue.signals.get(k) or other.signals.get(k))
                                     for k in {*issue.signals, *other.signals}},
                            depends_on=(*issue.depends_on, *other.depends_on), user_estimate=False)
        deps = tuple(dict.fromkeys(keep if d == gone else d for d in issue.depends_on))
        merged.append(replace(issue, depends_on=tuple(d for d in deps if d != issue.local_id)))
    return with_estimates(merged)


async def revise(deps: TurnDeps, issues: Sequence[ProposedIssue], spec_text: str, instruction: str) -> EditResult:
    """One LLM call: current list in, new list out; re-checked in code; ids and statuses kept stable."""
    listed = [{"local_id": i.local_id, "title": i.title, "description": i.description,
               "acceptance_criteria": list(i.acceptance_criteria), "source_quote": i.source_quote,
               "signals": dict(i.signals), "depends_on": list(i.depends_on)} for i in issues]
    user = "\n\n".join((prompts.fence_list(json.dumps(listed, ensure_ascii=False)),
                        prompts.fence_spec(spec_text[:30_000]),
                        prompts.fence_ask(instruction[:deps.config.max_instruction_chars])))
    answer = await ask_once(deps, ({"role": "system", "content": prompts.REVISE_SYSTEM},
                                   {"role": "user", "content": user}))
    data = load_json_object(answer)
    if data is not None and data.get("status") == prompts.OFF_TOPIC:
        return EditResult(tuple(issues), off_topic=True)
    parsed = parse_split(answer)
    if parsed is None:
        return EditResult(tuple(issues), notes=("I couldn't apply that edit; the list is unchanged.",))
    kept, dropped, lines = check_issues(parsed, spec_text)
    old = {i.local_id: i for i in issues}
    stable = [replace(n, exists=old[n.local_id].exists, exists_url=old[n.local_id].exists_url,
                      status=old[n.local_id].status) if n.local_id in old else n for n in kept]
    cleaned, cycle_notes = clean_dependencies(with_estimates(stable))
    notes = [*cycle_notes]
    if dropped or lines:
        notes.append(f"{dropped} edited issue(s) and {lines} acceptance line(s) were not in the spec and were left out.")
    return EditResult(cleaned, notes=tuple(notes))
