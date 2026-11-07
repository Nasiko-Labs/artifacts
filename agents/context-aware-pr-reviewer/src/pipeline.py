"""One review turn end to end: discover → pr → ticket question if none → (diff ∥ ticket) → similar →
precedent → review. Or, with a pasted diff, the review alone (no tool call). Returns a reply, a
question (which ticket?), or a Review."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import diffing
import review as review_step
import steps
import targets
from deps import TurnDeps
from discovery import Discoverer
from nasiko_hitl import question_metadata
from reader import TurnReader
from records import Ticket
from report import Review
from targets import Target

log = logging.getLogger("pr_reviewer.pipeline")

DIFF_ONLY = "No ticket, review the diff only"
TICKET_HEADER = "Which ticket is this PR for?"
PASTED_TICKET_MIN_CHARS = 60


@dataclass(frozen=True)
class Result:
    reply: str = ""
    review: Review | None = None
    question: dict | None = None    # {"text": ..., "hitl": ...}


def ticket_question(target: Target) -> dict:
    text = (f"I found no ticket linked from {target.describe()} (title, body, branch or commits). Which ticket is it "
            f"for? Reply with a key like ABC-123 or an issue number like #45, or choose \"{DIFF_ONLY}\".")
    hitl = question_metadata(TICKET_HEADER, [(DIFF_ONLY, "Review without ticket criteria.")], allow_custom_input=True)
    return {"text": text, "hitl": hitl}


async def run(deps: TurnDeps, target: Target) -> Result:
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return Result(failure)
    reader = TurnReader(deps, discoverer)
    pr, commits, reply = await steps.find_pr(reader, target)
    if pr is None:
        return Result(reply)
    refs = steps.ticket_refs(target, pr, commits)
    if not refs and not target.ticket:
        return Result(question=ticket_question(target))
    fetched = await steps.gather(reader, target, pr, refs)
    if not fetched.files:
        notes = " ".join(fetched.notes)
        return Result(f"I couldn't read the changes of {target.repo_full}#{pr.number}. {notes}".strip())
    inputs = review_step.Inputs(target.owner, target.repo, pr, fetched.files, fetched.ticket, fetched.refs,
                                fetched.similar, fetched.precedent, fetched.notes, fetched.seen, target.focus)
    built = await review_step.build(deps, inputs)
    return Result(built) if isinstance(built, str) else Result(review=built)


async def run_pasted(deps: TurnDeps, text: str, focus: str) -> Result:
    """A pasted `diff --git` diff, with the ticket text (if any) pasted above it. No tool call."""
    files, before = diffing.pasted_diff(text)
    if not files:
        return Result("I couldn't find a `diff --git` block in what you pasted.")
    notes = []
    if len(text) > diffing.MAX_PASTED_DIFF_CHARS:
        notes.append(f"Only the first {diffing.MAX_PASTED_DIFF_CHARS:,} pasted characters were read.")
    lines = before.strip().splitlines()
    ticket_text = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
    ticket = None
    if len(ticket_text) >= PASTED_TICKET_MIN_CHARS:
        key = targets.ticket_in(ticket_text) or "pasted ticket"
        ticket = Ticket(key, lines[1].strip()[:120], ticket_text)
    else:
        notes.append("No ticket text pasted above the diff, so there are no criteria to check.")
    inputs = review_step.Inputs("", "", None, files, ticket, (), (), {}, tuple(notes), text, focus, pasted=True)
    built = await review_step.build(deps, inputs)
    return Result(built) if isinstance(built, str) else Result(review=built)
