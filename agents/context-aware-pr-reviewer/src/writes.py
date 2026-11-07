"""The one write, prepared in code: a summary comment on the reviewed pull request.

The destination comes from checked data (the Review), never from the model; the write tool is chosen in
code from what the write query found; the body is the card built in code, with every `@` removed. Nothing
is written here: a `Proposal` goes to the approval walk."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any

import render
import send_guard
from deps import TurnDeps
from discovery import Discoverer
from nasiko_hitl import question_metadata
from nasiko_mcp import McpTool
from report import Review
from send_guard import Approval

log = logging.getLogger("pr_reviewer.writes")

POST, SKIP = "Post comment", "Skip"
_FINAL = frozenset({"sent", "denied"})
_YES = frozenset({"post", "post comment", "yes", "y", "ok", "okay", "approve", "approved", "authorized", "send",
                  "post it", "do it"})
_NO = frozenset({"skip", "no", "n", "cancel", "stop", "denied", "deny", "reject", "rejected", "decline", "declined",
                 "don't", "dont"})


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

    @property
    def destination(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"

    @property
    def title(self) -> str:
        return ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Proposal:
        return Proposal(**{f.name: str(data.get(f.name) or "") for f in fields(Proposal)})


@dataclass(frozen=True)
class Prepared:
    reply: str = ""
    proposal: Proposal | None = None


def write_key(review: Review, body: str) -> str:
    digest = hashlib.sha256(body.encode()).hexdigest()[:10]
    return f"comment:{review.pr_label}:{digest}"


async def prepare_comment(deps: TurnDeps, review: Review, outcomes: Mapping[str, str]) -> Prepared:
    body = render.comment_body(review)
    if review.pasted or not review.number:
        return Prepared(copyable("This review is of a pasted diff, so there is no pull request to comment on.", body))
    key = write_key(review, body)
    if outcomes.get(key) in _FINAL:
        return Prepared(f"I already handled this comment on {review.pr_label} ({outcomes[key]}); I won't post it twice.")
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return Prepared(failure)
    tool = pick_tool((await discoverer.catalog("post")).write_tools())
    if tool is None:
        return Prepared(copyable("I have no granted tool to comment on the pull request.", body))
    return Prepared(proposal=Proposal("comment", "post", key, tool.name, review.owner, review.repo, review.number, body))


def pick_tool(tools: Sequence[McpTool]) -> McpTool | None:
    """The write tool for a plain comment, by the words in its name."""
    def fits(tool: McpTool) -> bool:
        tokens = set(send_guard.name_tokens(tool.name))
        return "comment" in tokens and not tokens & {"review", "commit", "delete", "update", "edit", "reaction",
                                                     "reply", "line"}
    return next((t for t in tools if fits(t)), None)


def approval_for(p: Proposal, body: str) -> Approval:
    return Approval(action=p.kind, target=p.destination, draft=body, tool=p.tool, owner=p.owner, repo=p.repo,
                    number=p.number, bound=True)


def question(p: Proposal) -> tuple[str, dict]:
    """The pause text (everything that will be written) and the structured question for the card."""
    header = f"Comment on {p.destination}?"
    options = [(POST, "Post exactly this text."), (SKIP, "Don't write anything.")]
    hitl = question_metadata(header[:200], options, allow_custom_input=True)
    return describe(p, p.body) + "\n\nReply with an option, or type your own text to post instead.", hitl


def describe(p: Proposal, body: str) -> str:
    return "\n".join([f"Destination: {p.destination}", f"Via: {p.tool}", "", body])


def interpret(answer: str) -> tuple[str, str]:
    """(decision, body): skip / post (the proposed body) / edit (typed text, any @mention stripped).
    Empty and unclear refusals are skip: nothing is written by default."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if not word or word in _NO:
        return "skip", ""
    if word in _YES:
        return "post", ""
    return "edit", render.strip_mentions(text)


def copyable(reason: str, body: str) -> str:
    return f"{reason} Here is the text to copy:\n\n{body}"
