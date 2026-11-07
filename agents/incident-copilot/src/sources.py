"""The five fanout loops. Each opens its own reader (discovery + ToolHealth + excerpting fence), runs a
bounded loop, reads data by shape or checks the model's records against its own raw output, filters by
window, and leaves JSON-safe results and its coverage status on its Slot."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

import coverage as cov
import loops
import prompts
import records as r
import scoring
import shapes
from deps import TurnDeps
from excerpt import CHANGES_CAP
from investigation import Investigation
from jsonutil import first_key, parse_json, walk_dicts
from loops import Reader
from windows import Windows, describe, parse_time

if TYPE_CHECKING:
    from fanout import Slot

METRICS_CALLS = LOGS_CALLS = ERRORS_CALLS = HISTORY_CALLS = 3
CHANGES_A_CALLS = 3
CHANGES_B_CALLS = 2
RUNBOOK_CALLS = 2
CHANGES_B_TOP = 3
RUNBOOK_WORDS = re.compile(r"runbook|playbook|on-?call", re.IGNORECASE)
REPO_NAME_KEYS = ("full_name", "name", "slug", "path_with_namespace")


def _request(inv: Investigation) -> str:
    return f"Service: {inv.service or 'unknown'}\nIncident: {inv.incident.title or inv.incident.id or 'unknown'}"


async def _open(deps: TurnDeps, slot: Slot, step: str, cap: int | None = None) -> Reader:
    reader = await (loops.open_reader(deps, step, cap) if cap else loops.open_reader(deps, step))
    slot.readers.append(reader)
    return reader


def _status(source: str, reader: Reader, found_any: bool) -> cov.SourceStatus:
    if reader.failure:
        return cov.SourceStatus(source, "failed", "tool gateway unreachable")
    return cov.assess(source, reader.offered_names, reader.dropped, found_any=found_any)


async def metrics_loop(deps: TurnDeps, inv: Investigation, slot: Slot) -> None:
    reader = await _open(deps, slot, "metrics")
    w = inv.windows
    if reader.tools:
        await loops.run(deps, reader, prompts.metrics_system(inv.service, describe(w.obs)), _request(inv),
                        METRICS_CALLS)
    series = shapes.read_series(reader.calls, w.obs)
    slot.partial = {**slot.partial, "series": [r.to_dict(s) for s in series]}
    slot.statuses = [_status("metrics", reader, bool(series))]


async def logs_loop(deps: TurnDeps, inv: Investigation, slot: Slot) -> None:
    reader = await _open(deps, slot, "logs")
    w = inv.windows
    answer = ""
    if reader.tools:
        result = await loops.run(deps, reader, prompts.logs_system(inv.service, describe(w.obs)), _request(inv),
                                 LOGS_CALLS, json_key="lines")
        answer = result.text
    lines = shapes.read_logs(reader.calls, w.obs)
    dropped = 0
    if not lines and answer:  # no shape recognised: the model's verbatim lines, checked
        seen, items = reader.haystack(), r.answer_list(answer, "lines") or []
        checked = r.check_all(items, lambda i: r.check_line(i, seen, inv.now))
        lines = tuple(x for x in checked.kept if w.obs.contains(parse_time(x.at)))
        dropped = len(items) - len(lines)
    slot.partial = {**slot.partial, "log_lines": [r.to_dict(x) for x in lines], "dropped_logs": dropped}
    slot.statuses = [_status("logs", reader, bool(lines))]


def window_issues(issues: Sequence[r.Issue], w: Windows) -> tuple[r.Issue, ...]:
    """Issues seen in the observability window: first seen by its end, and last seen (if known) after its start."""
    def seen_in(issue: r.Issue) -> bool:
        first, last = parse_time(issue.first_seen), parse_time(issue.last_seen) if issue.last_seen else None
        return first is not None and first <= w.obs.end and (last is None or last >= w.obs.start)
    return tuple(i for i in issues if seen_in(i))


async def errors_loop(deps: TurnDeps, inv: Investigation, slot: Slot) -> None:
    reader = await _open(deps, slot, "errors")
    w = inv.windows
    answer = ""
    if reader.tools:
        result = await loops.run(deps, reader, prompts.errors_system(inv.service, describe(w.obs)), _request(inv),
                                 ERRORS_CALLS, json_key="issues")
        answer = result.text
    seen, items = reader.haystack(), r.answer_list(answer, "issues") or []
    claimed = r.check_all(items, lambda i: r.check_issue(i, seen, inv.now))
    by_shape = shapes.read_issues(reader.calls)
    extra_files = {i.id: i.files for i in claimed.kept}
    issues = tuple(r.with_issue_files(i, extra_files.get(i.id, ())) for i in by_shape) or claimed.kept
    kept = window_issues(issues, w)
    slot.partial = {**slot.partial, "issues": [r.to_dict(i) for i in kept],
                    "dropped_issues": claimed.dropped if not by_shape else 0}
    slot.statuses = [_status("errors", reader, bool(kept))]


async def changes_loop(deps: TurnDeps, inv: Investigation, slot: Slot) -> None:
    reader = await _open(deps, slot, "changes", CHANGES_CAP)
    if not reader.tools:
        slot.statuses = [_status("code", reader, False)]
        return
    repo, budget = inv.repo, CHANGES_A_CALLS
    if not repo:
        repo = await _repo_by_service(deps, reader, inv.service)
        budget -= 1
    if not repo:
        reason = (f"No code host repository matched service `{inv.service}`. Say which repo to include deploys "
                  "(for example: the repo is owner/name).")
        slot.statuses = [cov.SourceStatus("code", "skipped", reason)]
        return
    w = inv.windows
    result = await loops.run(deps, reader, prompts.changes_a_system(repo, describe(w.change)), _request(inv),
                             budget, json_key="changes")
    seen, items = reader.haystack(), r.answer_list(result.text, "changes") or []
    checked = r.check_all(items, lambda i: r.check_change(i, seen, inv.now))
    changes = tuple(c for c in checked.kept if w.change.contains(parse_time(c.time)))
    slot.partial = {**slot.partial, "repo": repo, "changes": [r.to_dict(c) for c in changes],
                    "dropped_changes": len(items) - len(changes)}
    slot.statuses = [_status("code", reader, bool(changes))]
    if changes:
        changes = await _with_files(deps, reader, inv, repo, changes)
        slot.partial = {**slot.partial, "changes": [r.to_dict(c) for c in changes]}


async def _repo_by_service(deps: TurnDeps, reader: Reader, service: str) -> str:
    """One call to list repositories; code accepts exactly one whose name equals the service name."""
    if not scoring.norm(service):
        return ""
    before = len(reader.calls)
    await loops.run(deps, reader, prompts.find_repo_system(service), f"Service: {service}", 1)
    names = set()
    for call in reader.calls[before:]:
        for record in walk_dicts(parse_json(call.text)):
            name = first_key(record, REPO_NAME_KEYS)
            if isinstance(name, str) and scoring.norm(name.split("/")[-1]) == scoring.norm(service):
                full = record.get("full_name") if isinstance(record.get("full_name"), str) else name
                names.add(full)
    return names.pop() if len(names) == 1 else ""


async def _with_files(deps: TurnDeps, reader: Reader, inv: Investigation, repo: str,
                      changes: tuple[r.Change, ...]) -> tuple[r.Change, ...]:
    """Changes B: the changed files of the top candidates by a code pre-score (recency and service only)."""
    w = inv.windows
    pre = sorted((s for s in (scoring.score_change(c, w, inv.service, repo, (), ()) for c in changes) if s),
                 key=lambda s: (-s.score, s.delta_min))[:CHANGES_B_TOP]
    if not pre:
        return changes
    refs = ", ".join(f"{s.change.kind} {s.change.ref} (sha {s.change.sha})" for s in pre)
    before = len(reader.calls)
    await loops.run(deps, reader, prompts.changes_b_system(repo, refs), _request(inv), CHANGES_B_CALLS)
    found = shapes.read_changed_files(reader.calls[before:])
    return tuple(r.with_files(c, [f for keys, files in found if _names(c, keys) for f in files]) for c in changes)


def _names(change: r.Change, keys: frozenset[str]) -> bool:
    ref = change.ref.lstrip("#")
    return any(k == ref or k == change.ref or scoring.same_sha(k, change.sha) for k in keys)


async def history_loop(deps: TurnDeps, inv: Investigation, slot: Slot) -> None:
    reader = await _open(deps, slot, "history")
    w = inv.windows
    past: tuple[r.PastIncident, ...] = ()
    dropped = 0
    if reader.tools:
        result = await loops.run(deps, reader, prompts.history_system(inv.service, inv.incident.title,
                                                                       describe(w.past)), _request(inv),
                                 HISTORY_CALLS, json_key="incidents")
        seen, items = reader.haystack(), r.answer_list(result.text, "incidents") or []
        checked = r.check_all(items, lambda i: r.check_past(i, seen, inv.now))
        past, dropped = checked.kept, checked.dropped
    slot.partial = {**slot.partial, "past": [r.to_dict(p) for p in past], "dropped_past": dropped}
    statuses = [_status("history", reader, bool(past))]
    if inv.incident.runbook_url:
        slot.partial = {**slot.partial, "runbook": inv.incident.runbook_url}
    else:
        runbook, status = await _runbook(deps, slot, inv)
        slot.partial = {**slot.partial, "runbook": runbook}
        statuses.append(status)
    slot.statuses = statuses


async def _runbook(deps: TurnDeps, slot: Slot, inv: Investigation) -> tuple[str, cov.SourceStatus]:
    reader = await _open(deps, slot, "runbook")
    if not reader.tools:
        return "", _status("document", reader, False)
    result = await loops.run(deps, reader, prompts.runbook_system(inv.service), _request(inv), RUNBOOK_CALLS,
                             json_key="docs")
    seen = reader.haystack()
    docs = r.check_all(r.answer_list(result.text, "docs") or [], lambda i: r.check_doc(i, seen)).kept
    wanted = scoring.norm(inv.service)
    match = next((d for d in docs if wanted and wanted in scoring.norm(d.title) and RUNBOOK_WORDS.search(d.title)),
                 None)
    return (match.url if match else ""), _status("document", reader, match is not None)
