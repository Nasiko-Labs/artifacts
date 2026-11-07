"""The conversational nodes: intake (which run, read in code first), the follow-up on a report, and the
write preparation. Every prompt that reads the user's words carries the scope rule."""

from __future__ import annotations

import logging

import prompts
import smalltalk
import targets
import writes
from analysis import Report
from deps import TurnDeps
from reader import ask_once
from steps import load_object
from targets import Target

log = logging.getLogger("ci_detective.intake")

_MAX_FOLLOWUP_CHARS = 9_000


def names_new_target(state: dict) -> bool:
    """A run or pull request other than the one last reported ("post it on PR 42" is a follow-up)."""
    target = targets.parse(state.get("query") or "", state.get("repo") or "")
    report = Report.from_dict(state.get("report"))
    if target is None or report is None:
        return target is not None
    same_repo = target.repo_full == report.repo_full
    same = (target.kind == "pr" and target.number == report.pr_number) or target.run_id == report.run_id
    return not (same_repo and same)


async def intake(deps: TurnDeps, state: dict) -> dict:
    query, remembered = state["query"], state.get("repo") or ""
    target = targets.parse(query, remembered)
    pending = Target.from_dict(state.get("pending"))
    if target is None and pending is not None and targets.repo_in(query):
        target = targets.with_repo(pending, targets.repo_in(query))
    if target is None:
        if not deps.traceparent:
            return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
        answer = load_object(await ask_once(deps, ({"role": "system", "content": prompts.INTAKE_SYSTEM},
                                                   {"role": "user", "content": query}))) or {}
        if answer.get("status") == "off_topic":
            return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
        target = targets.from_model(answer, remembered) if answer.get("status") == "ok" else None
        if target is None:
            return {"reply": prompts.ASK_TARGET, "route": ""}
    if not target.has_repo:
        return {"reply": prompts.ASK_REPO.format(number=target.number or target.run_id), "pending": target.to_dict(),
                "route": ""}
    return _go(target)


def _go(target: Target) -> dict:
    return {"target": target.to_dict(), "repo": target.repo_full, "pending": {}, "route": "locate"}


async def followup(deps: TurnDeps, state: dict) -> dict:
    report = Report.from_dict(state.get("report"))
    if report is None:
        return {"reply": prompts.NO_REPORT, "route": ""}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    job = targets.job_name_in(state["query"])
    if job:
        return _other_job(report, job)
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_card(_context(report))}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": state["query"]}))
    word = text.strip().strip("`.").strip()
    if word.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if word in (prompts.POST, prompts.POST_MENTION):
        return {"write": "comment", "route": "write"}
    if word == prompts.TICKET:
        return {"write": "ticket", "route": "write"}
    if word == prompts.RECHECK:
        target = Target.from_dict(report.target)
        return _go(target) if target else {"reply": prompts.NO_REPORT, "route": ""}
    if word.startswith(prompts.OTHER_JOB):
        return _other_job(report, word[len(prompts.OTHER_JOB):].strip())
    return {"reply": text or "I couldn't read that; ask again about the report.", "route": ""}


def _other_job(report: Report, name: str) -> dict:
    known = next((j for j in report.other_jobs if j.casefold() == name.casefold()), "")
    if not known:
        listed = ", ".join(report.other_jobs) or "none"
        return {"reply": f"Run {report.run_id} has no other failed job called \"{name}\". Other failed jobs: {listed}.",
                "route": ""}
    target = Target("run", report.owner, report.repo, run_id=report.run_id, job_name=known)
    return _go(target)


def _context(report: Report) -> str:
    others = ", ".join(report.other_jobs) or "none"
    text = f"{report.card}\n\nOther failed jobs: {others}\n\nFull log excerpt:\n{report.excerpt}"
    return text[:_MAX_FOLLOWUP_CHARS]


async def write(deps: TurnDeps, state: dict) -> dict:
    report = Report.from_dict(state.get("report"))
    if report is None:
        return {"reply": prompts.NO_REPORT, "route": ""}
    outcomes = state.get("outcomes") or {}
    prepare = writes.prepare_ticket if state.get("write") == "ticket" else writes.prepare_comment
    prepared = await prepare(deps, report, outcomes)
    if prepared.proposal is None:
        return {"reply": prepared.reply, "route": ""}
    return {"reply": "", "proposal": prepared.proposal.to_dict(), "route": ""}

