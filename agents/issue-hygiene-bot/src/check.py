"""One hygiene check: scope check → discover → issues (tracker) → rules (code) → group (code) → owners
(chat lookup) → the stored result, the rendered reply, the envelope and the picker. No message is drafted here
(the picker comes first), except the copyable text when nothing can be sent."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import coverage
import drafting
import envelope
import group
import pick
import prompts
import render
import replies
import rules
import smalltalk
import steps_read
from deps import TurnDeps
from discovery import discover
from dm import copyable_all
from envelope import Quality, Result
from reader import ask_once, reader_for
from request import Request, asks_for_edit, on_topic

log = logging.getLogger("issue_hygiene.check")


@dataclass(frozen=True)
class CheckOutcome:
    reply: str
    result: Result | None = None
    data: dict | None = None
    pause: pick.Pause | None = None


async def run(deps: TurnDeps, query: str, request: Request) -> CheckOutcome:
    if asks_for_edit(query):
        return CheckOutcome(replies.EDIT_DECLINED)
    if not deps.traceparent:
        return CheckOutcome(replies.NO_TRACE_CONTEXT)
    if not on_topic(query) and await off_topic(deps, query):
        return CheckOutcome(smalltalk.OFF_TOPIC_REPLY)
    catalog, failure = await discover(deps, "read")
    if failure is not None:
        return CheckOutcome(failure)
    tracker = reader_for(deps, catalog, "tracker")
    issues = await steps_read.read_issues(deps, tracker, query, request)
    if issues.reply is not None:
        return CheckOutcome(issues.reply)
    evaluation = rules.evaluate(issues.check.kept, deps.today, deps.config, request.rules)
    grouped = group.group(evaluation.findings)
    chat = reader_for(deps, catalog, "chat")
    emails = group.lookup_emails(grouped.owners, deps.config.max_owners)
    owners_read = await steps_read.lookup_owners(deps, chat, emails)
    owners = group.match_users(grouped.owners, owners_read.users)
    chat_ok = owners_read.status.ok
    quality = Quality(tracker_ok=True, chat_ok=chat_ok or not emails,
                      capped=bool(issues.check.capped or issues.check.teams_capped), extracted=issues.extracted,
                      dropped=issues.check.dropped, salvaged=issues.salvaged or owners_read.salvaged)
    notes = _notes(deps, request, issues, owners_read, len(grouped.owners), bool(emails))
    result = Result(label=_label(request, evaluation), owners=owners, unassigned=grouped.unassigned,
                    scores=evaluation.scores, notes=notes, quality=quality, chat_ok=chat_ok)
    return present(deps, result, {}, request.json_mode)


async def off_topic(deps: TurnDeps, query: str) -> bool:
    verdict = await ask_once(deps, ({"role": "system", "content": prompts.SCOPE_CHECK_SYSTEM},
                                    {"role": "user", "content": query}))
    return verdict.startswith(prompts.OFF_TOPIC)


def present(deps: TurnDeps, result: Result, outcomes: dict, json_mode: bool) -> CheckOutcome:
    """The reply for a stored result: markdown (or the fenced envelope), the envelope, and the picker."""
    data = envelope.build(result, outcomes, render.summary(result))
    if not result.flagged:
        text = "\n\n".join([replies.NOTHING_FLAGGED.format(old_days=deps.config.old_days), *result.notes])
        return CheckOutcome(envelope.as_json(data) if json_mode else text, result, data)
    if json_mode:
        return CheckOutcome(envelope.as_json(data), result, data)
    parts = [render.report(result, outcomes)]
    if not result.chat_ok and result.owners:
        parts.append(copyable_all(drafting.fixed_messages(result.owners, deps), replies.NO_CHAT_NOTE))
    unmatched = [o for o in result.owners if not o.user_id] if result.chat_ok else []
    if unmatched:
        parts.append(copyable_all(drafting.fixed_messages(unmatched, deps), replies.NO_CHAT_USER_NOTE))
    pause = pick.question(result.owners, outcomes, deps.config.max_owners) if result.chat_ok else None
    return CheckOutcome("\n\n".join(parts), result, data, pause)


def _label(request: Request, evaluation: rules.Evaluation) -> str:
    scope = request.scope
    if scope.team or scope.project:
        return ", ".join(p for p in (scope.team, f"project {scope.project}" if scope.project else "") if p)
    teams = [s.team for s in evaluation.scores]
    return ", ".join(teams) if teams else "all teams read"


def _notes(deps: TurnDeps, request: Request, issues: steps_read.IssuesOutcome, owners: steps_read.OwnersOutcome,
           owner_count: int, looked_up: bool) -> tuple[str, ...]:
    check, config = issues.check, deps.config
    notes = list(issues.notes)
    if request.rules != rules.ALL_RULES:
        notes.append("Rules checked: " + ", ".join(rules.RULE_NAMES[r] for r in request.rules) + " only.")
    if check.dropped:
        notes.append(f"{check.dropped} issue(s) reported by the model were not in the tracker's output, so they "
                     "are not shown.")
    if check.out_of_scope:
        notes.append(f"{check.out_of_scope} issue(s) outside the requested team or project were left out.")
    if check.capped:
        notes.append(f"Capped at {config.max_issues} issues; {check.capped} more were not checked.")
    if check.teams_capped:
        notes.append(f"Read at most {config.max_teams} teams; not checked: {', '.join(check.teams_capped)}.")
    if owner_count > config.max_owners:
        notes.append(f"Only the first {config.max_owners} owners were looked up in chat.")
    if looked_up and not owners.status.ok:
        notes.append(coverage.line(owners.status) + " Messages are shown as copyable text instead.")
    notes.extend(owners.notes)
    return tuple(dict.fromkeys(notes))
