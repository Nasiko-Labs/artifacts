"""Graph node, the writes: one resumed turn per approval card. Approve runs exactly the one planned step
through the sender (send guard first); Skip stops and reports what exists; custom text is a revision on
card 1 (before anything is written), a commit-message edit on a commit card, or a title/body edit on the
PR card. A stale card (its step is not the next one, or its digest changed) writes nothing."""

from __future__ import annotations

import re
from dataclasses import replace

import compose
import hitl
import prompts
import render
import sender
from discovery import discover
from send_guard import Approval
from turn_propose import approval_pause, changes_of, diff_only_reply
from turn_read import Scope, end
from writeplan import WritePlan, leftovers

MAX_REVISIONS = 2
_MESSAGE = re.compile(r"^(?:commit )?message\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)
_TITLE = re.compile(r"^title\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)
_BODY = re.compile(r"^body\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)


async def approve_node(state: dict, scope: Scope) -> dict:
    pending = scope.pending
    answer = hitl.answer_text(scope.incoming.text)
    if pending.kind == "diff_only":
        if answer.casefold() in ("show diff", "show the diff") or hitl.is_yes(answer):
            return diff_only_reply(state, scope, "No write tool is granted to this agent.")
        return end(prompts.CANCELLED)
    plan = WritePlan.from_dict(state.get("plan"))
    step = plan.next_step if plan else None
    if step is None or pending.step != plan.done or pending.digest != step.digest():
        return end(prompts.NOT_WAITING)
    if answer == hitl.APPROVE or hitl.is_yes(answer):
        return await _write(state, scope, plan, pending)
    if hitl.is_stop(answer):
        return _stopped(state, scope, plan.stop("skipped"), "")
    return _edit(state, scope, plan, answer)


async def _write(state: dict, scope: Scope, plan: WritePlan, pending: hitl.Pending) -> dict:
    step = plan.next_step
    catalog, failure = await discover(scope.deps, "write")
    if catalog is None:
        return _stopped(state, scope, plan.stop(failure or "the tool gateway is unreachable"), failure or "")
    content = next((c.final for c in changes_of(state) if c.path == step.path), "") if step.kind == "commit" else ""
    result = await sender.execute(scope.deps, catalog, plan, Approval(pending.step, pending.digest), content)
    if result.status != "written":
        return _stopped(state, scope, plan.stop(result.reason), f"That write did not go through: {result.reason}.")
    done = plan.advanced()
    if step.kind == "pr":
        merged = {**state, "plan": done.to_dict()}
        env = render.envelope(merged, "opened", result.url)
        url = result.url or "(the code host did not return a URL; check the repository's pull requests)"
        text = render.as_json(env) if scope.incoming.wants_json else f"Draft PR opened: {url}\n\n{env['summary']}"
        return end(text, data=env, envelope=env, plan=done.to_dict())
    return approval_pause(done, state, scope, render.card_text(done, done.done, state, render.written_note(step)))


def _stopped(state: dict, scope: Scope, plan: WritePlan, why: str) -> dict:
    left = leftovers(plan)
    cleanup = ""
    if plan.done > 0:
        cleanup = (f" Nothing was cleaned up; to remove it, delete branch `{plan.steps[0].branch}` on the code host "
                   "(I never delete branches).")
    merged = {**state, "plan": plan.to_dict(), "leftovers": left}
    env = render.envelope(merged, "skipped")
    text = " ".join(x for x in (why, left + cleanup) if x)
    if plan.done == 0 and not why:
        text = prompts.CANCELLED
    return end(render.as_json(env) if scope.incoming.wants_json else text, data=env, envelope=env,
               plan=plan.to_dict(), leftovers=left)


def _edit(state: dict, scope: Scope, plan: WritePlan, answer: str) -> dict:
    step = plan.next_step
    index = plan.done
    if step.kind == "branch":
        if int(state.get("revisions") or 0) >= MAX_REVISIONS:
            return approval_pause(plan, state, scope, render.card_text(plan, index, state, prompts.REVISION_LIMIT))
        scope.revision = answer
        return {"route": "change"}
    if step.kind == "commit":
        match = _MESSAGE.match(answer)
        if not match:
            return approval_pause(plan, state, scope, render.card_text(plan, index, state, prompts.DIFF_FIXED))
        edited = plan.with_step(index, replace(step, message=" ".join(match.group(1).split())[:200]))
        return approval_pause(edited, state, scope, render.card_text(edited, index, state, "Commit message updated."))
    title, body = _TITLE.match(answer), _BODY.match(answer)
    if title and len(" ".join(title.group(1).split())) <= compose.MAX_TITLE:
        edited = plan.with_step(index, replace(step, title=" ".join(title.group(1).split())))
    elif body:
        edited = plan.with_step(index, replace(step, body=body.group(1).strip()[:8_000]))
    else:
        return approval_pause(plan, state, scope, render.card_text(plan, index, state, prompts.PR_EDIT_HINT))
    return approval_pause(edited, state, scope, render.card_text(edited, index, state, "Pull request updated."))
