"""Graph nodes, the proposal: propose (one model call for the PR title and body, then the write plan and
card 1, or the diff only) and followup ("what did you change?" from stored state, zero calls)."""

from __future__ import annotations

import compose
import hitl
import prompts
import render
import tests_runner as tr
import writeplan
from constraints import Constraints
from diffing import FileChange, full_diff
from discovery import discover
from turn_read import Scope, end, issue_doc, pause, repo_of


def changes_of(state: dict) -> list[FileChange]:
    return [FileChange.from_dict(c) for c in state.get("changes") or []]


def approval_pause(plan: writeplan.WritePlan, state: dict, scope: Scope, text: str, **extra) -> dict:
    step = plan.next_step
    repo = step.repo
    pending = hitl.Pending("approval", options=(hitl.APPROVE, hitl.SKIP), wants_json=scope.incoming.wants_json,
                           step=plan.done, digest=step.digest(), header=render.card_header(step, repo), text=text)
    return pause(pending, plan=plan.to_dict(), **extra)


async def propose_node(state: dict, scope: Scope) -> dict:
    items = changes_of(state)
    issue = issue_doc(state["issue"])
    tests = tr.Tests.from_dict(state.get("tests"))
    number = state.get("issue_number")
    rules = Constraints.from_dict(state.get("constraints"))
    composed = await compose.compose(scope.deps, issue.text(), issue.title, full_diff(items))
    body = compose.body(composed, tests.line(), number, [c.path for c in items])
    common = {"title": composed.title, "body": body}
    if state.get("pasted_only") or rules.diff_only:
        reason = "You asked for the diff only." if rules.diff_only else \
            "There is no code host connection, so I can't open a pull request."
        return diff_only_reply({**state, **common}, scope, reason)
    catalog = scope.catalog
    if catalog is None:  # a revision turn: this turn's write tools, found again
        catalog, _ = await discover(scope.deps, "write")
    can_open = catalog is not None and catalog.can_open_pr
    if not can_open:
        pending = hitl.Pending("diff_only", options=(hitl.SHOW_DIFF, hitl.SKIP), wants_json=scope.incoming.wants_json,
                               header="Show the diff only?",
                               text=render.proposal(state, None, composed.title, body) + "\n\n" + prompts.BLOCKED_REPLY)
        return pause(pending, **common)
    tools = {k: catalog.write_tool(k).name for k in ("branch", "commit", "pr")}
    repo = repo_of(state.get("repo"))
    branch = writeplan.branch_name(number, issue.title, full_diff(items))
    message = writeplan.commit_message(composed.title, number, rules.closes)
    plan = writeplan.build(tools, repo.full, state.get("ref") or "main", branch, items, state.get("blob_shas") or {},
                           message, composed.title, body)
    merged = {**state, **common, "plan": plan.to_dict()}
    env = render.envelope(merged, "awaiting_approval")
    text = render.proposal(merged, plan, composed.title, body)
    if scope.incoming.wants_json:
        text = render.as_json(env) + "\n\n" + text
    return approval_pause(plan, merged, scope, text, envelope=env, **common)


def diff_only_reply(state: dict, scope: Scope, reason: str) -> dict:
    env = render.envelope(state, "diff_only")
    text = render.as_json(env) if scope.incoming.wants_json else \
        render.diff_only(state, state.get("title") or "", state.get("body") or "", reason)
    return end(text, data=env, envelope=env, title=state.get("title"), body=state.get("body"), plan=None)


async def followup_node(state: dict, scope: Scope) -> dict:
    env = state.get("envelope") or {}
    items = changes_of(state)
    if not items:
        return end(env.get("summary") or prompts.NO_PRIOR)
    return end(f"{env.get('summary', '')}\n\n{render.diff_block(items)}", data=env)
