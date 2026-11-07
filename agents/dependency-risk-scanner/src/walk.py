"""The upgrade card walk (branch → one commit per manifest → PR), its state on the A2A task between pauses.

    card → [input-required: Approve / Skip / edit] → one guarded write ─┬─ next card (its own pause)
                                                                         ├─ gateway ask → [auth-required]
                                                                         └─ stop: leftovers said plainly

One approval = exactly one write tool call, one write per turn. Skipping any card stops the walk and the
reply says what is left behind; nothing is ever deleted or reverted by the agent. A later card whose
branch, base, path or diff differs from the plan the branch card showed stops the walk. "Open PRs for
all affected" queues at most three repositories and walks them one at a time.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import pr_prep
import records
import writes
from deps import TurnDeps
from scan import Scan
from send_guard import Approval
from sender import SendResult, resume_send, send_one
from writes import Plan

log = logging.getLogger("dependency_risk.walk")

METADATA_KEY = "dependency_risk_scanner"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
STAGES = ("branch", "commit", "pr")


@dataclass(frozen=True)
class Walk:
    plan: Plan
    stage: str                       # branch | commit | pr
    index: int = 0                   # which file the commit card is for
    gateway: bool = False            # paused on the platform's own approval card
    approval: Approval | None = None
    loop: dict | None = None
    branch_created: bool = False
    commits: int = 0
    done: bool = False
    queue: tuple[str, ...] = ()      # repositories still to walk, in order
    scan: dict = field(default_factory=dict)
    prs: tuple = ()                  # finished PR records of earlier repositories in this walk

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"plan": self.plan.to_dict(), "stage": self.stage, "index": self.index,
                               "gateway": self.gateway, "approval": self.approval.to_dict() if self.approval else None,
                               "loop": self.loop, "branch_created": self.branch_created, "commits": self.commits,
                               "done": self.done, "queue": list(self.queue), "scan": self.scan,
                               "prs": [dict(p) for p in self.prs]}}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        return Walk(Plan.from_dict(data.get("plan") or {}), str(data.get("stage") or ""), int(data.get("index") or 0),
                    bool(data.get("gateway")), Approval.from_dict(data.get("approval")), data.get("loop") or None,
                    bool(data.get("branch_created")), int(data.get("commits") or 0), bool(data.get("done")),
                    tuple(data.get("queue") or ()), dict(data.get("scan") or {}),
                    tuple(dict(p) for p in data.get("prs") or ()))


@dataclass(frozen=True)
class Outcome:
    kind: str                     # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None  # task metadata: the walk, to resume from
    hitl: dict | None = None      # status-message metadata: the question, or the gateway's hitl_request_id
    data: dict | None = None      # the envelope for the DataPart


def start(plan: Plan, preface: str = "", queue: tuple[str, ...] = (), scan: dict | None = None,
          prs: tuple = ()) -> Outcome:
    walk = Walk(plan, "branch", queue=tuple(queue), scan=dict(scan or {}), prs=tuple(prs))
    if plan.can_pr:
        return _show(walk, preface)
    why = ("I have no granted tools to create a branch, commit a file and open a pull request." if plan.files else
           "There is no line I can edit safely, so there is nothing to commit.")
    rest = f"Not done: {', '.join(queue)}." if queue else ""
    return _finish(replace(walk, queue=()), "\n\n".join(x for x in (
        preface, why, "Here is the change to make by hand:\n\n" + writes.copy_text(plan), rest) if x), "copy_only")


def _show(walk: Walk, preface: str = "") -> Outcome:
    if walk.stage == "branch":
        walk = replace(walk, plan=replace(walk.plan, bound=walk.plan.binding()))
    text, hitl = writes.card(walk.plan, walk.stage, walk.index, preface)
    return Outcome("input_required", text, walk.to_metadata(), hitl)


async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome:
    stored = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(stored, Mapping) or not stored.get("plan"):
        return Outcome("complete", NOT_WAITING)
    walk = Walk.from_metadata(stored)
    if walk.done or walk.stage not in STAGES:
        return Outcome("complete", NOT_WAITING)
    outcome = await _step(deps, walk, answer)
    return await _next_repo(deps, outcome)


async def _step(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    if walk.stage != "branch" and walk.plan.bound != walk.plan.binding():
        return _stop(walk, "The plan changed after the branch card was shown (branch, base, path or diff differ), "
                           "so I stopped without writing.")
    if walk.stage == "commit" and walk.index >= len(walk.plan.files):
        return _stop(walk, "The pause state was incomplete, so nothing more was written.")
    if walk.gateway:
        if walk.approval is None or not walk.loop:  # fail closed: never guess what was approved
            return _stop(walk, "The pause state was incomplete, so nothing more was written.")
        return _after(walk, await resume_send(deps, walk.approval, walk.loop, answer))
    decision, text = writes.interpret(answer)
    if decision == "skip":
        return _stop(walk, "Skipped.")
    if decision == "edit":
        plan, problem = writes.apply_edit(walk.plan, walk.stage, text)
        return _show(replace(walk, plan=plan), problem)
    approval = writes.approval(walk.plan, walk.stage, walk.index)
    log.info("%s approved", walk.stage)
    return _after(replace(walk, approval=approval), await send_one(deps, approval))


def _after(walk: Walk, result: SendResult) -> Outcome:
    if result.status == "paused":
        paused = replace(walk, gateway=True, loop=result.loop)
        return Outcome("auth_required", f"The platform needs your approval before the {walk.stage} write is made.",
                       paused.to_metadata(), {"hitl_request_id": result.hitl_request_id})
    walk = replace(walk, gateway=False, approval=None, loop=None)
    if result.status == "blocked":
        return _stop(walk, "Writing is blocked for this agent, so here is the change to make by hand:\n\n"
                     + writes.copy_text(walk.plan), "copy_only")
    if result.status == "denied":
        return _stop(walk, f"The {walk.stage} write was denied, so it was not made and I won't retry it.")
    if result.status != "sent":
        return _stop(walk, f"The {walk.stage} write was not made: {result.reason or 'unknown reason'}.")
    return _advance(walk, result.text)


def _advance(walk: Walk, text: str) -> Outcome:
    plan, stage = walk.plan, walk.stage
    if stage == "branch":
        made = records.created_ref(text)
        walk = replace(walk, branch_created=True)
        if made and made != plan.branch:
            return _stop(walk, f"The branch was created as `{made}`, not `{plan.branch}` as planned, so I stopped.")
        return _show(replace(walk, stage="commit", index=0), f"Done: branch `{plan.branch}` created.")
    if stage == "commit":
        wanted = plan.files[walk.index]["path"]
        path = records.committed_path(text)
        walk = replace(walk, commits=walk.commits + 1)
        if path and path != wanted:
            return _stop(walk, f"The commit went to `{path}`, not `{wanted}` as planned, so I stopped.")
        done = f"Done: `{wanted}` committed to `{plan.branch}`."
        if walk.index + 1 < len(plan.files):
            return _show(replace(walk, index=walk.index + 1), done)
        return _show(replace(walk, stage="pr"), done)
    number, url = records.created_number(text)
    link = f"#{number} {url}".strip() if number else ""
    return _finish(walk, f"Done: pull request {link or 'opened'} from `{plan.branch}` to `{plan.base}` in {plan.full}.",
                   "opened", url)


def _stop(walk: Walk, reason: str, status: str = "") -> Outcome:
    rest = f"\n\nNot done: {', '.join(walk.queue)} (stopped before them)." if walk.queue else ""
    return _finish(replace(walk, queue=()), f"{reason}\n\n{leftovers(walk)}{rest}", status or _status(walk))


def leftovers(walk: Walk) -> str:
    plan = walk.plan
    cleanup = f"Nothing was cleaned up; to remove it yourself, delete the branch `{plan.branch}` in {plan.full}."
    if walk.commits:
        return f"Left behind: branch `{plan.branch}` has the commit, no PR is open. {cleanup}"
    if walk.branch_created:
        return f"Left behind: branch `{plan.branch}` exists with no commit and no PR. {cleanup}"
    return f"Nothing was written to {plan.full}."


def _status(walk: Walk) -> str:
    return "committed" if walk.commits else "branch_only" if walk.branch_created else "drafted"


def _finish(walk: Walk, text: str, status: str, url: str = "") -> Outcome:
    record = pr_record(walk, status, url)
    done = replace(walk, done=True, gateway=False, approval=None, loop=None, prs=(*walk.prs, record))
    return Outcome("complete", text, done.to_metadata(), None, {"envelope": envelope(done.prs, text)})


def pr_record(walk: Walk, status: str, url: str = "") -> dict:
    plan = walk.plan
    left = [x for x in (("branch " + plan.branch) if walk.branch_created and status != "opened" else "",
                        ("commit on " + plan.branch) if walk.commits and status != "opened" else "") if x]
    return {"repo": plan.full, "branch": plan.branch, "title": plan.title, "body": plan.pr_body,
            "diff": "\n".join(f["diff"] for f in plan.files), "status": status, "leftovers": left, "url": url or None}


def envelope(prs: tuple, text: str) -> dict:
    opened = all(p["status"] == "opened" for p in prs)
    return {"summary": text.split("\n", 1)[0], "items": [], "confidence": "high" if opened else "medium",
            "needs_human": not opened, "exposed_repos": [], "prs": [dict(p) for p in prs]}


async def _next_repo(deps: TurnDeps, outcome: Outcome) -> Outcome:
    """After one repository's PR opened, prepare the next queued one and show its branch card."""
    stored = (outcome.metadata or {}).get(METADATA_KEY)
    if outcome.kind != "complete" or not isinstance(stored, Mapping) or not stored.get("queue"):
        return outcome
    walk = Walk.from_metadata(stored)
    repo, rest = walk.queue[0], walk.queue[1:]
    plan = await pr_prep.prepare(deps, Scan.from_dict(walk.scan), repo)
    if isinstance(plan, str):
        tail = f"\n\nNot done: {', '.join(rest)}." if rest else ""
        return replace(outcome, text=f"{outcome.text}\n\n{repo}: {plan}{tail}")
    return start(plan, f"{outcome.text}\n\nNext: {repo}.", rest, walk.scan, walk.prs)
