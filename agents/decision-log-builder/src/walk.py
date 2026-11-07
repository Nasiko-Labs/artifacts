"""The document walk: one question per pause, its state on the A2A task between pauses.

    begin ─┬─ text only / no doc tool ───────────────────────────────→ list + copyable block
           └─ find the log ─┬─ one doc → read → mark duplicates ─┐
                            ├─ several / none → [which log?] ────┤ (create new / show text only)
                            └────────────────────────────────────┴→ [which decisions?] (multi-select)
           → block (code) → [append / create?] → re-read (changed? show the card again) → write (guarded)
                                              └─ gateway ask → [auth-required] → authorized: same call

Every pause ends the turn; the next turn resumes from the task metadata (any replica). One approval allows
exactly one write. The conversation's list, doc and written ids are also handed back as `state` for the
checkpointer, so "log 1,3" works in a later turn.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import answers
import block_guard
import dedupe
import docs
import entry
import parse
import prompts
import render
from deps import TurnDeps
from discovery import Catalog, discover
from models import Decision, Doc
from nasiko_mcp import McpTool
from sender import WriteResult

log = logging.getLogger("decision_log.walk")

METADATA_KEY = "decision_log"
NOT_WAITING = ("Nothing is waiting for your approval, so nothing was written. Say \"log 1,3\" to pick entries "
               "from the list.")
MAX_LOG_CHARS = 20_000


@dataclass(frozen=True)
class Outcome:
    kind: str                       # "complete" | "input_required" | "auth_required" | "new_turn"
    text: str = ""
    metadata: dict | None = None    # task metadata: the walk, to resume from
    hitl: dict | None = None        # status-message metadata: the question, or the gateway's hitl_request_id
    state: dict | None = None       # the conversation's list, doc and written ids, for the checkpointer
    envelope: dict | None = None    # the output envelope, on completion


@dataclass(frozen=True)
class Walk:
    decisions: tuple[Decision, ...]
    report: dict = field(default_factory=dict)
    doc: Doc | None = None
    log_text: str = ""
    written: tuple[str, ...] = ()
    write_blocked: bool = False
    title_hint: str = ""
    json_mode: bool = False
    stage: str = ""                 # doc | select | approve | gateway
    docs: tuple[Doc, ...] = ()      # the doc choices offered
    ids: tuple[str, ...] = ()       # the decisions picked for the block
    note: str = ""
    approval: dict | None = None
    loop: dict | None = None
    lead: str = ""

    def to_metadata(self) -> dict:
        return {"decisions": [d.to_dict() for d in self.decisions], "report": self.report,
                "doc": self.doc.to_dict() if self.doc else None, "log_text": self.log_text,
                "written": list(self.written), "write_blocked": self.write_blocked, "title_hint": self.title_hint,
                "json_mode": self.json_mode, "stage": self.stage, "docs": [d.to_dict() for d in self.docs],
                "ids": list(self.ids), "note": self.note, "approval": self.approval, "loop": self.loop,
                "lead": self.lead}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Walk(decisions=tuple(Decision.from_dict(d) for d in data.get("decisions") or ()),
                    report=dict(data.get("report") or {}), doc=Doc.from_dict(data.get("doc")),
                    log_text=str(data.get("log_text") or ""), written=tuple(str(i) for i in data.get("written") or ()),
                    write_blocked=bool(data.get("write_blocked")), title_hint=str(data.get("title_hint") or ""),
                    json_mode=bool(data.get("json_mode")), stage=str(data.get("stage") or ""),
                    docs=tuple(d for d in (Doc.from_dict(x) for x in data.get("docs") or ()) if d),
                    ids=tuple(str(i) for i in data.get("ids") or ()), note=str(data.get("note") or ""),
                    approval=data.get("approval") or None, loop=data.get("loop") or None,
                    lead=str(data.get("lead") or ""))

    def state(self) -> dict:
        return {"decisions": [d.to_dict() for d in self.decisions], "report": self.report,
                "doc": self.doc.to_dict() if self.doc else None, "log_text": self.log_text,
                "written": list(self.written), "write_blocked": self.write_blocked}

    @property
    def fresh(self) -> tuple[Decision, ...]:
        """The default write set: not already in the log, not written in this conversation."""
        return tuple(d for d in self.decisions if not d.duplicate_of and d.id not in self.written)


@dataclass(frozen=True)
class Setup:
    """What the turn hands over: the list, the conversation's doc, and what this message asked for."""

    walk: Walk
    show_only: bool = False
    change_doc: bool = False
    ids: tuple[str, ...] = ()
    every: bool = False


# --- start -------------------------------------------------------------------------------------------

async def begin(deps: TurnDeps, setup: Setup) -> Outcome:
    walk = setup.walk
    if setup.show_only:
        return _show_text(walk, _chosen(walk, setup.ids, setup.every) or walk.fresh, "")
    catalog, _ = await discover(deps, "doc")
    if catalog is None or not (catalog.doc_read_tools() or catalog.append_tool() or catalog.create_tool()):
        return _show_text(_noted(walk, prompts.NO_DOC_TOOL_NOTE), walk.fresh, "")
    walk = replace(walk, ids=_chosen(walk, setup.ids, setup.every), lead=_every_note(walk, setup))
    if walk.doc is not None and walk.doc.state in ("found", "create") and not setup.change_doc:
        return await _after_doc(deps, walk)
    hint = walk.title_hint or (walk.doc.title if walk.doc is not None and not setup.change_doc else "")
    walk = replace(walk, doc=None, log_text="", title_hint=hint, decisions=dedupe.mark(walk.decisions, ""))
    return await _find(deps, catalog, walk)


def _chosen(walk: Walk, ids: Sequence[str], every: bool) -> tuple[str, ...]:
    if every:  # "log all new": implied decisions are never selected by a shortcut
        return tuple(d.id for d in walk.fresh if not d.implied)
    known = {d.id for d in walk.decisions}
    return tuple(i for i in ids if i in known and i not in walk.written)


def _every_note(walk: Walk, setup: Setup) -> str:
    if setup.every and any(d.implied for d in walk.fresh):
        return "Implied decisions are left out of \"all new\"; name them by number to include them."
    return ""


async def _find(deps: TurnDeps, catalog: Catalog, walk: Walk) -> Outcome:
    found = await docs.find(deps, catalog, walk.title_hint or None)
    if found.note:
        walk = _noted(walk, found.note)
    if len(found.docs) == 1:
        return await _use_doc(deps, catalog, walk, found.docs[0])
    return _ask_doc(replace(walk, docs=found.docs), catalog)


async def _use_doc(deps: TurnDeps, catalog: Catalog, walk: Walk, doc: Doc) -> Outcome:
    read = await docs.read(deps, catalog, doc)
    if not read.ok:
        walk = _noted(walk, read.note)
    text = read.text[:MAX_LOG_CHARS]
    walk = replace(walk, doc=replace(doc, state="found", version=read.version), log_text=text,
                   decisions=dedupe.mark(walk.decisions, text))
    return await _after_doc(deps, walk)


async def _after_doc(deps: TurnDeps, walk: Walk) -> Outcome:
    if walk.json_mode:
        return _complete(walk, _list_text(walk), awaiting=bool(walk.fresh))
    if walk.ids:
        return await _ask_approval(deps, walk)
    if not walk.fresh:
        return _complete(walk, "\n\n".join([_list_text(walk), prompts.NOTHING_LEFT]))
    return _ask_selection(walk)


# --- questions ---------------------------------------------------------------------------------------

def _list_text(walk: Walk, can_write: bool = True) -> str:
    return render.decision_list(walk.decisions, int(walk.report.get("window_days") or parse.DEFAULT_WINDOW_DAYS),
                                walk.doc, list(walk.report.get("coverage") or ()), walk.written,
                                int(walk.report.get("not_decided") or 0), bool(walk.report.get("pasted")),
                                can_write)


def _ask_doc(walk: Walk, catalog: Catalog) -> Outcome:
    can_create = catalog.create_tool() is not None
    labels = answers.doc_labels(walk.docs, can_create)
    if not walk.docs and not can_create:
        reason = "I found no decision log document and no tool to create one."
        return _show_text(walk, walk.fresh, reason)
    lead = answers.DOC_HEADER if walk.docs else "I found no decision log document. " + answers.DOC_HEADER
    text = "\n\n".join(p for p in (_list_text(walk), walk.lead, lead, "\n".join(f"- {x}" for x in labels)) if p)
    return Outcome("input_required", text, _metadata(replace(walk, stage="doc")), answers.doc_question(labels),
                   walk.state())


def _ask_selection(walk: Walk) -> Outcome:
    offered = walk.fresh[:answers.MAX_OPTIONS]
    question = answers.selection_question([render.option_label(d) for d in offered])
    more = len(walk.fresh) - len(offered)
    tail = f" {more} more can be logged by number (\"log 12\")." if more > 0 else ""
    text = "\n\n".join(p for p in (_list_text(walk), walk.lead, answers.SELECT_HEADER + " " + answers.SELECT_HINT
                                   + tail) if p)
    return Outcome("input_required", text, _metadata(replace(walk, stage="select", lead="")), question, walk.state())


async def _ask_approval(deps: TurnDeps, walk: Walk, lead: str = "") -> Outcome:
    picked = tuple(d for d in walk.decisions if d.id in walk.ids)
    block = entry.block(picked, deps.today, walk.note)
    if walk.write_blocked:
        return _show_text(walk, picked, "Writing to your documents is blocked for this agent.")
    catalog, _ = await discover(deps, "doc")
    tool, kind, doc = _write_target(catalog, walk, deps.today)
    if tool is None:
        return _show_text(walk, picked, "No tool that appends to or creates a document is available to this agent.")
    approval = block_guard.WriteApproval.of(tool.name, kind, block, doc.id if kind == block_guard.APPEND else "",
                                            doc.title)
    creates = kind == block_guard.CREATE
    question = answers.write_question(len(picked), doc.title, creates)
    body = render.card(block, tool.name, doc, creates)
    lead_text = lead or "\n\n".join(p for p in (walk.lead,) if p)
    text = "\n\n".join(p for p in (lead_text, question["header"], body, answers.WRITE_HINT) if p)
    paused = replace(walk, stage="approve", approval=approval.to_dict(), doc=doc if creates else walk.doc, lead="")
    return Outcome("input_required", text, _metadata(paused), question, paused.state())


def _write_target(catalog: Catalog | None, walk: Walk, today: date) -> tuple[McpTool | None, str, Doc]:
    doc = walk.doc or Doc(title=entry.NEW_DOC_TITLE, state="create")
    if catalog is None:
        return None, "", doc
    append = catalog.append_tool()
    if doc.state == "found" and doc.id and append is not None:
        return append, block_guard.APPEND, doc
    create = catalog.create_tool()
    if create is None:
        return None, "", doc
    title = doc.title if doc.state == "create" else entry.weekly_doc_title(today)
    return create, block_guard.CREATE, Doc(title=title, state="create")


# --- resume ------------------------------------------------------------------------------------------

async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome:
    stored = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(stored, Mapping):
        return Outcome("new_turn")
    walk = Walk.from_metadata(stored)
    if walk.stage == "doc":
        return await _resume_doc(deps, walk, answer)
    if walk.stage == "select":
        return await _resume_select(deps, walk, answer)
    if walk.stage == "approve":
        return await _resume_approve(deps, walk, answer)
    if walk.stage == "gateway":
        return await _resume_gateway(deps, walk, answer)
    return Outcome("new_turn")


async def _resume_doc(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    choice = answers.pick(answer, answers.doc_labels(walk.docs, True))
    if choice == answers.SHOW_TEXT:
        return _show_text(walk, walk.fresh, "Nothing was written.")
    catalog, failure = await discover(deps, "doc")
    if catalog is None:
        return _complete(walk, failure or prompts.NO_DOC_TOOL_NOTE)
    if choice == answers.CREATE_NEW:
        doc = Doc(title=walk.title_hint or entry.NEW_DOC_TITLE, state="create")
        return await _after_doc(deps, replace(walk, doc=doc, stage=""))
    doc = answers.doc_for(choice, walk.docs)
    if doc is None:
        return _ask_doc(replace(walk, lead="Please pick one of these."), catalog)
    return await _use_doc(deps, catalog, replace(walk, stage=""), doc)


async def _resume_select(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    labels = {render.option_label(d): d.id for d in walk.fresh[:answers.MAX_OPTIONS]}
    picked = parse.read_selection(answer, {**labels, **{d.id: d.id for d in walk.decisions}})
    reviewed = replace(walk, report={**walk.report, "reviewed": True}, stage="")
    if picked.skip:
        return _complete(reviewed, "OK, nothing logged. Say \"log 1,3\" whenever you want entries added.")
    ids = _chosen(walk, picked.ids, picked.every)
    if not ids:
        if picked.ids or picked.every:
            return _complete(reviewed, prompts.NOTHING_LEFT if picked.every else prompts.UNKNOWN_NUMBERS)
        return Outcome("new_turn")
    lead = _every_note(walk, Setup(walk=walk, every=picked.every))
    return await _ask_approval(deps, replace(reviewed, ids=ids, note=picked.note), lead)


async def _resume_approve(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    decision, text = answers.decision(answer)
    if decision == "skip":
        done = replace(walk, report={**walk.report, "reviewed": True}, stage="", approval=None)
        return _complete(done, "Skipped: nothing was written. Say \"log 1,3\" later to add entries.")
    if decision == "other":
        return await _edit(deps, walk, text)
    approval = block_guard.WriteApproval.from_dict(walk.approval)
    if approval is None:
        return _complete(walk, "Nothing was written: the approval was incomplete. Please ask again.")
    catalog, failure = await discover(deps, "doc")
    tool = _tool(catalog, approval.tool)
    if tool is None:
        return _after_write(deps, walk, approval, WriteResult("not_done", failure or "the write tool is no longer "
                                                              "granted to this agent"))
    if approval.kind == block_guard.APPEND:
        changed = await _changed(deps, catalog, walk)
        if changed is not None:
            return changed
    log.info("block of %d decision(s) approved", len(walk.ids))
    return _after_write(deps, walk, approval, await docs.write(deps, tool, approval))


async def _changed(deps: TurnDeps, catalog: Catalog, walk: Walk) -> Outcome | None:
    """Re-read the doc right before the write: changed since it was read → show the card again, no write."""
    read = await docs.read(deps, catalog, walk.doc)
    if not read.ok:
        return _show_text(walk, _picked(walk), "I couldn't re-read the document to check it hadn't changed, so "
                                               "nothing was written.")
    if read.version == walk.doc.version:
        return None
    text = read.text[:MAX_LOG_CHARS]
    fresh = replace(walk, doc=replace(walk.doc, version=read.version), log_text=text,
                    decisions=dedupe.mark(walk.decisions, text))
    lead = "The document changed since I read it, so I read it again. Nothing was written; please check the card again."
    return await _ask_approval(deps, fresh, lead)


async def _edit(deps: TurnDeps, walk: Walk, text: str) -> Outcome:
    picked = parse.read_selection(text, {d.id: d.id for d in walk.decisions})
    note = answers.note_edit(text)
    if note is not None:
        return await _ask_approval(deps, replace(walk, note=note), "Updated the note. Please confirm the block.")
    ids = _chosen(walk, picked.ids, picked.every)
    if ids:
        return await _ask_approval(deps, replace(walk, ids=ids), "Updated the selection. Please confirm the block.")
    return await _ask_approval(deps, walk, answers.EDIT_HELP)


async def _resume_gateway(deps: TurnDeps, walk: Walk, answer: str) -> Outcome:
    approval = block_guard.WriteApproval.from_dict(walk.approval)
    if approval is None or not walk.loop:  # fail closed: never guess what was approved
        return _complete(walk, "Nothing was written: the pause state was incomplete.")
    catalog, failure = await discover(deps, "doc")
    tool = _tool(catalog, approval.tool)
    if tool is None:
        return _after_write(deps, walk, approval, WriteResult("not_done", failure or "the write tool is no longer "
                                                              "granted to this agent"))
    return _after_write(deps, walk, approval, await docs.resume(deps, tool, approval, walk.loop, answer))


def _tool(catalog: Catalog | None, name: str) -> McpTool | None:
    if catalog is None:
        return None
    return next((t for t in (catalog.append_tool(), catalog.create_tool()) if t is not None and t.name == name), None)


# --- after the write ---------------------------------------------------------------------------------

def _after_write(deps: TurnDeps, walk: Walk, approval: block_guard.WriteApproval, result: WriteResult) -> Outcome:
    picked = _picked(walk)
    if result.status == "paused":
        paused = replace(walk, stage="gateway", loop=result.loop)
        text = "The platform needs your approval before this is written:\n\n```markdown\n" + approval.block + "\n```"
        return Outcome("auth_required", text, _metadata(paused), {"hitl_request_id": result.hitl_request_id},
                       paused.state())
    done = replace(walk, stage="", approval=None, loop=None, report={**walk.report, "reviewed": True})
    if result.status == "done":
        doc = walk.doc if approval.kind == block_guard.APPEND else Doc(title=approval.title, state="created")
        link = docs.link_in(result.output, doc.link if doc else "")
        written = replace(done, written=(*walk.written, *(d.id for d in picked)),
                          doc=replace(doc, link=link, state="found" if approval.kind == block_guard.APPEND
                                      else "created"))
        verb = "Appended" if approval.kind == block_guard.APPEND else "Created"
        what = (f'{len(picked)} decision{"s" if len(picked) != 1 else ""} to "{approval.title}"'
                if approval.kind == block_guard.APPEND else f'"{approval.title}" with {len(picked)} '
                f'decision{"s" if len(picked) != 1 else ""}')
        shown = f"{verb} {what}." + (f" [Open the doc]({link})" if link.startswith("http") else "")
        return _complete(written, shown, link=link)
    if result.status == "denied":
        return _complete(done, "The write was denied, so nothing was written and I won't retry it.")
    if result.status == "blocked":
        blocked = replace(done, write_blocked=True)
        return _complete(blocked, render.copyable(approval.block, "Writing to your documents is blocked for this "
                                                                  "agent, so nothing was written."))
    return _complete(done, render.copyable(approval.block, f"Not written: {result.reason or 'unknown reason'}."))


def _picked(walk: Walk) -> tuple[Decision, ...]:
    return tuple(d for d in walk.decisions if d.id in walk.ids)


# --- endings -----------------------------------------------------------------------------------------

def _noted(walk: Walk, note: str) -> Walk:
    coverage = list(walk.report.get("coverage") or ())
    return walk if not note or note in coverage else replace(walk, report={**walk.report,
                                                                           "coverage": [*coverage, note]})


def _show_text(walk: Walk, picked: Sequence[Decision], reason: str) -> Outcome:
    """The list plus the block as copyable text; nothing is written."""
    listing = _list_text(walk, can_write=False)
    if not picked:
        return _complete(walk, "\n\n".join(p for p in (listing, reason) if p))
    block = entry.block(picked, _today(walk), walk.note)
    return _complete(walk, "\n\n".join([listing, render.copyable(block, reason or "Nothing was written.")]))


def _today(walk: Walk) -> date:
    return date.fromisoformat(str(walk.report.get("today") or date.today().isoformat()))


def _complete(walk: Walk, text: str, *, awaiting: bool = False, link: str = "") -> Outcome:
    data = render.envelope(walk.report, walk.decisions, walk.doc, walk.written, link, awaiting=awaiting)
    shown = render.as_json(data) if walk.json_mode else text
    return Outcome("complete", shown, _metadata(replace(walk, stage="")), None, walk.state(), data)


def _metadata(walk: Walk) -> dict:
    return {METADATA_KEY: walk.to_metadata()}
