"""The analysis phase: criteria → review per chunk → verify → merge → align, then the code-built Review.
Used both for a pull request read through the tools and for a pasted diff."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import align
import analysis
import diffing
import prompts
import verify
from deps import TurnDeps
from diffing import FilePatch
from records import PullRequest, ReviewComment, Ticket
from report import Review
from similar import Similar

log = logging.getLogger("pr_reviewer.review")


@dataclass(frozen=True)
class Inputs:
    owner: str
    repo: str
    pr: PullRequest | None
    files: tuple[FilePatch, ...]
    ticket: Ticket | None = None
    refs: tuple[str, ...] = ()
    similar: tuple[Similar, ...] = ()
    precedent: Mapping[str, tuple[ReviewComment, ...]] | None = None
    notes: tuple[str, ...] = ()
    seen: str = ""
    focus: str = ""
    pasted: bool = False

    @property
    def repo_full(self) -> str:
        return f"{self.owner}/{self.repo}" if self.owner and self.repo else ""


def _precedent_text(i: Inputs) -> str:
    lines = []
    for s in i.similar:
        for c in (i.precedent or {}).get(s.number, ()):
            where = f" on {c.path}" if c.path else ""
            lines.append(f"{s.ref(i.repo_full)}{where}: {c.body}")
    return "\n".join(lines)


async def build(deps: TurnDeps, i: Inputs) -> Review | str:
    """The review, or the reply that ends the turn (nothing reviewable)."""
    plan = diffing.plan(i.files)
    if not plan.chunks:
        return prompts.EMPTY_DIFF.format(pr=f"{i.repo_full}#{i.pr.number}" if i.pr else "(pasted)")
    criteria, not_verbatim = await analysis.extract_criteria(deps, i.ticket.text) if i.ticket else ((), 0)
    by_path = {f.path: f for f in i.files}
    reviewed = tuple(by_path[p] for p in plan.reviewed)
    precedent_text = _precedent_text(i)
    refs = frozenset(s.ref(i.repo_full) for s in i.similar)
    seen = "\n".join([i.seen, i.ticket.text if i.ticket else "", precedent_text, *(c.text for c in plan.chunks),
                      *(f.patch for f in i.files)])
    reviews = await analysis.review_chunks(deps, plan.chunks, criteria, precedent_text, i.focus, by_path, seen, refs)
    merged = align.merge(criteria, [r.criteria for r in reviews])
    found = [c for r in reviews for c in r.comments] + list(align.gap_comments(merged, reviewed))
    comments, cut = verify.finalize(found)
    dropped = sum(r.dropped for r in reviews)
    label = align.label(merged, reviewed, has_ticket=i.ticket is not None)
    creep, percent = align.scope_creep(merged, reviewed)
    notes = _notes(i, plan, reviews, dropped, not_verbatim, cut)
    review = Review(
        owner=i.owner, repo=i.repo, number=i.pr.number if i.pr else "", title=i.pr.title if i.pr else "",
        url=i.pr.url if i.pr else "", ticket=i.ticket.key if i.ticket else "",
        ticket_title=i.ticket.title if i.ticket else "", other_refs=tuple(r for r in i.refs[1:]),
        criteria=tuple(c.to_dict() for c in merged), comments=tuple(c.to_dict() for c in comments), label=label,
        coverage=align.coverage(merged), scope_creep=creep, scope_percent=percent,
        similar=tuple({"pr": s.ref(i.repo_full), "overlap": s.overlap, "title": s.title} for s in i.similar),
        notes=notes, reviewed=plan.reviewed, skipped=plan.skipped, not_reviewed=plan.not_reviewed,
        dropped=dropped, focus=i.focus, pasted=i.pasted, chunks_ok=all(r.ok for r in reviews))
    facts, fallback = _facts(review)
    summary = await analysis.summarize(deps, facts, fallback)
    return Review(**{**review.__dict__, "summary": summary})


def _notes(i: Inputs, plan: diffing.Plan, reviews: Sequence[analysis.ChunkReview], dropped: int,
           not_verbatim: int, cut: int) -> tuple[str, ...]:
    notes = list(i.notes)
    failed = sum(1 for r in reviews if not r.ok)
    if failed:
        notes.append(f"{failed} of {len(reviews)} diff chunks could not be reviewed.")
    if plan.not_reviewed:
        notes.append(f"Reviewed the first {len(plan.reviewed)} files by changed lines; not reviewed: "
                     + ", ".join(plan.not_reviewed) + ".")
    if dropped:
        notes.append(f"{dropped} comment{'s' if dropped != 1 else ''} dropped as unverifiable.")
    if not_verbatim:
        notes.append(f"{not_verbatim} criteri{'a' if not_verbatim != 1 else 'on'} not found verbatim in the ticket "
                     "and left out.")
    if cut:
        notes.append(f"{cut} more comment{'s' if cut != 1 else ''} beyond the first {verify.MAX_COMMENTS} not shown.")
    return tuple(dict.fromkeys(notes))


def _facts(r: Review) -> tuple[str, str]:
    comments = r.comment_objs()
    blockers = sum(1 for c in comments if c.severity == "blocker")
    missing = [c for c in r.criteria_objs() if c.status == "not_found"]
    said = f"{len(comments)} comment{'s' if len(comments) != 1 else ''}, {blockers} blocker{'s' if blockers != 1 else ''}"
    lines = [f"Pull request: {r.pr_label} {r.title}".strip(),
             f"Ticket: {r.ticket or 'none'} {r.ticket_title}".strip(),
             f"Alignment: {r.label.upper()} ({r.coverage} criteria met)",
             f"Comments: {len(comments)} ({blockers} blockers)",
             *(f"Not found: {c.id} {c.text}" for c in missing)]
    if r.label in ("no_ticket", "unclear"):
        fallback = f"Reviewed {r.pr_label} without ticket criteria; {said}."
    else:
        fallback = f"{r.pr_label} meets {r.coverage} of {r.ticket}'s criteria ({r.label}); {said}."
    return "\n".join(lines), fallback
