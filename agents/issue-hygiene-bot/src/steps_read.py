"""The two read steps: issues (tracker) and owners (chat lookup by email). Each is one bounded tool loop
whose JSON answer is checked in code; nothing the model says survives unless the tool returned it."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import checks
import coverage
import prompts
import replies
import smalltalk
from deps import TurnDeps
from reader import Reader, limit_note
from records import ChatUser, parse_issues, parse_users
from request import Request

log = logging.getLogger("issue_hygiene.steps")

ISSUES_LOOP_CALLS = 6   # plus one JSON repair
OWNERS_LOOP_CALLS = 3   # plus one JSON repair


@dataclass(frozen=True)
class IssuesOutcome:
    """Either a fixed reply that ends the turn, or the checked issues with what the reader noted."""

    reply: str | None
    check: checks.IssueCheck | None = None
    notes: tuple[str, ...] = ()
    salvaged: bool = False
    extracted: int = 0


async def read_issues(deps: TurnDeps, reader: Reader, query: str, request: Request) -> IssuesOutcome:
    if not reader.status.ok:
        return IssuesOutcome(replies.NO_TRACKER)
    system = prompts.issues_system(deps.today, deps.config, request.scope)
    run = await reader.run_json(deps, system, query, ISSUES_LOOP_CALLS, parse_issues)
    if run.status == "paused":
        return IssuesOutcome(replies.READ_NEEDS_APPROVAL)
    if not reader.status.ok:
        return IssuesOutcome(f"I couldn't read the tracker, so I can't check hygiene. {coverage.line(reader.status)}")
    answer = run.parsed
    if answer is None:
        return IssuesOutcome(replies.STEP_LIMIT_NOTE if limit_note(run) else replies.UNREADABLE_ANSWER)
    if answer.status == "off_topic":
        return IssuesOutcome(smalltalk.OFF_TOPIC_REPLY)
    checked = checks.check_issues(answer.issues, reader.seen_text, request.scope, deps.config)
    if checked.dropped:
        log.info("post-check dropped %d issue(s) not backed by tool output", checked.dropped)
    return IssuesOutcome(None, checked, limit_note(run), run.salvaged or run.status == "max_iterations",
                         len(answer.issues))


@dataclass(frozen=True)
class OwnersOutcome:
    users: tuple[ChatUser, ...]
    status: coverage.SourceStatus
    notes: tuple[str, ...] = ()
    salvaged: bool = False


async def lookup_owners(deps: TurnDeps, reader: Reader, emails: Sequence[str]) -> OwnersOutcome:
    if not emails or not reader.status.ok:
        return OwnersOutcome((), reader.status)
    run = await reader.run_json(deps, prompts.OWNERS_SYSTEM, prompts.owners_user(tuple(emails)), OWNERS_LOOP_CALLS,
                                parse_users)
    users = checks.check_users(run.parsed or (), reader.seen_text, emails)
    notes = limit_note(run)
    if run.status == "paused":
        notes = (*notes, "A chat lookup tool needs approval, so owners were not matched.")
    return OwnersOutcome(users, reader.status, notes, run.salvaged or run.status == "max_iterations")
