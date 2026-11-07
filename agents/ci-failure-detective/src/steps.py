"""The locate and log steps: find the failed run and its failing job, then read the job's log (or the
check annotations). The model drives the tools; code reads the facts out of the raw tool output."""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import classify
import excerpt
import prompts
import records
from analysis import Analysis
from reader import TurnReader
from records import Job, Run

log = logging.getLogger("ci_detective.steps")

LOCATE_CALLS, LOCATE_SECS = 3, 60.0
LOG_CALLS, LOG_SECS = 2, 60.0
FAILED = ("failure", "timed_out")
RUNNING = ("queued", "in_progress", "pending", "waiting", "requested")


async def locate(reader: TurnReader, analysis: Analysis, query: str) -> Analysis | str:
    """The failed run and job for the target, or the reply that ends the turn."""
    target = analysis.target
    step = await reader.run("locate", prompts.locate_system(target.describe(), reader.deps.now), query,
                            LOCATE_CALLS, LOCATE_SECS)
    if step.status in ("no_tools", "paused"):
        return step.note
    seen = reader.ledger.seen
    texts = [c.text for c in reader.ledger.of("locate")]
    pr = next((p for t in texts for p in records.pull_requests(t) if p.number == target.number), None)
    runs = _merge_runs([r for t in texts for r in records.runs(t)] + _model_runs(step.text, seen))
    jobs = _unique_jobs([j for t in texts for j in records.jobs(t)] + _model_jobs(step.text, seen))
    run = _pick_run(target, pr, runs, jobs)
    notes = (*analysis.notes, *((step.note,) if step.note else ()))
    if run is None:
        return _no_failed_run(target, pr, runs, notes)
    run_jobs = [j for j in jobs if j.run_id == run.run_id]
    job = _pick_job(target, run_jobs)
    others = tuple(j.name for j in _failed_jobs(run_jobs) if job is None or j.job_id != job.job_id)
    if job is None:
        job = Job(job_id="", run_id=run.run_id, name="(failed job)", conclusion=run.conclusion)
        notes = (*notes, "I couldn't list the run's jobs, so the failing job is not named.")
    return replace(analysis, pr=pr, run=run, job=job, other_jobs=others, notes=notes)


def _pick_run(target, pr, runs: list[Run], jobs: list[Job]) -> Run | None:
    if target.kind == "job" and not target.run_id:
        run_id = next((j.run_id for j in jobs if j.job_id == target.job_id), "")
    else:
        run_id = target.run_id
    if run_id:
        return next((r for r in runs if r.run_id == run_id), None)
    if target.kind == "pr":
        pool = [r for r in runs if (pr and r.head_sha == pr.head_sha) or target.number in r.pr_numbers]
    else:
        pool = [r for r in runs if not target.branch or r.branch == target.branch]
    failed = [r for r in pool if r.conclusion in FAILED]
    return max(failed, key=lambda r: r.created_at, default=None)


def _no_failed_run(target, pr, runs: list[Run], notes: tuple[str, ...]) -> str:
    sha = pr.head_sha[:7] if pr else ""
    pool = [r for r in runs if not pr or r.head_sha == pr.head_sha]
    if target.run_id:
        found = next((r for r in runs if r.run_id == target.run_id), None)
        reply = (f"Run {target.run_id} didn't fail (its conclusion is {found.conclusion or found.status})."
                 if found else f"I couldn't find run {target.run_id} in {target.repo_full}.")
    elif any(r.status in RUNNING or not r.conclusion for r in pool):
        reply = f"The latest run for {sha or target.describe()} is still running."
    elif pool:
        reply = f"The latest run for {sha or target.describe()} didn't fail."
    else:
        reply = f"I couldn't find a CI run for {target.describe()}."
    return "\n\n".join([reply, *notes])


def _pick_job(target, jobs: list[Job]) -> Job | None:
    if target.job_id:
        return next((j for j in jobs if j.job_id == target.job_id), None)
    if target.job_name:
        wanted = target.job_name.casefold()
        return next((j for j in jobs if j.name.casefold() == wanted), None) or next(
            (j for j in jobs if wanted in j.name.casefold()), None)
    failed = _failed_jobs(jobs)
    return failed[0] if failed else None


def _failed_jobs(jobs: list[Job]) -> list[Job]:
    return sorted((j for j in jobs if j.conclusion in FAILED), key=lambda j: (j.started_at, j.job_id))


async def read_log(reader: TurnReader, analysis: Analysis) -> Analysis:
    run, job, target = analysis.run, analysis.job, analysis.target
    system = prompts.log_system(target.repo_full, run.run_id, job.job_id or "(unknown)", job.name, reader.deps.now)
    step = await reader.run("log", system, "Fetch it.", LOG_CALLS, LOG_SECS)
    calls = reader.ledger.of("log", "locate")
    about_job = [c for c in calls if job.job_id and job.job_id in records.mentioned_ids(c.arguments)]
    raw = next((text for c in (*about_job, *calls) if c.step == "log" and (text := _log_of(c.text))), "")
    notes = (*analysis.notes, *((step.note,) if step.note else ()))
    source = "log"
    if not raw:
        raw, source, note = _annotations_fallback(calls, job)
        notes = (*notes, note)
    failure_class, line = classify.classify(raw, job.conclusion)
    return replace(analysis, log_raw=raw, log_source=source, excerpt=excerpt.excerpt(raw),
                   test_ids=excerpt.test_ids(raw), locations=excerpt.locations(raw),
                   failure_class=failure_class, class_line=line, notes=notes)


def _log_of(text: str) -> str:
    """The log in one tool result; an annotations listing is never mistaken for one."""
    return "" if records.annotations(text) else records.log_text(text)


def _annotations_fallback(calls, job: Job) -> tuple[str, str, str]:
    found = [a for c in calls for a in records.annotations(c.text)]
    archived = any(records.archive_url(c.text) for c in calls if c.step == "log")
    why = ("Full job log not readable by this connection (it comes as an archive)" if archived
           else "Full job log not readable by this connection")
    if not found:
        step = f" The failed step was \"{job.failed_step}\"." if job.failed_step else ""
        return "", "", f"{why}, and no check annotations were found.{step}"
    lines = [a.as_log_line() for a in dict.fromkeys(found)]
    if job.failed_step:
        lines = [*lines, f"Failed step: {job.failed_step}"]
    return "\n".join(lines), "annotations", f"{why}; used check annotations."


def _merge_runs(runs: list[Run]) -> list[Run]:
    by_key: dict[tuple[str, str], Run] = {}
    for r in runs:
        by_key.setdefault((r.run_id, r.attempt), r)
    return list(by_key.values())


def _unique_jobs(jobs: list[Job]) -> list[Job]:
    by_id: dict[str, Job] = {}
    for j in jobs:
        by_id.setdefault(j.job_id, j)
    return list(by_id.values())


def _model_runs(text: str, seen: str) -> list[Run]:
    """The model's own run list: a fallback, each run id and head commit checked verbatim."""
    out = []
    for r in _model_list(text, "runs"):
        run_id, sha = str(r.get("run_id") or ""), str(r.get("head_sha") or "")
        if records.verbatim(run_id, seen) and records.verbatim(sha, seen):
            out.append(Run(run_id, str(r.get("workflow") or ""), "", sha, str(r.get("branch") or ""),
                           str(r.get("created_at") or ""), str(r.get("conclusion") or ""), url=str(r.get("url") or "")))
    return out


def _model_jobs(text: str, seen: str) -> list[Job]:
    out = []
    for r in _model_list(text, "runs"):
        for j in r.get("jobs") or ():
            job_id = str(j.get("job_id") or "") if isinstance(j, dict) else ""
            if records.verbatim(job_id, seen):
                out.append(Job(job_id, str(r.get("run_id") or ""), str(j.get("name") or ""),
                               str(j.get("conclusion") or ""), str(j.get("started_at") or "")))
    return out


def _model_list(text: str, key: str) -> list[dict]:
    data = records.parse_json(_strip_fence(text))
    items = data.get(key) if isinstance(data, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _strip_fence(text: str) -> str:
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body.removeprefix("json")
    return body


def load_object(text: str) -> dict | None:
    data = records.parse_json(_strip_fence(text))
    if isinstance(data, dict):
        return data
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            return None
    return data if isinstance(data, dict) else None
