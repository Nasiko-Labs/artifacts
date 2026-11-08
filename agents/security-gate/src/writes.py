"""The one write, prepared in code: a summary comment on the scanned pull request.

The destination comes from the checked report, never from the model; the write tool is chosen in code from
what the write query found (approve / merge / review / label / close tools are refused by name even when
discovered); the body is rendered in code. Typed text on the card is an EDIT INSTRUCTION, applied by one
tool-less call that may only remove or shorten lines (checked in code), at most twice. Nothing is written
here: a `Proposal` goes to the approval walk."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

import comment
import prompts
import send_guard
from deps import TurnDeps
from discovery import Discoverer
from explain import load_object
from nasiko_hitl import question_metadata
from nasiko_mcp import McpTool
from reader import ask_once
from records import GateReport
from send_guard import Approval

log = logging.getLogger("security_gate.writes")

APPROVE, SKIP = "Approve", "Skip"
MAX_EDITS = 2
FORBIDDEN_TOKENS = frozenset({"merge", "approve", "approval", "review", "reviews", "label", "labels", "close",
                              "push", "dismiss", "lock", "block", "delete", "update", "edit", "reaction", "reply",
                              "line", "commit", "status", "check"})
_FINAL = frozenset({"sent", "denied"})
_YES = frozenset({"approve", "approved", "yes", "y", "ok", "okay", "post", "post it", "send", "do it", "authorized"})
_NO = frozenset({"skip", "no", "n", "cancel", "stop", "deny", "denied", "reject", "rejected", "don't", "dont"})


@dataclass(frozen=True)
class Proposal:
    kind: str
    step: str
    key: str
    tool: str
    owner: str
    repo: str
    number: str
    body: str
    edits: int = 0

    @property
    def destination(self) -> str:
        return f"{self.owner}/{self.repo} #{self.number}"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Proposal:
        values = {f.name: str(data.get(f.name) or "") for f in fields(Proposal) if f.name != "edits"}
        return Proposal(**values, edits=int(data.get("edits") or 0))


@dataclass(frozen=True)
class Prepared:
    reply: str = ""
    proposal: Proposal | None = None


def is_forbidden_tool(name: str) -> bool:
    return bool(set(send_guard.name_tokens(name)) & FORBIDDEN_TOKENS)


def pick_tool(tools: Sequence[McpTool]) -> McpTool | None:
    """The write tool for a plain comment, by the words in its name."""
    return next((t for t in tools if "comment" in send_guard.name_tokens(t.name) and not is_forbidden_tool(t.name)),
                None)


def write_key(report: GateReport) -> str:
    digest = hashlib.sha256(comment.body(report).encode()).hexdigest()[:10]
    return f"comment:{report.pr.label if report.pr else 'pasted'}:{digest}"


async def prepare_comment(deps: TurnDeps, report: GateReport, outcomes: Mapping[str, str]) -> Prepared:
    text = comment.body(report)
    if report.pr is None:
        return Prepared(copyable("This report is of pasted input, so there is no pull request to comment on.", text))
    key = write_key(report)
    if outcomes.get(key) in _FINAL:
        return Prepared(f"I already handled this comment on {report.pr.label} ({outcomes[key]}); I won't post it twice.")
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return Prepared(copyable(failure, text))
    tool = pick_tool((await discoverer.catalog("post")).write_tools())
    if tool is None:
        return Prepared(copyable(prompts.COMMENT_UNAVAILABLE, text))
    owner, _, repo = report.pr.repo.partition("/")
    return Prepared(proposal=Proposal("comment", "post", key, tool.name, owner, repo, report.pr.number, text))


def approval_for(p: Proposal) -> Approval:
    return Approval(action=p.kind, target=p.destination, draft=p.body, tool=p.tool, owner=p.owner, repo=p.repo,
                    number=p.number, bound=True)


def question(p: Proposal, note: str = "") -> tuple[str, dict]:
    """The pause text (everything that will be written) and the structured card."""
    header = f"Post this security summary on {p.destination}?"
    options = [(APPROVE, "Post exactly this text."), (SKIP, "Don't write anything.")]
    can_edit = p.edits < MAX_EDITS
    hitl = question_metadata(header[:200], options, allow_custom_input=can_edit)
    hint = ("Reply Approve or Skip, or type an edit (\"shorter\", \"remove the second finding\")."
            if can_edit else "Reply Approve or Skip (no more edits).")
    return "\n\n".join(x for x in (note, describe(p), hint) if x), hitl


def describe(p: Proposal) -> str:
    return "\n".join([f"Destination: {p.destination}", f"Via: {p.tool}", "", p.body])


def interpret(answer: str) -> str:
    """skip / post / edit. Empty and unclear refusals are skip: nothing is written by default."""
    word = (answer or "").strip().casefold().rstrip(".!")
    if not word or word in _NO:
        return "skip"
    return "post" if word in _YES else "edit"


async def apply_edit(deps: TurnDeps, p: Proposal, instruction: str) -> tuple[Proposal, str]:
    """(the proposal after the edit, a note). An edit that would add text is refused in code."""
    if p.edits >= MAX_EDITS:
        return p, "No more edits are possible; approve or skip this text."
    if not deps.traceparent:
        return replace(p, edits=p.edits + 1), prompts.NO_TRACE_CONTEXT
    messages = [{"role": "system", "content": prompts.EDIT_SYSTEM},
                {"role": "user", "content": f"{prompts.fence_data(p.body)}\n\nEdit instruction: {instruction}"}]
    data = load_object(await ask_once(deps, messages)) or {}
    edited = str(data.get("comment") or "").strip()
    if not edited or not comment.only_removes(p.body, edited):
        log.info("edit refused: it would add text")
        return replace(p, edits=p.edits + 1), "That edit would add text that isn't in the summary, so I kept the text."
    return replace(p, body=comment.with_closing(edited), edits=p.edits + 1), "Edited."


def copyable(reason: str, body: str) -> str:
    return f"{reason} Here is the text to copy:\n\n{body}"
