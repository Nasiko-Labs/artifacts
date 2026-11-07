"""Turn 1's data gathering: runs → pick the failed attempts (code) → jobs + logs in batches → extract (code)
→ the commit check for main-branch flips → rank (code). The model only drives the tools; every fact is
read by code out of the turn's ledger."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import history
import prompts
import records
import testids
from deps import TurnDeps
from discovery import Discoverer
from reader import TurnReader

log = logging.getLogger("flaky_hunter.scan")

WINDOW_DAYS = 30
MAX_FAILED, MAX_RERUNS, BATCH, MAX_BATCHES = 12, 6, 5, 3
RUNS_CALLS, RUNS_SECS = 2, 60.0
LOGS_CALLS, LOGS_SECS = 2, 150.0
COMMITS_CALLS, COMMITS_SECS, MAX_PATHS = 2, 60.0, 5


@dataclass(frozen=True)
class Params:
    repo: str = ""
    workflow: str = ""
    branch: str = ""
    runs: int = 30

    def to_dict(self) -> dict:
        return {"repo": self.repo, "workflow": self.workflow, "branch": self.branch, "runs": self.runs}

    @staticmethod
    def from_dict(data) -> Params:
        data = data if isinstance(data, dict) else {}
        return Params(str(data.get("repo") or ""), str(data.get("workflow") or ""), str(data.get("branch") or ""),
                      int(data.get("runs") or 30))


@dataclass(frozen=True)
class Item:
    run: records.Run
    attempt: int


@dataclass(frozen=True)
class Scan:
    params: Params
    branch: str
    workflow: str
    base: str                       # the repository's default branch, from tool output ("" if unseen)
    base_sha: str                   # newest kept run's head commit on the base branch
    runs: tuple[records.Run, ...]
    failed: int
    inspected: int
    not_inspected: int
    unattributed: int
    ranking: history.Ranking
    notes: tuple[str, ...] = ()
    pasted: bool = False


async def scan(deps: TurnDeps, params: Params) -> Scan | str:
    """The ranked report, or the reply that ends the turn."""
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return failure
    reader = TurnReader(deps, discoverer)
    since = deps.now - timedelta(days=WINDOW_DAYS)
    step = await reader.run("runs", prompts.runs_system(params.repo, params.workflow, params.branch, params.runs,
                                                        since, deps.now), "List them.", RUNS_CALLS, RUNS_SECS)
    if step.status in ("no_tools", "paused"):
        return prompts.NO_CI_TOOL if step.status == "no_tools" else step.note
    texts = [c.text for c in reader.ledger.of("runs")]
    base = next((b for b in map(records.default_branch, texts) if b), "")
    all_runs = _latest([r for t in texts for r in records.runs(t)])
    kept, branch, workflow = _keep(all_runs, params, base, since)
    notes = tuple(n for n in (step.note,) if n)
    if not all_runs:
        return "\n\n".join([prompts.NO_RUN_DATA.format(repo=params.repo), *notes])
    if not kept:
        where = f"the workflow \"{params.workflow}\"" if params.workflow else "any workflow"
        return "\n\n".join([(f"I found no completed runs of {where} on {branch or 'the default branch'} of "
                             f"{params.repo} in the last {WINDOW_DAYS} days."), *notes])
    failed = [r for r in kept if r.conclusion in history.RED]
    reruns = [r for r in kept if r.attempt > 1]
    items = [Item(r, r.attempt - 1) for r in reruns[:MAX_RERUNS]] + [Item(r, r.attempt) for r in failed[:MAX_FAILED]]
    capacity = BATCH * MAX_BATCHES
    skipped = max(0, len(failed) - MAX_FAILED) + max(0, len(reruns) - MAX_RERUNS) + max(0, len(items) - capacity)
    items = items[:capacity]
    attempts, unattributed, log_notes = await _read_logs(reader, params.repo, items)
    base_sha = next((r.head_sha for r in kept if r.branch == (base or branch)), "")
    scan_result = Scan(params, branch, workflow, base or branch, base_sha, tuple(kept), len(failed), len(items),
                       skipped, unattributed, history.Ranking(), (*notes, *log_notes))
    touches, commit_notes = await _touches(reader, params.repo, kept, attempts, deps.now, since)
    ranking = history.analyse(kept, attempts, touches, deps.now)
    return replace(scan_result, ranking=ranking, notes=(*scan_result.notes, *commit_notes))


def _latest(runs: list[records.Run]) -> list[records.Run]:
    """One row per run: the highest attempt seen."""
    best: dict[str, records.Run] = {}
    for r in runs:
        if r.run_id not in best or r.attempt > best[r.run_id].attempt:
            best[r.run_id] = r
    return list(best.values())


def _keep(runs: list[records.Run], params: Params, base: str, since: datetime):
    """Completed runs inside the window on the branch, of the chosen workflow; newest first, at most N."""
    branch = params.branch or base or _most(r.branch for r in runs if r.event in ("push", ""))
    pool = [r for r in runs if r.conclusion and r.branch == branch and _inside(r, since)]
    if params.workflow:
        wanted = params.workflow.casefold()
        pool = [r for r in pool if wanted in r.workflow.casefold()]
        workflow = next((r.workflow for r in pool), params.workflow)
    else:
        fails = Counter(r.workflow for r in pool if r.conclusion in history.RED)
        totals = Counter(r.workflow for r in pool)
        workflow = min(totals, key=lambda w: (-fails[w], -totals[w], w), default="")
        pool = [r for r in pool if r.workflow == workflow]
    pool = sorted(pool, key=lambda r: r.created_at, reverse=True)[:params.runs]
    return pool, branch, workflow


def _most(values) -> str:
    counts = Counter(v for v in values if v)
    return min(counts, key=lambda v: (-counts[v], v), default="")


def _inside(run: records.Run, since: datetime) -> bool:
    created = records.parse_time(run.created_at)
    return created is not None and created >= since


async def _read_logs(reader: TurnReader, repo: str, items: list[Item]):
    if not items:
        return [], 0, ()
    batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
    steps = await asyncio.gather(*(
        reader.run("logs", prompts.logs_system(repo, _describe(batch), reader.deps.now), "Fetch them.", LOGS_CALLS,
                   LOGS_SECS, tag=f"logs{i}") for i, batch in enumerate(batches)))
    notes = tuple(dict.fromkeys(s.note for s in steps if s.note))
    calls = reader.ledger.of(*(f"logs{i}" for i in range(len(batches))))
    jobs = _jobs(calls, items)
    attempts, unattributed = [], 0
    for item in items:
        failures = _failures(calls, [j for j, attempt in jobs if j.run_id == item.run.run_id
                                     and attempt == item.attempt and j.conclusion in history.RED])
        if failures:
            attempts.append(history.Attempt(item.run.run_id, item.attempt, failures))
        else:
            unattributed += 1
    return attempts, unattributed, notes


def _describe(batch: list[Item]) -> str:
    return "\n".join(f"- run {i.run.run_id}, attempt {i.attempt}" + (" (an earlier attempt of a rerun)"
                                                                     if i.attempt < i.run.attempt else "")
                     for i in batch)


def _jobs(calls, items: list[Item]) -> list[tuple[records.Job, int]]:
    """Every job listed, with the attempt it belongs to (the job's own field, else the listing's arguments,
    else the only attempt asked for that run)."""
    asked: dict[str, set[int]] = {}
    for i in items:
        asked.setdefault(i.run.run_id, set()).add(i.attempt)
    out: dict[str, tuple[records.Job, int]] = {}
    for call in calls:
        arg_attempt = next((int(v) for k, v in call.arguments.items() if "attempt" in k and str(v).isdigit()), 0)
        for job in records.jobs(call.text):
            only = next(iter(asked.get(job.run_id, ())), 0) if len(asked.get(job.run_id, ())) == 1 else 0
            attempt = job.attempt or arg_attempt or only or max(asked.get(job.run_id, {1}))
            out.setdefault(f"{job.job_id}:{attempt}", (job, attempt))
    return list(out.values())


def _failures(calls, jobs: list[records.Job]) -> tuple[testids.Failure, ...]:
    found: dict[str, testids.Failure] = {}
    for job in jobs:
        for call in calls:
            if job.job_id not in records.mentioned_ids(call.arguments) or records.jobs(call.text):
                continue
            raw = records.log_text(call.text) or "\n".join(records.annotation_lines(call.text))
            for f in testids.failures(raw):
                found.setdefault(f.test_id, replace(f, line=testids.verbatim_line(f, raw)))
    return tuple(found.values())


async def _touches(reader: TurnReader, repo: str, runs, attempts, now: datetime, since: datetime):
    """Commit dates per test file, for the tests whose main-branch flips need the commit check. None when
    no commit tool is available (those flips stay unverified)."""
    pre = history.analyse(runs, attempts, None, now)
    paths = list(dict.fromkeys(f.file for f in pre.flaky if f.unverified and f.file))[:MAX_PATHS]
    if not paths:
        return {}, ()
    step = await reader.run("commits", prompts.commits_system(repo, ", ".join(paths), since, now), "List them.",
                            COMMITS_CALLS, COMMITS_SECS)
    if step.status == "no_tools":
        return None, ("No commit-history tool is available, so main-branch flips are unverified.",)
    touches: dict[str, tuple[datetime, ...]] = {}
    for call in reader.ledger.of("commits"):
        named = [p for p in paths if p in records.mentioned_ids(call.arguments)]
        dates = tuple(d for c in records.commits(call.text) if (d := records.parse_time(c.date)))
        for p in named:
            touches[p] = (*touches.get(p, ()), *dates)
    return touches, tuple(n for n in (step.note,) if n)
