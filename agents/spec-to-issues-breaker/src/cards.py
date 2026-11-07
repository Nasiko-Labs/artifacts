"""The create walk: one card per write, only for picked issues, in dependency order; a new project's card comes
first when the picked issues need it. Each card is its own pause and its own turn, and an approval allows
exactly one write through the send guard. Cancel stops the walk (nothing is deleted)."""

from __future__ import annotations

from typing import Any

import approval
import outcome
import render
import replies
import writes
from deps import TurnDeps
from outcome import Outcome, View
from proposal import creation_order
from records import issues_to
from send_guard import Approval


async def start(deps: TurnDeps, state: dict[str, Any], ids: tuple[int, ...]) -> Outcome:
    view = View(state)
    by_id = {i.local_id: i for i in view.issues}
    chosen = [by_id[n] for n in ids if n in by_id and not by_id[n].exists]
    notes = [f"#{n} already exists in the tracker, so it stays skipped." for n in ids if n in by_id and by_id[n].exists]
    queue: list[Any] = [i.local_id for i in creation_order(chosen)]
    project = view.project
    if queue and project is not None and not project.id:
        if view.get("project_tool"):
            queue = ["project", *queue]
        else:
            notes.append("No project-creating tool is available, so the issues are created without a project.")
            state = {**state, "project": None}
    state = {**state, "phase": "card", "picked": list(ids), "queue": queue, "index": 0, "reshown": False,
             "approval": None, "notes": [*view.notes, *notes]}
    return show_card(deps, state, " ".join(notes))


def show_card(deps: TurnDeps, state: dict[str, Any], lead: str = "") -> Outcome:
    queue, index = list(state.get("queue") or ()), int(state.get("index") or 0)
    if index >= len(queue):
        return finish(state)
    view = View(state)
    stored = Approval.from_dict(state.get("approval"))
    step = queue[index]
    current = stored or writes.approval_for(view, step)
    issue_steps = [s for s in queue if s != "project"]
    if step == "project":
        header = "Create project?"
        body = render.project_card(view.project, view.team, len(issue_steps))
    else:
        n = issue_steps.index(step) + 1
        header = f"Create issue {n} of {len(issue_steps)}?"
        body = render.issue_card(n, len(issue_steps), current.title, current.description, current.estimate
                                 if current.estimate is not None else _shown_estimate(view, step), view.team,
                                 view.project if view.project and view.project.id else None)
    hitl = approval.card_question(header, allow_edit=not state.get("reshown"))
    text = f"{lead}\n\n{body}" if lead else body
    return outcome.pause(text, {**state, "approval": current.to_dict()}, hitl)


async def resume_card(deps: TurnDeps, state: dict[str, Any], text: str) -> Outcome:
    answer = approval.read_card(text)
    if answer.kind == "cancel":
        return finish({**state}, cancelled=True)
    if answer.kind == "skip":
        return advance(deps, writes.mark_current(state, "skipped"))
    if answer.kind == "edit" and not state.get("reshown"):
        current = Approval.from_dict(state.get("approval"))
        edited = writes.edited(current, answer.title, answer.description)
        return show_card(deps, {**state, "approval": edited.to_dict(), "reshown": True}, "Updated as you asked.")
    if answer.kind != "create":
        return show_card(deps, state, replies.CARD_EDIT_HELP)
    result = await writes.create(deps, state)
    return settle(deps, state, result)


async def resume_gateway(deps: TurnDeps, state: dict[str, Any], text: str) -> Outcome:
    result = await writes.resume(deps, state, text)
    return settle(deps, {**state, "phase": "card", "loop": None}, result)


def settle(deps: TurnDeps, state: dict[str, Any], result) -> Outcome:
    if result.status == "paused":
        hitl = {"hitl_request_id": result.hitl_request_id}
        return outcome.pause("The tracker asks you to confirm this write before it runs.",
                             {**state, "phase": "gateway", "loop": result.loop}, hitl, kind="auth_required")
    if result.status == "blocked":
        return finish(writes.mark_current(state, "failed"), blocked=True)
    if result.status == "done":
        return advance(deps, writes.record_done(state, result.output))
    reason = result.reason or "it was not created"
    state = writes.mark_current(state, "failed")
    return advance(deps, {**state, "notes": [*View(state).notes, f"One write did not happen: {reason}."]})


def advance(deps: TurnDeps, state: dict[str, Any]) -> Outcome:
    nxt = {**state, "index": int(state.get("index") or 0) + 1, "reshown": False, "approval": None}
    return show_card(deps, nxt)


def finish(state: dict[str, Any], cancelled: bool = False, blocked: bool = False) -> Outcome:
    view = View(state)
    picked = [int(n) for n in state.get("picked") or ()]
    lead = []
    if cancelled:
        lead.append(f"{replies.CANCELLED} Anything already created stays.")
    if blocked:
        by_id = {i.local_id: i for i in view.issues}
        rest = [by_id[n] for n in picked if n in by_id and by_id[n].status in ("proposed", "failed")]
        lead.append(replies.WRITE_BLOCKED)
        lead.extend(render.plain_issue(i, by_id) for i in rest)
    text = render.report(view.issues, picked, str(state.get("project_line") or ""), view.notes)
    done = {**state, "phase": "done", "issues": issues_to(view.issues)}
    return outcome.complete("\n\n".join([*lead, text]), done, text.splitlines()[0])


def _shown_estimate(view: View, local_id: int) -> int | None:
    return next((i.estimate for i in view.issues if i.local_id == local_id), None)
