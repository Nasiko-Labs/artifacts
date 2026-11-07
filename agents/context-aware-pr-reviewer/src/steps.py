"""The fetch phase as tool steps: pr → (diff ∥ ticket) → similar → precedent. The model drives the
tools; code reads the facts out of the raw tool output in the turn's ledger afterwards."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import prompts
import records
import similar as sim
import targets
from diffing import FilePatch
from reader import StepRun, TurnReader
from records import PullRequest, ReviewComment, Ticket
from targets import Target

log = logging.getLogger("pr_reviewer.steps")

PR_CALLS, PR_SECS = 2, 45.0
DIFF_CALLS, DIFF_SECS = 3, 60.0
TICKET_CALLS, TICKET_SECS = 2, 45.0
SIMILAR_CALLS, SIMILAR_SECS = 3, 60.0
PRECEDENT_CALLS, PRECEDENT_SECS = 2, 45.0
MAX_SIMILAR_PATHS = 10


@dataclass(frozen=True)
class Fetched:
    pr: PullRequest
    files: tuple[FilePatch, ...] = ()
    refs: tuple[str, ...] = ()
    ticket_ref: str = ""
    ticket: Ticket | None = None
    similar: tuple[sim.Similar, ...] = ()
    precedent: dict[str, tuple[ReviewComment, ...]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    seen: str = ""


def _notes(*runs: StepRun) -> tuple[str, ...]:
    return tuple(r.note for r in runs if r.note)


async def find_pr(reader: TurnReader, target: Target) -> tuple[PullRequest | None, tuple[str, ...], str]:
    """(the pull request, its commit messages, a reply that ends the turn or "")."""
    step = await reader.run("pr", prompts.pr_system(target.describe(), reader.deps.now), "Fetch it.", PR_CALLS, PR_SECS)
    if step.status == "paused":
        return None, (), prompts.READ_NEEDS_APPROVAL
    if step.status == "no_tools":
        return None, (), f"{step.note} {prompts.NO_GATEWAY}"
    texts = [c.text for c in reader.ledger.of("pr")]
    prs = [p for t in texts for p in records.pull_requests(t)]
    if target.number:
        pr = next((p for p in prs if p.number == target.number), None)
    else:
        mine = [p for p in prs if p.head_ref == target.branch]
        pr = max(mine, key=lambda p: int(p.number) if p.number.isdigit() else 0, default=None)
    if pr is None:
        hint = "" if prs else (" None of the tools available to me returned a pull request: check that the code-host "
                               "connector is enabled for this agent, or paste the diff (`diff --git` text) instead.")
        extra = f" {step.note}" if step.note else ""
        return None, (), f"I couldn't find pull request {target.describe()}.{hint}{extra}"
    commits = tuple(m for t in texts for m in records.commit_messages(t))
    return pr, commits, ""


def ticket_refs(target: Target, pr: PullRequest, commits: tuple[str, ...]) -> tuple[str, ...]:
    if target.ticket == targets.NO_TICKET:
        return ()
    found = targets.ticket_keys(pr.title, pr.body, pr.head_ref, commits)
    if target.ticket:
        return (target.ticket, *(k for k in found if k != target.ticket))
    return found


async def gather(reader: TurnReader, target: Target, pr: PullRequest, refs: tuple[str, ...]) -> Fetched:
    describe = f"{target.repo_full}#{pr.number}"
    ref = refs[0] if refs else ""
    diff_job = reader.run("diff", prompts.diff_system(describe, reader.deps.now), "Fetch it.", DIFF_CALLS, DIFF_SECS)
    ticket_job = (reader.run("ticket", prompts.ticket_system(ref, target.repo_full, reader.deps.now), "Fetch it.",
                             TICKET_CALLS, TICKET_SECS) if ref else _none("ticket"))
    diff_run, ticket_run = await asyncio.gather(diff_job, ticket_job)
    files = _files(reader)
    ticket, ticket_note = _ticket(reader, ref) if ref else (None, "")
    notes = (*_notes(diff_run, ticket_run), *((ticket_note,) if ticket_note else ()))
    fetched = Fetched(pr, files, refs, ref, ticket, notes=notes)
    if not files:
        return fetched
    return await _precedent(reader, target, await _similar(reader, target, fetched))


async def _none(step: str) -> StepRun:
    return StepRun(step)


def _files(reader: TurnReader) -> tuple[FilePatch, ...]:
    by_path: dict[str, FilePatch] = {}
    for call in reader.ledger.of("diff"):
        for f in records.files(call.text):
            if f.patch or f.path not in by_path:
                by_path[f.path] = f
    return tuple(by_path.values())


def _ticket(reader: TurnReader, ref: str) -> tuple[Ticket | None, str]:
    calls = reader.ledger.of("ticket")
    ticket = next((t for c in calls if (t := records.ticket(c.text, ref)) is not None), None)
    if ticket is None:
        return None, f"Couldn't read ticket {ref}; reviewed without ticket criteria."
    comments = tuple(dict.fromkeys(m for c in calls for m in records.ticket_comments(c.text)
                                   if m not in (ticket.description, ticket.title)))
    return Ticket(ticket.key, ticket.title, ticket.description, ticket.url, comments), ""


async def _similar(reader: TurnReader, target: Target, f: Fetched) -> Fetched:
    paths = [x.path for x in sorted(f.files, key=lambda x: -x.changed_lines)][:MAX_SIMILAR_PATHS]
    run = await reader.run("similar", prompts.similar_system(target.repo_full, f.pr.number, ", ".join(paths),
                                                              reader.deps.now), "Search.", SIMILAR_CALLS, SIMILAR_SECS)
    ranked = sim.rank(reader.ledger.of("similar"), f.pr.number, (x.path for x in f.files))
    return Fetched(**{**f.__dict__, "similar": ranked, "notes": (*f.notes, *_notes(run))})


async def _precedent(reader: TurnReader, target: Target, f: Fetched) -> Fetched:
    if not f.similar:
        return Fetched(**{**f.__dict__, "seen": reader.ledger.seen})
    numbers = ", ".join(f"#{s.number}" for s in f.similar)
    run = await reader.run("precedent", prompts.precedent_system(target.repo_full, numbers, reader.deps.now),
                           "Fetch them.", PRECEDENT_CALLS, PRECEDENT_SECS)
    found = sim.precedent(reader.ledger.of("precedent", "similar"), (s.number for s in f.similar))
    notes = (*f.notes, *_notes(run))
    return Fetched(**{**f.__dict__, "precedent": found, "notes": notes, "seen": reader.ledger.seen})
