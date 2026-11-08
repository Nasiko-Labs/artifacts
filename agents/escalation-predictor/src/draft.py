"""Status-reply drafts, copy-only: one tool-less call per ticket with code-built facts, checked by
draft_check; one redraft with the problems named; else a fixed template chosen by the bug's state.
Nothing is ever sent."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import draft_check
import matching
import prompts
import records
from budget import TurnLimitReached
from deps import TurnDeps
from reader import ask_once

log = logging.getLogger("escalation.draft")

STARTED_TEMPLATE = ("We have identified the problem you reported and our engineers are working on it. We will "
                    "update you here as soon as we have news.")
LOGGED_TEMPLATE = "We have logged the problem and are investigating."


@dataclass(frozen=True)
class Draft:
    ticket_id: str
    customer: str
    body: str
    source: str        # "model" | "template"

    def to_dict(self) -> dict:
        return {"ticket_id": self.ticket_id, "customer": self.customer, "body": self.body, "source": self.source}


def facts_block(target: Mapping[str, Any]) -> str:
    lines = [f"Customer's ticket: #{target['ticket_id']}", f"Ticket subject: {target.get('subject') or 'unknown'}"]
    bug = _bug(target)
    if bug is None:
        lines.append("Known bug: none matched. Say only that the problem is logged and being investigated.")
    elif bug.get("closed"):
        lines.append(f"Known bug: \"{bug['title']}\", marked closed in the tracker, but it is not confirmed that "
                     "the fix has reached the customer. Do not say it is fixed.")
    else:
        lines.append(f"Known bug: \"{bug['title']}\", state \"{bug['state']}\", still open.")
    return "\n".join(lines)


def template(target: Mapping[str, Any]) -> str:
    bug = _bug(target)
    return STARTED_TEMPLATE if bug and matching.is_started(str(bug.get("state"))) else LOGGED_TEMPLATE


def _bug(target: Mapping[str, Any]) -> Mapping[str, Any] | None:
    bugs = target.get("bugs") or ()
    return bugs[0] if bugs else None


async def draft_one(deps: TurnDeps, target: Mapping[str, Any]) -> Draft:
    ticket_id, customer = str(target["ticket_id"]), str(target.get("customer") or "unknown")
    system = prompts.draft_system(facts_block(target))
    request = f"Draft the status reply for ticket #{ticket_id}."
    for attempt in range(2):
        try:
            body = records.parse_body(await ask_once(deps, ({"role": "system", "content": system},
                                                            {"role": "user", "content": request})))
        except TurnLimitReached:
            break
        issues = draft_check.problems(body, ticket_id, deps.config.max_reply_chars)
        if not issues:
            return Draft(ticket_id, customer, body, "model")
        log.info("draft for #%s broke a rule (%s); attempt %d", ticket_id, ", ".join(issues), attempt + 1)
        request = (f"Draft the status reply for ticket #{ticket_id} again. The last draft was rejected because "
                   f"{'; '.join(issues)}. Fix that.")
    return Draft(ticket_id, customer, template(target), "template")


async def draft_many(deps: TurnDeps, targets: Sequence[Mapping[str, Any]], ids: Sequence[str]) -> tuple:
    """Drafts for the requested ids that were scored, in order, at most `max_drafts`; and the ids unknown."""
    by_id = {str(t["ticket_id"]): t for t in targets}
    known = [i for i in ids if i in by_id][:deps.config.max_drafts]
    unknown = tuple(i for i in ids if i not in by_id)
    drafts = [await draft_one(deps, by_id[i]) for i in known]
    return tuple(drafts), unknown
