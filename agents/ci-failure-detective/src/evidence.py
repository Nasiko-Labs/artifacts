"""The commits, history and blame steps, run after the log is read; then flakiness and the culprit are
computed in code. Commits and history run concurrently."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

import culprit as c
import excerpt
import history as h
import prompts
import records
from analysis import Analysis
from culprit import Commit
from reader import StepRun, TurnReader
from records import Run

log = logging.getLogger("ci_detective.evidence")

COMMITS_CALLS, HISTORY_CALLS, BLAME_CALLS, COMPARE_CALLS = 2, 4, 2, 1
GATHER_SECS, BLAME_SECS, COMPARE_SECS = 120.0, 60.0, 60.0
MAX_COMMITS = 10
MAX_BLAME_PATHS = 4


async def gather(reader: TurnReader, a: Analysis) -> Analysis:
    commits_step, history_step = await asyncio.gather(_commits(reader, a), _history(reader, a))
    notes = tuple(s.note for s in (commits_step, history_step) if s.note)
    a = replace(a, commits=_read_commits(reader, ("commits",), a.commits), notes=(*a.notes, *notes))
    a = replace(a, rows=_rows(reader, a))
    if not a.rows or len(a.rows) <= 1:
        a = replace(a, notes=(*a.notes, "No earlier runs of this workflow were found, so flakiness was not checked."))
    return a


async def _commits(reader: TurnReader, a: Analysis) -> StepRun:
    what = (f"list the commits of pull request #{a.pr.number if a.pr else a.target.number}." if a.target.kind == "pr"
            else f"get commit {a.run.head_sha} with its changed files.")
    system = prompts.commits_system(a.target.repo_full, what, reader.deps.now)
    return await reader.run("commits", system, "Fetch them.", COMMITS_CALLS, GATHER_SECS)


async def _history(reader: TurnReader, a: Analysis) -> StepRun:
    start, end = h.window(_t_fail(a, reader.deps.now))
    branches = " and ".join(f'branch "{b}"' for b in dict.fromkeys(x for x in (a.target_branch, a.head_branch) if x))
    system = prompts.history_system(a.target.repo_full, a.run.workflow, branches or "the run's branch", a.job.name,
                                    start, end, reader.deps.now)
    return await reader.run("history", system, "Fetch them.", HISTORY_CALLS, GATHER_SECS)


async def compare_if_needed(reader: TurnReader, a: Analysis) -> Analysis:
    """For a push run with a boundary: the commits between the last green run and B (one call)."""
    boundary = a.flaky.boundary if a.flaky else None
    if a.target.kind == "pr" or boundary is None or boundary.last_green_sha == boundary.sha:
        return a
    what = f"compare commit {boundary.last_green_sha} with commit {boundary.sha} and list the commits between them."
    system = prompts.commits_system(a.target.repo_full, what, reader.deps.now)
    step = await reader.run("commits", system, "Fetch them.", COMPARE_CALLS, COMPARE_SECS)
    commits = _read_commits(reader, ("commits",), a.commits)
    return replace(a, commits=commits, notes=(*a.notes, *((step.note,) if step.note else ())))


async def blame(reader: TurnReader, a: Analysis) -> Analysis:
    paths = _blame_paths(a)
    top = c.narrow(_suspects(a), a.locations, excerpt.test_file(a.test_id)).culprit
    fetch = top.commit.sha if top is not None and top.commit.files is None else ""
    if not paths and not fetch:
        return a
    start, end = h.window(_t_fail(a, reader.deps.now))
    system = prompts.blame_system(a.target.repo_full, ", ".join(paths) or "(none)", start, end, reader.deps.now, fetch)
    step = await reader.run("blame", system, "Fetch them.", BLAME_CALLS, BLAME_SECS)
    changed = _read_commits(reader, ("blame",), ())
    commits = tuple(_with_files(x, changed) for x in a.commits)
    note = (step.note,) if step.note else ()
    return replace(a, commits=commits, change_shas=tuple(x.sha for x in changed), notes=(*a.notes, *note))


def conclude(a: Analysis, reader: TurnReader) -> Analysis:
    """Flakiness over the history rows, then the culprit over the suspect range (all in code)."""
    changes = [t for x in _read_commits(reader, ("blame",), ()) if (t := records.parse_time(x.date))]
    flaky = h.assess(a.rows, a.target_branch, a.head_branch, changes)
    a = replace(a, flaky=flaky)
    narrowed = c.narrow(_suspects(a), a.locations, excerpt.test_file(a.test_id))
    return replace(a, narrowed=narrowed)


def assess_only(a: Analysis) -> Analysis:
    """Flakiness without blame (before the compare pass)."""
    return replace(a, flaky=h.assess(a.rows, a.target_branch, a.head_branch))


def _suspects(a: Analysis) -> tuple[Commit, ...]:
    boundary = a.flaky.boundary if a.flaky else None
    commits = a.commits[-MAX_COMMITS:]
    if boundary is None:
        return commits if a.target.kind == "pr" else tuple(x for x in commits if x.sha == a.run.head_sha) or commits
    return c.suspect_range(commits, boundary.last_green_sha, boundary.sha)


def _blame_paths(a: Analysis) -> tuple[str, ...]:
    test_path = excerpt.test_file(a.test_id)
    paths = ([test_path] if test_path else []) + [loc.path for loc in a.locations]
    return tuple(dict.fromkeys(paths))[:MAX_BLAME_PATHS]


def _read_commits(reader: TurnReader, steps: Sequence[str], known: Sequence[Commit]) -> tuple[Commit, ...]:
    """Commits by shape, in first-seen order; a version with files replaces one without."""
    found = [x for call in reader.ledger.of(*steps) for x in records.commits(call.text)]
    order: dict[str, Commit] = {x.sha: x for x in known}
    for x in found:
        prior = order.get(x.sha)
        if prior is None or (prior.files is None and x.files is not None):
            order[x.sha] = x if prior is None else replace(prior, files=x.files)
    return tuple(order.values())


def _with_files(commit: Commit, fetched: Sequence[Commit]) -> Commit:
    if commit.files is not None:
        return commit
    match = next((x for x in fetched if x.sha == commit.sha and x.files is not None), None)
    return replace(commit, files=match.files) if match else commit


def _t_fail(a: Analysis, now: datetime) -> datetime:
    return records.parse_time(a.run.created_at) or now


def _rows(reader: TurnReader, a: Analysis) -> tuple[h.Row, ...]:
    calls = reader.ledger.of("history", "locate")
    runs = {(r.run_id, r.attempt): r for call in calls for r in records.runs(call.text) if _same_workflow(r, a.run)}
    jobs = [j for call in calls for j in records.jobs(call.text) if j.name == a.job.name]
    t_fail = _t_fail(a, reader.deps.now)
    rows = []
    for run in runs.values():
        when = records.parse_time(run.created_at)
        if when is None or run.conclusion not in ("success", "failure", "timed_out"):
            continue
        rows.append(h.Row(run.run_id, run.attempt, run.head_sha, run.branch, when, "green", run.url))
    failed = [r.run_id for r in rows if r.run_id != a.run.run_id and runs[(r.run_id, r.attempt)].conclusion != "success"]
    inspected = set(h.to_inspect([replace(r, state="red") for r in rows if r.run_id in failed], a.run.run_id))
    stated = [replace(r, state=_state(runs[(r.run_id, r.attempt)], a, jobs, inspected, reader)) for r in rows]
    stated = [r for r in stated if r.run_id != a.run.run_id] + [_failed_row(a, t_fail)]
    return h.select(stated, a.target_branch, a.head_branch, t_fail, a.run.head_sha)


def _failed_row(a: Analysis, t_fail: datetime) -> h.Row:
    return h.Row(a.run.run_id, a.run.attempt, a.run.head_sha, a.run.branch, t_fail, "red", a.run.url)


def _same_workflow(r: Run, target: Run) -> bool:
    if r.workflow_id and target.workflow_id:
        return r.workflow_id == target.workflow_id
    return bool(r.workflow) and r.workflow == target.workflow


def _state(run: Run, a: Analysis, jobs, inspected: set[str], reader: TurnReader) -> h.State:
    if run.conclusion == "success":
        return "green"
    job = next((j for j in jobs if j.run_id == run.run_id), None)
    if job is not None and job.conclusion == "success":
        return "green"
    if not a.test_ids:  # test id unknown: job-level failure is the signal
        return "red" if job is not None else "unknown"
    if job is None or run.run_id not in inspected:
        return "unknown"
    return "red" if _shows_test(reader, job.job_id, run.run_id, a.test_ids) else "unknown"


def _shows_test(reader: TurnReader, job_id: str, run_id: str, test_ids: Sequence[str]) -> bool:
    for call in reader.ledger.of("history"):
        ids = records.mentioned_ids(call.arguments)
        if job_id not in ids:
            continue
        text = records.log_text(call.text) or "\n".join(x.as_log_line() for x in records.annotations(call.text))
        if any(excerpt.lines_with(text, t) for t in test_ids):
            return True
    return False
