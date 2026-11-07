"""The approval walk: one question per pause, its state on the A2A task between pauses.

    begin ─┬─ two trackers → [tracker?] ─┐
           └─────────────────────────────┴→ tracker read + duplicates ─┬─ no team → [team?] ─┐
                                                                       └─────────────────────┴→ propose
    propose → per issue: [create?] → create (guarded) ─┬─ created → [post link-back?] → post (guarded)
                                                       └─ gateway ask → [auth-required] → authorized: same call
    already tracked in the tracker (but not in the thread) → [post link-back?] only

Every approval ends the turn; the next turn resumes from the task metadata (any replica). One approval
allows exactly one write. The link-back card appears only after its issue exists.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import answers
import issue
import linkback
import prompts
import render
import tracker
from create_guard import CreateApproval
from deps import TurnDeps
from discovery import Catalog, discover
from models import Finding, IssueDraft, Named, TrackerContext
from nasiko_mcp import McpTool
from send_guard import Approval
from sender import WriteResult

log = logging.getLogger("thread_to_issue.walk")

METADATA_KEY = "thread_to_issue"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
TRACKER_HEADER = "Which issue tracker should I use?"
TEAM_HEADER = "Which team?"


@dataclass(frozen=True)
class Setup:
    """What the graph hands over: this batch of findings and the conversation's stored choices."""

    findings: tuple[Finding, ...]
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
    findings: tuple[Finding, ...]
    steps: tuple[dict, ...] = ()
    index: int = 0
    stage: str = "approve"          # tracker | team | approve | gateway
    family: str = ""
    families: tuple[str, ...] = ()
    hint: str = ""
    tracker: dict = field(default_factory=dict)
    create_tool: str = ""
    post_tool: str = ""
    issues: tuple[dict, ...] = ()   # the envelope's proposed_issues, updated as the walk goes
    notes: tuple[str, ...] = ()
    approval: dict | None = None
    loop: dict | None = None
    preamble: str = ""
    dry_run: bool = False
    json_mode: bool = False
    report: dict = field(default_factory=dict)

    def to_metadata(self) -> dict:
        return {"findings": [f.to_dict() for f in self.findings], "steps": list(self.steps), "index": self.index,
                "stage": self.stage, "family": self.family, "families": list(self.families), "hint": self.hint,
                "tracker": self.tracker, "create_tool": self.create_tool, "post_tool": self.post_tool,
                "issues": list(self.issues), "notes": list(self.notes), "approval": self.approval,
                "loop": self.loop, "preamble": self.preamble, "dry_run": self.dry_run, "json_mode": self.json_mode,
                "report": self.report}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Walk(findings=tuple(Finding.from_dict(f) for f in data.get("findings") or ()),
                    steps=tuple(dict(s) for s in data.get("steps") or ()), index=int(data.get("index") or 0),
                    stage=str(data.get("stage") or "approve"), family=str(data.get("family") or ""),
                    families=tuple(str(f) for f in data.get("families") or ()), hint=str(data.get("hint") or ""),
                    tracker=dict(data.get("tracker") or {}), create_tool=str(data.get("create_tool") or ""),
                    post_tool=str(data.get("post_tool") or ""),
                    issues=tuple(dict(i) for i in data.get("issues") or ()),
                    notes=tuple(str(n) for n in data.get("notes") or ()), approval=data.get("approval") or None,
                    loop=data.get("loop") or None, preamble=str(data.get("preamble") or ""),
                    dry_run=bool(data.get("dry_run")), json_mode=bool(data.get("json_mode")),
                    report=dict(data.get("report") or {}))

    @property
    def current(self) -> dict:
        return self.steps[self.index]

    def finding(self, finding_id: str) -> Finding | None:
        return next((f for f in self.findings if f.id == finding_id), None)


# --- start -------------------------------------------------------------------------------------------

async def begin(deps: TurnDeps, catalog: Catalog, setup: Setup) -> Outcome:
    walk = Walk(findings=setup.findings, hint=setup.team_hint or "", dry_run=setup.dry_run,
                json_mode=setup.json_mode, report=dict(setup.report))
    families = catalog.families()
    if len(families) > 1 and setup.family not in families and not setup.dry_run:
        return _ask_choice(replace(walk, stage="tracker", families=families), TRACKER_HEADER, families)
    family = setup.family if setup.family in families else (families[0] if families else "")
    return await _prepare(deps, catalog, replace(walk, family=family), setup.team)


async def _prepare(deps: TurnDeps, catalog: Catalog, walk: Walk, stored_team: Named | None,
                   state: dict | None = None) -> Outcome:
    has_tracker = bool(catalog.families())
    tools = catalog.tracker_read_tools(walk.family or None) if has_tracker else ()
    read = await tracker.read(deps, tools, walk.family, walk.findings) if tools else tracker.TrackerRead(
        TrackerContext(family=walk.family, dup_check_ok=False))
    create_tool = catalog.create_tool(walk.family or None) if has_tracker else None
    post_tool = catalog.post_tool()
    coverage = list(walk.report.get("coverage") or ())
    if not has_tracker:
        coverage = [*coverage, prompts.NO_TRACKER_NOTE]
    findings = _not_linked(tracker.mark_duplicates(walk.findings, read.issues), tracker.hosts(read.issues))
    walk = replace(walk, findings=findings, tracker=read.context.to_dict(),
                   create_tool=create_tool.name if create_tool else "", post_tool=post_tool.name if post_tool else "",
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
    return _propose(walk, team, state)


def _not_linked(findings: Sequence[Finding], hosts: Sequence[str]) -> tuple[Finding, ...]:
    """Drop a finding whose thread already links an issue on the tracker's own host (seen this turn)."""
    if not hosts:
        return tuple(findings)
    return tuple(f for f in findings if not any(f"//{h}/" in f.text.casefold() for h in hosts))


def _untracked(walk: Walk) -> tuple[Finding, ...]:
    return tuple(f for f in walk.findings if not f.tracked_key)


def _propose(walk: Walk, team: Named | None, state: dict | None) -> Outcome:
    context = TrackerContext.from_dict(walk.tracker)
    proposed = tuple((f, issue.build(f, team, context.projects, context.labels)) for f in _untracked(walk))
    tracked = tuple(f for f in walk.findings if f.tracked_key)
    can_create = bool(walk.create_tool and team) and not walk.dry_run
    status = render.AWAITING if can_create else (render.DRY_RUN if walk.dry_run else render.COPY_ONLY)
    notes = list(walk.report.get("coverage") or ())
    steps = tuple({"kind": "create", "issue": d.to_dict()} for _, d in proposed) if can_create else ()
    links = () if walk.dry_run else tuple(linkback.draft(f, f.tracked_key or "", f.tracked_url or "")
                                          for f in tracked if f.can_link_back)
    if walk.post_tool:
        steps = (*steps, *({"kind": "link_back", "link": link.to_dict()} for link in links))
    else:
        notes = [*notes, *(render.copyable_linkback(link.where, link.text, "No chat posting tool is available to "
                                                    "this agent.") for link in links)]
    preamble = render.proposal_list(proposed, tracked, status, walk.report, int(walk.report.get("remaining") or 0),
                                    notes)
    issues = (*(render.proposed_issue(f, d, "proposed") for f, d in proposed),
              *(render.proposed_issue(f, None, "skipped") for f in tracked))
    walk = replace(walk, steps=steps, preamble=preamble, issues=issues)
    if not steps:
        extra = [] if can_create or walk.dry_run or not proposed else [_why_copy(walk)]
        copies = [render.issue_card_plain(d) for _, d in proposed] if extra else []
        return _complete(replace(walk, notes=(*extra, *copies)), state, awaiting=bool(proposed))
    return _ask(walk, state)


def _why_copy(walk: Walk) -> str:
    if not walk.create_tool:
        return ("No issue-creating tool is available to this agent, so I can't create these; copy them into "
                "your tracker:")
    return "I couldn't read the teams in your tracker, so I can't create these safely; copy them instead:"


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
        body = render.issue_card(draft, walk.finding(draft.finding_id), walk.create_tool)
        hint = 'Answer Approve or Skip, or edit it: "title: ..." or "labels: a, b".'
    else:
        link = linkback.LinkBack.from_dict(step["link"])
        text = str(step.get("text") or link.text)
        question = answers.linkback_question(text)
        body = render.linkback_card(link.where, link.thread_ts, text, walk.post_tool or "none")
        hint = "Answer Post or Skip, or type the text to post instead."
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
        return _resume_team(walk, answer)
    if walk.index >= len(walk.steps):
        return Outcome("complete", NOT_WAITING)
    if walk.stage == "gateway":
        return await _resume_gateway(deps, walk, answer)
    if walk.current["kind"] == "create":
        return await _answer_create(deps, walk, answer)
    return await _answer_link(deps, walk, answer)


async def _resume_tracker(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    family = answers.pick(answer, walk.families)
    if family is None:
        return _ask_choice(walk, TRACKER_HEADER, walk.families, "Please pick one of these. ")
    catalog, failure = await discover(deps)
    if failure is not None:
        return Outcome("complete", failure)
    return await _prepare(deps, catalog, replace(walk, family=family, stage="approve"), None, {"family": family})


def _resume_team(walk: Walk, answer: str) -> Outcome:
    teams = TrackerContext.from_dict(walk.tracker).teams
    name = answers.pick(answer, [t.name for t in teams])
    team = next((t for t in teams if t.name == name), None)
    if team is None:
        return _ask_choice(walk, TEAM_HEADER, [t.name for t in teams], "Please pick one of these. ")
    return _propose(replace(walk, stage="approve"), team, {"team": {"id": team.id, "name": team.name}})


async def _answer_create(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    draft = IssueDraft.from_dict(walk.current["issue"])
    decision, text = answers.decision(answer)
    if decision == "skip":
        return _advance(_with_issue(walk, draft.finding_id, state="skipped"), f'Skipped: "{draft.title}", nothing created.')
    if decision == "other":
        edited, why = answers.edit(draft, text, TrackerContext.from_dict(walk.tracker).labels)
        if edited is None:
            return _ask(walk, lead=why)
        steps = tuple({**s, "issue": edited.to_dict()} if i == walk.index else s for i, s in enumerate(walk.steps))
        walk = _with_issue(replace(walk, steps=steps), draft.finding_id, title=edited.title,
                           labels=[x.name for x in edited.labels])
        return _ask(walk, lead="Updated. Please confirm the edited issue.")
    tool, failure = await _tool(deps, walk, walk.create_tool, "create")
    if tool is None:
        return _after_create(walk, draft, WriteResult("not_done", failure), None)
    approval = CreateApproval(tool=tool.name, issue=draft)
    log.info("issue for %s approved", draft.finding_id)
    return _after_create(walk, draft, await tracker.create(deps, tool, approval), approval)


async def _answer_link(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    link = linkback.LinkBack.from_dict(walk.current["link"])
    decision, text = answers.decision(answer)
    if decision == "skip":
        return _advance(walk, f"Link-back in {link.where}: skipped, nothing posted.")
    if decision == "other":  # the user's own text: confirm it before posting (the guard binds this text)
        steps = tuple({**s, "text": text} if i == walk.index else s for i, s in enumerate(walk.steps))
        return _ask(replace(walk, steps=steps), lead="Updated. Please confirm the edited reply.")
    body = str(walk.current.get("text") or link.text)
    tool, failure = await _tool(deps, walk, walk.post_tool, "post")
    approval = linkback.approval_for(link, tool.name if tool else walk.post_tool, body)
    if tool is None:
        return _after_link(walk, link, approval, WriteResult("not_done", failure))
    return _after_link(walk, link, approval, await linkback.post(deps, tool, link, approval))


async def _resume_gateway(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    step = walk.current
    if not walk.approval or not walk.loop:  # fail closed: never guess what was approved
        return _advance(walk, "Not done: the pause state was incomplete.")
    if step["kind"] == "create":
        approval = CreateApproval.from_dict(walk.approval)
        tool, failure = await _tool(deps, walk, walk.create_tool, "create")
        if tool is None or approval is None:
            return _after_create(walk, IssueDraft.from_dict(step["issue"]),
                                 WriteResult("not_done", failure or "approval lost"), None)
        result = await tracker.resume_create(deps, tool, approval, walk.loop, answer)
        return _after_create(walk, approval.issue, result, approval)
    link = linkback.LinkBack.from_dict(step["link"])
    approval = Approval.from_dict(walk.approval)
    tool, failure = await _tool(deps, walk, walk.post_tool, "post")
    if tool is None or approval is None:
        return _after_link(walk, link, approval or linkback.approval_for(link, walk.post_tool),
                           WriteResult("not_done", failure or "approval lost"))
    return _after_link(walk, link, approval, await linkback.resume_post(deps, tool, link, approval, walk.loop, answer))


async def _tool(deps: TurnDeps, walk: Walk, name: str, kind: str) -> tuple[McpTool | None, str]:
    if not name:
        return None, f"no {kind} tool is available to this agent"
    catalog, failure = await discover(deps)
    if failure is not None:
        return None, failure
    found = catalog.create_tool(walk.family or None) if kind == "create" else catalog.post_tool()
    if found is None or found.name != name:
        return None, f"the {kind} tool is no longer granted to this agent"
    return found, ""


# --- after a write -----------------------------------------------------------------------------------

def _after_create(walk: Walk, draft: IssueDraft, result: WriteResult, approval: CreateApproval | None) -> Outcome:
    if result.status == "paused" and approval is not None:
        paused = replace(walk, stage="gateway", approval=approval.to_dict(), loop=result.loop)
        text = "The platform needs your approval before this issue is created:\n\n" + render.issue_card(
            draft, walk.finding(draft.finding_id), approval.tool)
        return Outcome("auth_required", text, _metadata(paused), {"hitl_request_id": result.hitl_request_id})
    if result.status != "done":
        state = "skipped" if result.status == "denied" else "failed"
        reason = {"denied": "The create was denied, so nothing was created.",
                  "blocked": "Creating issues is blocked for this agent."}.get(
            result.status, f"Not created: {result.reason or 'unknown reason'}.")
        note = reason if result.status == "denied" else render.copyable_issue(draft, reason)
        return _advance(_with_issue(walk, draft.finding_id, state=state), note)
    key, url = tracker.created_ref(result.output)
    shown = f"{key or 'Issue'} ({url})" if url else (key or "Issue")
    walk = _with_issue(walk, draft.finding_id, state="created", issue_key=key or None, issue_url=url or None)
    note = f'Created {shown}: "{draft.title}".'
    finding = walk.finding(draft.finding_id)
    if finding is None or not (key or url):
        return _advance(walk, note)
    if not finding.can_link_back:
        return _advance(walk, note)
    link = linkback.draft(finding, key, url)
    if not walk.post_tool:
        return _advance(walk, "\n\n".join([note, render.copyable_linkback(
            link.where, link.text, "No chat posting tool is available to this agent.")]))
    steps = (*walk.steps[:walk.index + 1], {"kind": "link_back", "link": link.to_dict()}, *walk.steps[walk.index + 1:])
    return _advance(replace(walk, steps=steps), note)


def _after_link(walk: Walk, link: linkback.LinkBack, approval: Approval, result: WriteResult) -> Outcome:
    if result.status == "paused":
        paused = replace(walk, stage="gateway", approval=approval.to_dict(), loop=result.loop)
        text = "The platform needs your approval before this is posted:\n\n" + render.linkback_card(
            link.where, link.thread_ts, approval.draft, approval.tool)
        return Outcome("auth_required", text, _metadata(paused), {"hitl_request_id": result.hitl_request_id})
    if result.status == "done":
        return _advance(_with_issue(walk, link.finding_id, linked_back=True), f"Posted in the thread in {link.where}.")
    if result.status == "denied":
        return _advance(walk, f"The link-back in {link.where} was denied, so nothing was posted and I won't retry it.")
    reason = ("Posting is blocked for this agent." if result.status == "blocked"
              else f"Not posted: {result.reason or 'unknown reason'}.")
    return _advance(walk, render.copyable_linkback(link.where, approval.draft, reason))


def _with_issue(walk: Walk, finding_id: str, **changes: Any) -> Walk:
    return replace(walk, issues=tuple({**i, **changes} if i.get("id") == finding_id else i for i in walk.issues))


def _advance(walk: Walk, note: str) -> Outcome:
    moved = replace(walk, index=walk.index + 1, notes=(*walk.notes, note), stage="approve", approval=None, loop=None)
    if moved.index < len(moved.steps):
        return _ask(moved)
    return _complete(moved, None, awaiting=False)


def _complete(walk: Walk, state: dict | None, *, awaiting: bool) -> Outcome:
    data = render.envelope(walk.report, walk.issues, awaiting=awaiting)
    if walk.json_mode:
        text = render.as_json(data)
    elif walk.steps:
        text = "\n\n".join([*walk.notes, data["summary"]])
    else:
        text = "\n\n".join(p for p in (walk.preamble, *walk.notes) if p)
    return Outcome("complete", text, _metadata(walk), None, state, data)


def _metadata(walk: Walk) -> dict:
    return {METADATA_KEY: walk.to_metadata()}
