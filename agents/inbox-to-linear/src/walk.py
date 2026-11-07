"""The approval walk: one question per pause, its state on the A2A task between pauses.

    begin ─┬─ two trackers → [tracker?] ─┐
           └─────────────────────────────┴→ tracker read + duplicates ─┬─ no team → [team?] ─┐
                                                                       └─────────────────────┴→ propose
    propose → per issue: [create?] → create (guarded) ─┬─ created → reply draft → [send?] → send (guarded)
                                                       └─ gateway ask → [auth-required] → authorized: same call

Every approval ends the turn; the next turn resumes from the task metadata (any replica). One approval
allows exactly one write.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import answers
import issue
import prompts
import render
import reply as reply_step
import tracker
from create_guard import CreateApproval
from deps import TurnDeps
from discovery import Catalog, discover
from models import IssueDraft, Named, Request, TrackerContext
from nasiko_mcp import McpTool
from send_guard import Approval
from sender import WriteResult

log = logging.getLogger("inbox_to_linear.walk")

METADATA_KEY = "inbox_to_linear"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
TRACKER_HEADER = "Which issue tracker should I use?"
TEAM_HEADER = "Which team for these issues?"


@dataclass(frozen=True)
class Setup:
    """What the graph hands over: this batch of requests and the conversation's stored choices."""

    requests: tuple[Request, ...]
    team_hint: str | None = None
    team: Named | None = None
    family: str | None = None
    dry_run: bool = False
    json_mode: bool = False
    report: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Outcome:
    kind: str                       # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None    # task metadata: the walk, to resume from
    hitl: dict | None = None        # status-message metadata: the question, or the gateway's hitl_request_id
    state: dict | None = None       # conversation choices to remember (team, tracker)
    envelope: dict | None = None    # the output envelope, on completion


@dataclass(frozen=True)
class Walk:
    requests: tuple[Request, ...]
    steps: tuple[dict, ...] = ()
    index: int = 0
    stage: str = "approve"          # tracker | team | approve | gateway
    family: str = ""
    families: tuple[str, ...] = ()
    hint: str = ""
    tracker: dict = field(default_factory=dict)
    create_tool: str = ""
    reply_tool: str = ""
    created: tuple[dict, ...] = ()
    replies: tuple[dict, ...] = ()
    notes: tuple[str, ...] = ()
    approval: dict | None = None
    loop: dict | None = None
    preamble: str = ""
    dry_run: bool = False
    json_mode: bool = False
    report: dict = field(default_factory=dict)

    def to_metadata(self) -> dict:
        return {"requests": [r.to_dict() for r in self.requests], "steps": list(self.steps), "index": self.index,
                "stage": self.stage, "family": self.family, "families": list(self.families), "hint": self.hint,
                "tracker": self.tracker, "create_tool": self.create_tool, "reply_tool": self.reply_tool,
                "created": list(self.created), "replies": list(self.replies), "notes": list(self.notes),
                "approval": self.approval, "loop": self.loop, "preamble": self.preamble, "dry_run": self.dry_run,
                "json_mode": self.json_mode, "report": self.report}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Walk(requests=tuple(Request.from_dict(r) for r in data.get("requests") or ()),
                    steps=tuple(dict(s) for s in data.get("steps") or ()), index=int(data.get("index") or 0),
                    stage=str(data.get("stage") or "approve"), family=str(data.get("family") or ""),
                    families=tuple(str(f) for f in data.get("families") or ()), hint=str(data.get("hint") or ""),
                    tracker=dict(data.get("tracker") or {}), create_tool=str(data.get("create_tool") or ""),
                    reply_tool=str(data.get("reply_tool") or ""),
                    created=tuple(dict(c) for c in data.get("created") or ()),
                    replies=tuple(dict(r) for r in data.get("replies") or ()),
                    notes=tuple(str(n) for n in data.get("notes") or ()), approval=data.get("approval") or None,
                    loop=data.get("loop") or None, preamble=str(data.get("preamble") or ""),
                    dry_run=bool(data.get("dry_run")), json_mode=bool(data.get("json_mode")),
                    report=dict(data.get("report") or {}))

    @property
    def current(self) -> dict:
        return self.steps[self.index]


# --- start -------------------------------------------------------------------------------------------

async def begin(deps: TurnDeps, catalog: Catalog, setup: Setup) -> Outcome:
    walk = Walk(requests=setup.requests, hint=setup.team_hint or "", dry_run=setup.dry_run,
                json_mode=setup.json_mode, report=dict(setup.report))
    families = catalog.families()
    if len(families) > 1 and setup.family not in families:
        return _ask_choice(replace(walk, stage="tracker", families=families), TRACKER_HEADER, families)
    family = setup.family if setup.family in families else (families[0] if families else "")
    return await _prepare(deps, catalog, replace(walk, family=family), setup.team)


async def _prepare(deps: TurnDeps, catalog: Catalog, walk: Walk, stored_team: Named | None,
                   state: dict | None = None) -> Outcome:
    has_tracker = bool(catalog.families())
    tools = catalog.tracker_read_tools(walk.family or None) if has_tracker else ()
    read = await tracker.read(deps, tools, walk.family, walk.requests) if tools else tracker.TrackerRead(
        TrackerContext(family=walk.family, dup_check_ok=False))
    create_tool = catalog.create_tool(walk.family or None) if has_tracker else None
    reply_tool = catalog.reply_tool()
    coverage = list(walk.report.get("coverage") or ())
    if not has_tracker:
        coverage = [*coverage, prompts.NO_TRACKER_NOTE]
    walk = replace(walk, requests=tracker.mark_duplicates(walk.requests, read.issues),
                   tracker=read.context.to_dict(), create_tool=create_tool.name if create_tool else "",
                   reply_tool=reply_tool.name if reply_tool else "",
                   report={**walk.report, "dup_check_ok": read.context.dup_check_ok and bool(tools),
                           "coverage": coverage})
    teams = read.context.teams
    team, guessed = answers.pick_team(teams, walk.hint)
    if team is not None:  # a team named in the request is remembered for "continue" and later turns
        state = {**(state or {}), "team": {"id": team.id, "name": team.name}}
    if team is None and stored_team is not None:
        team = next((t for t in teams if t.id == stored_team.id or t.name == stored_team.name), None)
    if team is None and len(teams) == 1:
        team = teams[0]
    walk = replace(walk, report={**walk.report, "team_guessed": guessed})
    if team is None and len(teams) > 1 and walk.create_tool and not walk.dry_run and _untracked(walk):
        lead = (f'I couldn\'t find a team called "{walk.hint}". ' if walk.hint else "")
        return _ask_choice(replace(walk, stage="team"), TEAM_HEADER, [t.name for t in teams], lead, state)
    return await _propose(deps, walk, team, state)


def _untracked(walk: Walk) -> tuple[Request, ...]:
    return tuple(r for r in walk.requests if not r.tracked_key)


async def _propose(deps: TurnDeps, walk: Walk, team: Named | None, state: dict | None) -> Outcome:
    context = TrackerContext.from_dict(walk.tracker)
    proposed = tuple((r, issue.build(r, team, context.projects, context.labels)) for r in _untracked(walk))
    tracked = tuple(r for r in walk.requests if r.tracked_key)
    can_create = bool(walk.create_tool and team) and not walk.dry_run
    status = render.AWAITING if can_create else (render.DRY_RUN if walk.dry_run else render.COPY_ONLY)
    notes = list(walk.report.get("coverage") or ())
    steps = tuple({"kind": "create", "issue": d.to_dict()} for _, d in proposed) if can_create else ()
    if not walk.dry_run:
        steps = (*steps, *[await _tracked_reply_step(deps, r) for r in tracked if r.tracked_by == "tracker"])
    preamble = render.request_list(proposed, tracked, status, int(walk.report.get("remaining") or 0), notes)
    walk = replace(walk, steps=steps, preamble=preamble,
                   report={**walk.report, "proposed": [d.to_dict() for _, d in proposed],
                           "found": len(walk.requests), "tracked": len(tracked)})
    if not steps:
        extra = [] if can_create or walk.dry_run or not proposed else [_why_copy(walk, team)]
        copies = [render.issue_card_plain(d) for _, d in proposed] if extra else []
        return _complete(replace(walk, notes=(*extra, *copies)), state, awaiting=bool(proposed))
    return _ask(walk, state)


def _why_copy(walk: Walk, team: Named | None) -> str:
    if not walk.create_tool:
        return ("No issue-creating tool is available to this agent, so I can't create these; copy them into "
                "your tracker:")
    return "I couldn't read the teams in your tracker, so I can't create these safely; copy them instead:"


async def _tracked_reply_step(deps: TurnDeps, request: Request) -> dict:
    drafted = await reply_step.draft(deps, request, request.tracked_key or "", request.tracked_url or "")
    return {"kind": "reply", "reply": drafted.to_dict()}


# --- questions ---------------------------------------------------------------------------------------

def _ask_choice(walk: Walk, header: str, labels: Sequence[str], lead: str = "",
                state: dict | None = None) -> Outcome:
    shown = list(labels)[:answers.MAX_CHOICES]
    text = "\n".join([f"{lead}{header}", *(f"- {label}" for label in shown)])
    return Outcome("input_required", text, _metadata(walk), answers.choice_question(header, shown), state)


def _ask(walk: Walk, state: dict | None = None, lead: str = "") -> Outcome:
    step = walk.current
    if step["kind"] == "create":
        draft = IssueDraft.from_dict(step["issue"])
        question = answers.create_question(draft)
        body = render.issue_card(draft, walk.create_tool)
        hint = 'Answer Approve or Skip, or edit it: "title: ..." or "labels: a, b".'
    else:
        drafted = reply_step.ReplyDraft.from_dict(step["reply"])
        question = answers.reply_question(drafted.who)
        body = render.reply_card(drafted.to_name, drafted.to, drafted.subject, drafted.body, walk.reply_tool or "none")
        hint = "Answer Send or Skip, or type the text to send instead."
    first = walk.index == 0 and not walk.notes
    lead_text = lead or (walk.preamble if first else (walk.notes[-1] if walk.notes else ""))
    progress = f"Step {walk.index + 1} of {len(walk.steps)}." if len(walk.steps) > 1 else ""
    text = "\n\n".join(p for p in (lead_text, progress, question["header"], body, hint) if p)
    return Outcome("input_required", text, _metadata(walk), question, state)


# --- resume ------------------------------------------------------------------------------------------

async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome:
    stored = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(stored, Mapping):
        return Outcome("complete", NOT_WAITING)
    walk = Walk.from_metadata(stored)
    if walk.stage == "tracker":
        return await _resume_tracker(deps, walk, answer)
    if walk.stage == "team":
        return await _resume_team(deps, walk, answer)
    if walk.index >= len(walk.steps):
        return Outcome("complete", NOT_WAITING)
    if walk.stage == "gateway":
        return await _resume_gateway(deps, walk, answer)
    if walk.current["kind"] == "create":
        return await _answer_create(deps, walk, answer)
    return await _answer_reply(deps, walk, answer)


async def _resume_tracker(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    family = answers.pick(answer, walk.families)
    if family is None:
        return _ask_choice(walk, TRACKER_HEADER, walk.families, "Please pick one of these. ")
    catalog, failure = await discover(deps)
    if failure is not None:
        return Outcome("complete", failure)
    return await _prepare(deps, catalog, replace(walk, family=family, stage="approve"), None, {"family": family})


async def _resume_team(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    teams = TrackerContext.from_dict(walk.tracker).teams
    name = answers.pick(answer, [t.name for t in teams])
    team = next((t for t in teams if t.name == name), None)
    if team is None:
        return _ask_choice(walk, TEAM_HEADER, [t.name for t in teams], "Please pick one of these. ")
    state = {"team": {"id": team.id, "name": team.name}}
    return await _propose(deps, replace(walk, stage="approve"), team, state)


async def _answer_create(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    draft = IssueDraft.from_dict(walk.current["issue"])
    decision, text = answers.decision(answer)
    if decision == "skip":
        entry = _created_entry(walk, draft, "skipped")
        return _advance(replace(walk, created=(*walk.created, entry)), f'Skipped: "{draft.title}", nothing created.')
    if decision == "other":
        edited, why = answers.edit(draft, text, TrackerContext.from_dict(walk.tracker).labels)
        if edited is None:
            return _ask(walk, lead=why)
        steps = tuple({**s, "issue": edited.to_dict()} if i == walk.index else s for i, s in enumerate(walk.steps))
        return _ask(replace(walk, steps=steps), lead="Updated. Please confirm the edited issue.")
    tool, failure = await _tool(deps, walk, walk.create_tool, "create")
    if tool is None:
        return await _after_create(deps, walk, draft, WriteResult("not_done", failure), None)
    approval = CreateApproval(tool=tool.name, issue=draft)
    log.info("issue for %s approved", draft.request_id)
    return await _after_create(deps, walk, draft, await tracker.create(deps, tool, approval), approval)


async def _answer_reply(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    drafted = reply_step.ReplyDraft.from_dict(walk.current["reply"])
    decision, text = answers.decision(answer)
    if decision == "skip":
        entry = _reply_entry(drafted, drafted.body, "skipped")
        return _advance(replace(walk, replies=(*walk.replies, entry)), f"Reply to {drafted.who}: skipped, not sent.")
    tool, failure = await _tool(deps, walk, walk.reply_tool, "reply")
    approval = reply_step.approval_for(drafted, tool.name if tool else walk.reply_tool, text or None)
    if tool is None:
        return await _after_reply(walk, drafted, approval, WriteResult("not_done", failure))
    return await _after_reply(walk, drafted, approval, await reply_step.send(deps, tool, drafted, approval))


async def _resume_gateway(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    step = walk.current
    if not walk.approval or not walk.loop:  # fail closed: never guess what was approved
        return _advance(walk, "Not done: the pause state was incomplete.")
    if step["kind"] == "create":
        approval = CreateApproval.from_dict(walk.approval)
        tool, failure = await _tool(deps, walk, walk.create_tool, "create")
        if tool is None or approval is None:
            return await _after_create(deps, walk, IssueDraft.from_dict(step["issue"]),
                                       WriteResult("not_done", failure or "approval lost"), None)
        result = await tracker.resume_create(deps, tool, approval, walk.loop, answer)
        return await _after_create(deps, walk, approval.issue, result, approval)
    drafted = reply_step.ReplyDraft.from_dict(step["reply"])
    approval = Approval.from_dict(walk.approval)
    tool, failure = await _tool(deps, walk, walk.reply_tool, "reply")
    if tool is None or approval is None:
        return await _after_reply(walk, drafted, approval or reply_step.approval_for(drafted, walk.reply_tool),
                                  WriteResult("not_done", failure or "approval lost"))
    return await _after_reply(walk, drafted, approval, await reply_step.resume_send(deps, tool, approval, walk.loop,
                                                                                    answer))


async def _tool(deps: TurnDeps, walk: Walk, name: str, kind: str) -> tuple[McpTool | None, str]:
    if not name:
        return None, f"no {kind} tool is available to this agent"
    catalog, failure = await discover(deps)
    if failure is not None:
        return None, failure
    found = catalog.create_tool(walk.family or None) if kind == "create" else catalog.reply_tool()
    if found is None or found.name != name:
        return None, f"the {kind} tool is no longer granted to this agent"
    return found, ""


# --- after a write -----------------------------------------------------------------------------------

async def _after_create(deps: TurnDeps, walk: Walk, draft: IssueDraft, result: WriteResult,
                        approval: CreateApproval | None) -> Outcome:
    if result.status == "paused" and approval is not None:
        paused = replace(walk, stage="gateway", approval=approval.to_dict(), loop=result.loop)
        text = "The platform needs your approval before this issue is created:\n\n" + render.issue_card(
            draft, approval.tool)
        return Outcome("auth_required", text, _metadata(paused), {"hitl_request_id": result.hitl_request_id})
    if result.status != "done":
        state = "skipped" if result.status == "denied" else "failed"
        reason = {"denied": "The create was denied, so nothing was created.",
                  "blocked": "Creating issues is blocked for this agent."}.get(
            result.status, f"Not created: {result.reason or 'unknown reason'}.")
        note = reason if result.status == "denied" else render.copyable_issue(draft, reason)
        return _advance(replace(walk, created=(*walk.created, _created_entry(walk, draft, state))), note)
    key, url = tracker.created_ref(result.output)
    entry = {**_created_entry(walk, draft, "created"), "key": key, "url": url}
    shown = f"{key or 'Issue'} ({url})" if url else (key or "Issue")
    note = f'Created {shown}: "{draft.title}".'
    walk = replace(walk, created=(*walk.created, entry))
    request = next((r for r in walk.requests if r.id == draft.request_id), None)
    if request is None or not request.sender or not (key or url):
        return _advance(walk, note)
    drafted = await reply_step.draft(deps, request, key, url)
    steps = (*walk.steps[:walk.index + 1], {"kind": "reply", "reply": drafted.to_dict()}, *walk.steps[walk.index + 1:])
    return _advance(replace(walk, steps=steps), note)


async def _after_reply(walk: Walk, drafted: reply_step.ReplyDraft, approval: Approval, result: WriteResult) -> Outcome:
    if result.status == "paused":
        paused = replace(walk, stage="gateway", approval=approval.to_dict(), loop=result.loop)
        text = "The platform needs your approval before this is sent:\n\n" + render.reply_card(
            drafted.to_name, drafted.to, approval.subject, approval.draft, approval.tool)
        return Outcome("auth_required", text, _metadata(paused), {"hitl_request_id": result.hitl_request_id})
    if result.status == "done":
        entry = _reply_entry(drafted, approval.draft, "sent")
        return _advance(replace(walk, replies=(*walk.replies, entry)), f"Reply sent to {drafted.who}.")
    if result.status == "denied":
        entry = _reply_entry(drafted, approval.draft, "skipped")
        return _advance(replace(walk, replies=(*walk.replies, entry)),
                        f"The reply to {drafted.who} was denied, so nothing was sent and I won't retry it.")
    reason = ("Sending is blocked for this agent." if result.status == "blocked"
              else f"Not sent: {result.reason or 'unknown reason'}.")
    entry = _reply_entry(drafted, approval.draft, "copyable")
    return _advance(replace(walk, replies=(*walk.replies, entry)),
                    render.copyable_reply(drafted.to, approval.subject, approval.draft, reason))


def _created_entry(walk: Walk, draft: IssueDraft, state: str) -> dict:
    request = next((r for r in walk.requests if r.id == draft.request_id), None)
    source = (request.link or request.message_id or request.subject) if request else ""
    return {"key": "", "title": draft.title, "url": "", "source_email": source, "state": state}


def _reply_entry(drafted: reply_step.ReplyDraft, body: str, state: str) -> dict:
    return {"to": drafted.to, "thread": drafted.thread_id, "body": body, "state": state}


def _advance(walk: Walk, note: str) -> Outcome:
    moved = replace(walk, index=walk.index + 1, notes=(*walk.notes, note), stage="approve", approval=None, loop=None)
    if moved.index < len(moved.steps):
        return _ask(moved)
    return _complete(moved, None, awaiting=False)


def _complete(walk: Walk, state: dict | None, *, awaiting: bool) -> Outcome:
    proposed = [IssueDraft.from_dict(d) for d in walk.report.get("proposed") or ()]
    data = render.envelope(walk.report, proposed, walk.created, walk.replies, awaiting=awaiting)
    if walk.json_mode:
        text = render.as_json(data)
    elif walk.steps:
        text = "\n\n".join([*walk.notes, data["summary"]])
    else:
        text = "\n\n".join(p for p in (walk.preamble, *walk.notes) if p)
    return Outcome("complete", text, _metadata(walk), None, state, data)


def _metadata(walk: Walk) -> dict:
    return {METADATA_KEY: walk.to_metadata()}
