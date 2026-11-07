"""The four-card write walk (ticket → branch → commit → PR), its state on the A2A task between pauses.

    card → [input-required: Approve / Skip / edit] → one guarded write ─┬─ next card (its own pause)
                                                                         ├─ gateway ask → [auth-required]
                                                                         └─ stop: leftovers said plainly

One approval = exactly one write tool call, one write per turn. Skipping the ticket keeps going (the skip
marker says TBD); skipping any later card stops, and the reply says what is left behind. Nothing is ever
deleted or reverted by the agent. A later card whose branch, base, path or diff differs from the plan the
branch card showed stops the walk.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import records
import writes
from deps import TurnDeps
from send_guard import Approval
from sender import SendResult, resume_send, send_one
from writes import Plan

log = logging.getLogger("flaky_hunter.walk")

METADATA_KEY = "flaky_test_hunter"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
NEXT = {"ticket": "branch", "branch": "commit", "commit": "pr", "pr": ""}


@dataclass(frozen=True)
class Walk:
    plan: Plan
    stage: str                       # ticket | branch | commit | pr
    gateway: bool = False            # paused on the platform's own approval card
    approval: Approval | None = None
    loop: dict | None = None
    branch_created: bool = False
    committed: bool = False
    done: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"plan": self.plan.to_dict(), "stage": self.stage, "gateway": self.gateway,
                               "approval": self.approval.to_dict() if self.approval else None, "loop": self.loop,
                               "branch_created": self.branch_created, "committed": self.committed, "done": self.done}}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        return Walk(Plan.from_dict(data.get("plan") or {}), str(data.get("stage") or ""), bool(data.get("gateway")),
                    Approval.from_dict(data.get("approval")), data.get("loop") or None,
                    bool(data.get("branch_created")), bool(data.get("committed")), bool(data.get("done")))


@dataclass(frozen=True)
class Outcome:
    kind: str                     # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None  # task metadata: the walk, to resume from
    hitl: dict | None = None      # status-message metadata: the question, or the gateway's hitl_request_id
    data: dict | None = None      # the envelope for the DataPart


def start(plan: Plan, preface: str = "", skip_ticket: bool = False) -> Outcome:
    if plan.tools.get("issue") or plan.tools.get("comment"):
        if not skip_ticket:
            return _show(Walk(plan, "ticket"), preface)
        preface = "\n\n".join(x for x in (preface, "Skipping the ticket, as asked; the skip marker will say TBD.") if x)
    else:
        preface = "\n\n".join(x for x in (preface, ("I have no granted tool to open an issue; here is the ticket text "
                                                    f"to file yourself:\n\nTitle: {plan.issue_title}\n\n{plan.body}")) if x)
    return _to_branch(Walk(plan, "ticket"), preface)


def _show(walk: Walk, preface: str = "") -> Outcome:
    if walk.stage == "branch":
        walk = replace(walk, plan=replace(walk.plan, bound=walk.plan.binding()))
    text, hitl = writes.card(walk.plan, walk.stage, preface)
    return Outcome("input_required", text, walk.to_metadata(), hitl)


def _to_branch(walk: Walk, preface: str) -> Outcome:
    plan = walk.plan
    if plan.can_pr:
        return _show(replace(walk, stage="branch"), preface)
    p = plan.patch()
    why = p if isinstance(p, str) else "I have no granted tools to create a branch, commit a file and open a pull request."
    copy = writes.copy_text(plan) if not isinstance(p, str) else ""
    text = "\n\n".join(x for x in (preface, why, ("Here is the change to make by hand:\n\n" + copy) if copy else "") if x)
    return _finish(walk, text, "copy_only" if copy else "drafted")


async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome:
    stored = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(stored, Mapping) or not stored.get("plan"):
        return Outcome("complete", NOT_WAITING)
    walk = Walk.from_metadata(stored)
    if walk.done or walk.stage not in NEXT:
        return Outcome("complete", NOT_WAITING)
    if walk.stage in ("commit", "pr") and walk.plan.bound != walk.plan.binding():
        return _stop(walk, "The plan changed after the branch card was shown (branch, base, path or diff differ), "
                           "so I stopped without writing.")
    if walk.gateway:
        if walk.approval is None or not walk.loop:  # fail closed: never guess what was approved
            return _stop(walk, "The pause state was incomplete, so nothing more was written.")
        return _after(walk, await resume_send(deps, walk.approval, walk.loop, answer))
    decision, text = writes.interpret(answer)
    if decision == "skip":
        return _skipped(walk)
    if decision == "edit":
        plan, problem = writes.apply_edit(walk.plan, walk.stage, text)
        if walk.stage == "commit" and not problem:
            plan = replace(plan, bound=plan.binding())
        return _show(replace(walk, plan=plan), problem)
    approval = writes.approval(walk.plan, _kind(walk))
    log.info("%s approved", walk.stage)
    return _after(replace(walk, approval=approval), await send_one(deps, approval))


def _kind(walk: Walk) -> str:
    if walk.stage == "ticket":
        return "comment" if walk.plan.existing_issue else "issue"
    return walk.stage


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
    if stage == "ticket":
        number, url = records.created_number(text)
        number = number if not plan.existing_issue else plan.existing_issue
        plan = replace(plan, ticket=f"#{number}" if number else "TBD", ticket_url=url)
        done = f"Done: issue #{number} {url}".strip() if number else "Done: the ticket was written."
        return _to_branch(replace(walk, plan=plan), done)
    if stage == "branch":
        made = records.created_ref(text)
        walk = replace(walk, branch_created=True)
        if made and made != plan.branch:
            return _stop(walk, f"The branch was created as `{made}`, not `{plan.branch}` as planned, so I stopped.")
        return _show(replace(walk, stage="commit"), f"Done: branch `{plan.branch}` created.")
    if stage == "commit":
        path = records.committed_path(text)
        walk = replace(walk, committed=True)
        if path and path != plan.path:
            return _stop(walk, f"The commit went to `{path}`, not `{plan.path}` as planned, so I stopped.")
        return _show(replace(walk, stage="pr"), f"Done: `{plan.path}` committed to `{plan.branch}`.")
    number, url = records.created_number(text)
    link = f"#{number} {url}".strip() if number else ""
    return _finish(walk, f"Done: draft pull request {link or 'opened'} from `{plan.branch}` to `{plan.base}`.",
                   "opened", url)


def _skipped(walk: Walk) -> Outcome:
    if walk.stage == "ticket":
        return _to_branch(replace(walk, plan=replace(walk.plan, ticket="TBD")), "Skipped the ticket; the skip marker "
                          "will say TBD.")
    return _stop(walk, "Skipped.")


def _stop(walk: Walk, reason: str, status: str = "") -> Outcome:
    return _finish(walk, f"{reason}\n\n{leftovers(walk)}", status or _status(walk))


def leftovers(walk: Walk) -> str:
    plan = walk.plan
    owner_repo = plan.full
    if walk.committed:
        return (f"Left behind: branch `{plan.branch}` has the commit, no PR is open. Nothing was cleaned up; to "
                f"remove it yourself, delete the branch `{plan.branch}` in {owner_repo}.")
    if walk.branch_created:
        return (f"Left behind: branch `{plan.branch}` exists with no commit and no PR. Nothing was cleaned up; to "
                f"remove it yourself, delete the branch `{plan.branch}` in {owner_repo}.")
    ticket = f" (the ticket {plan.ticket} stays open)" if plan.ticket != "TBD" else ""
    return f"Nothing was written for the pull request{ticket}."


def _status(walk: Walk) -> str:
    return "committed" if walk.committed else "branch_only" if walk.branch_created else "drafted"


def _finish(walk: Walk, text: str, status: str, url: str = "") -> Outcome:
    done = replace(walk, done=True, gateway=False, approval=None, loop=None)
    return Outcome("complete", text, done.to_metadata(), None, {"draft_pr": draft_pr(done, status, url)})


def draft_pr(walk: Walk, status: str, url: str = "") -> dict:
    plan, p = walk.plan, walk.plan.patch()
    left = [] if status == "opened" else [x for x in (("branch " + plan.branch) if walk.branch_created else "",
                                                      ("commit on " + plan.branch) if walk.committed else "") if x]
    return {"status": status, "leftovers": left, "branch": plan.branch, "title": plan.title, "body": plan.pr_body,
            "path": plan.path, "diff": getattr(p, "diff", ""), "url": url, "ticket": plan.ticket}
