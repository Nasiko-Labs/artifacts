"""The two writes, prepared in code: a comment on the pull request, or a flaky-test ticket in the same
repository's issues (a comment on the open ticket for that test when one exists, never a duplicate).

The destination comes from checked data (the report), never from the model; the write tool is chosen
in code from what the write queries found; the text is built in code. Nothing is written here: a
`Proposal` goes to the approval walk.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any

import card_render
import prompts
import records
import send_guard
from analysis import Report
from deps import TurnDeps
from discovery import Discoverer
from nasiko_hitl import question_metadata
from nasiko_mcp import McpTool
from reader import TurnReader
from send_guard import Approval

log = logging.getLogger("ci_detective.writes")

POST, MENTION, OPEN, SKIP = "Post comment", "Post and mention", "Open ticket", "Skip"
TICKET_SEARCH_CALLS, TICKET_SEARCH_SECS = 2, 60.0
_FINAL = frozenset({"sent", "denied"})
_YES = frozenset({"post", "post comment", "open", "open ticket", "yes", "y", "ok", "okay", "approve", "approved",
                  "authorized", "send", "post it", "do it"})
_NO = frozenset({"skip", "no", "n", "cancel", "stop", "denied", "deny", "reject", "rejected", "decline", "declined",
                 "don't", "dont"})


@dataclass(frozen=True)
class Proposal:
    kind: str           # comment | ticket | ticket_comment
    step: str           # discovery step of the write tool: comment | ticket
    key: str            # one write per run and kind: "comment:<run>", "ticket:<run>"
    tool: str
    owner: str
    repo: str
    number: str
    title: str
    labels: tuple[str, ...]
    body: str
    mention: str = ""   # the author login offered as a separate option (REAL verdicts only)

    @property
    def destination(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}" if self.number else f"{self.owner}/{self.repo} issues"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["labels"] = list(self.labels)
        return data

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Proposal:
        values = {f.name: str(data.get(f.name) or "") for f in fields(Proposal) if f.name != "labels"}
        return Proposal(**values, labels=tuple(str(x) for x in data.get("labels") or ()))


@dataclass(frozen=True)
class Prepared:
    reply: str = ""
    proposal: Proposal | None = None


async def prepare_comment(deps: TurnDeps, report: Report, outcomes: Mapping[str, str]) -> Prepared:
    if not report.pr_number:
        return Prepared("This run isn't on a pull request, so there is no pull request to comment on.")
    key = f"comment:{report.run_id}"
    if outcomes.get(key) in _FINAL:
        return Prepared(f"I already handled the comment for run {report.run_id} ({outcomes[key]}); I won't post it twice.")
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return Prepared(failure)
    tool = pick_tool((await discoverer.catalog("comment")).write_tools(), want="comment")
    if tool is None:
        return Prepared(copyable("I have no granted tool to comment on the pull request.", report.comment))
    mention = report.culprit_login if report.verdict == "real" else ""
    return Prepared(proposal=Proposal("comment", "comment", key, tool.name, report.owner, report.repo,
                                      report.pr_number, "", (), report.comment, mention))


async def prepare_ticket(deps: TurnDeps, report: Report, outcomes: Mapping[str, str]) -> Prepared:
    if report.verdict != "flaky":
        return Prepared("I only open a flaky-test ticket when the verdict is FLAKY; this one is "
                        f"{report.verdict.upper() or 'not a verdict'}.")
    key = f"ticket:{report.run_id}"
    if outcomes.get(key) in _FINAL:
        return Prepared(f"I already handled the ticket for run {report.run_id} ({outcomes[key]}); I won't file it twice.")
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return Prepared(failure)
    existing = await _existing_ticket(deps, discoverer, report)
    writes = (await discoverer.catalog("ticket")).write_tools()
    if existing is not None:
        tool = pick_tool(writes, want="comment")
        if tool is None:
            return Prepared(copyable(f"Issue #{existing.number} already tracks this test, and I have no granted tool "
                                     "to comment on it.", report.ticket_update))
        return Prepared(proposal=Proposal("ticket_comment", "ticket", key, tool.name, report.owner, report.repo,
                                          existing.number, "", (), report.ticket_update))
    tool = pick_tool(writes, want="issue")
    if tool is None:
        return Prepared(copyable("I have no granted tool to open an issue.", report.ticket_body, report.ticket_title))
    return Prepared(proposal=Proposal("ticket", "ticket", key, tool.name, report.owner, report.repo, "",
                                      report.ticket_title, (card_render.TICKET_LABEL,), report.ticket_body))


async def _existing_ticket(deps: TurnDeps, discoverer: Discoverer, report: Report) -> records.Issue | None:
    """A read first: an open issue in the repository that names this test."""
    needle = report.test_id or report.job_name
    reader = TurnReader(deps, discoverer)
    await reader.run("ticket", prompts.ticket_search_system(report.repo_full, needle, deps.now), "Search.",
                     TICKET_SEARCH_CALLS, TICKET_SEARCH_SECS)
    found = [i for call in reader.ledger.of("ticket") for i in records.issues(call.text)]
    return next((i for i in found if i.state == "open" and (needle in i.title or needle in i.body)), None)


def pick_tool(tools: Sequence[McpTool], want: str) -> McpTool | None:
    """The write tool for a comment or a new issue, by the words in its name (then its description)."""
    def fits(tool: McpTool) -> bool:
        tokens = set(send_guard.name_tokens(tool.name))
        if want == "comment":
            return "comment" in tokens and not tokens & {"review", "commit", "delete", "update", "edit", "reaction"}
        return "issue" in tokens and "comment" not in tokens and bool(tokens & {"create", "open", "add", "new"})
    return next((t for t in tools if fits(t)), None)


def approval_for(p: Proposal, body: str) -> Approval:
    return Approval(action=p.kind, target=p.destination, draft=body, tool=p.tool, owner=p.owner, repo=p.repo,
                    number=p.number, title=p.title, labels=p.labels, bound=True)


def question(p: Proposal) -> tuple[str, dict]:
    """The pause text (everything that will be written) and the structured question for the card."""
    if p.kind == "comment":
        header = f"Comment on {p.owner}/{p.repo}#{p.number}?"
        options = [(POST, "Post exactly this text.")]
        if p.mention:
            options.append((f"{MENTION} @{p.mention}", "Post it and mention the commit author."))
    elif p.kind == "ticket_comment":
        header = f"Comment on existing issue #{p.number}?"
        options = [(POST, "Add exactly this comment to the open ticket.")]
    else:
        header = f"Open a flaky-test issue in {p.owner}/{p.repo}?"
        options = [(OPEN, "Open the issue with exactly this title and text.")]
    hitl = question_metadata(header[:200], [*options, (SKIP, "Don't write anything.")], allow_custom_input=True)
    return describe(p, p.body) + "\n\nReply with an option, or type your own text to use as the body instead.", hitl


def describe(p: Proposal, body: str) -> str:
    lines = [f"Destination: {p.destination}", f"Via: {p.tool}"]
    if p.title:
        lines.append(f"Title: {p.title}")
    if p.labels:
        lines.append(f"Labels: {', '.join(p.labels)}")
    return "\n".join([*lines, "", body])


def interpret(answer: str, p: Proposal) -> tuple[str, str]:
    """(decision, body): skip / post (the proposed body) / mention (only via its option) / edit (typed text,
    any @mention stripped). Empty and unclear refusals are skip: nothing is written by default."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if p.mention and word == f"{MENTION} @{p.mention}".casefold():
        return "mention", f"{p.body}\n\ncc @{p.mention}"
    if not word or word in _NO:
        return "skip", ""
    if word in _YES:
        return "post", p.body
    return "edit", card_render.strip_mentions(text)


def copyable(reason: str, body: str, title: str = "") -> str:
    head = f"Title: {title}\n\n" if title else ""
    return f"{reason} Here is the text to copy:\n\n{head}{body}"
