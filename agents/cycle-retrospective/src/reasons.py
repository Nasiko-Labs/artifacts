"""The reasons step: tool-less calls, one per batch of spilled issues that have evidence, each labelling why
an issue spilled. Code keeps a label only when its quote is verbatim in that issue's own evidence; every
other spill (no evidence, unverifiable quote, not searched) is `no_reason_found`. Assignees never reach
the prompt: only the issue id, its title and the fenced evidence do."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import checks
import prompts
import records
import replies
from budget import TurnLimitReached
from deps import TurnDeps
from reader import ask_once
from records import NO_REASON, Evidence, Issue, Spill

log = logging.getLogger("retro.reasons")


def by_issue(evidence: Sequence[Evidence]) -> dict[str, tuple[Evidence, ...]]:
    grouped: dict[str, tuple[Evidence, ...]] = {}
    for item in evidence:
        grouped = {**grouped, item.issue_id: (*grouped.get(item.issue_id, ()), item)}
    return grouped


@dataclass(frozen=True)
class Reasons:
    spills: tuple[Spill, ...]
    notes: tuple[str, ...] = ()
    labelled: int = 0     # labels the model gave
    dropped: int = 0      # labels dropped because their quote was not verbatim in the evidence


async def reasons_step(deps: TurnDeps, spilled: Sequence[Issue], evidence: Sequence[Evidence]) -> Reasons:
    """One Spill per spilled issue, in the given order, and what the check dropped."""
    grouped = by_issue(evidence)
    with_evidence = [i for i in spilled if grouped.get(i.id)]
    labels: dict[str, tuple[str, Evidence | None, bool]] = {}
    notes: tuple[str, ...] = ()
    size = deps.config.reasons_batch
    for start in range(0, len(with_evidence), size):
        batch = with_evidence[start:start + size]
        try:
            labels = {**labels, **await _label(deps, batch, grouped)}
        except TurnLimitReached:
            notes = (replies.STEP_LIMIT_NOTE,)
            break
    spills = tuple(_spill(issue, *labels.get(issue.id, (NO_REASON, None, False))[:2]) for issue in spilled)
    return Reasons(spills, notes, len(labels), sum(1 for *_, dropped in labels.values() if dropped))


async def _label(deps: TurnDeps, batch: Sequence[Issue],
                 grouped: Mapping[str, Sequence[Evidence]]) -> dict[str, tuple[str, Evidence | None, bool]]:
    blocks = [f"Issue {i.id}: {i.title or '(no title)'}\n"
              + prompts.fence_evidence("\n---\n".join(f"[{e.source}] {e.text}" for e in grouped[i.id]))
              for i in batch]
    messages = ({"role": "system", "content": prompts.REASONS_SYSTEM},
                {"role": "user", "content": "\n\n".join(blocks)})
    parsed = records.parse_reasons(await ask_once(deps, messages)) or ()
    wanted = {i.id for i in batch}
    kept: dict[str, tuple[str, Evidence | None, bool]] = {}
    for issue_id, reason, quote in parsed:
        if issue_id not in wanted or issue_id in kept:
            continue
        label, holder = checks.check_reason(issue_id, reason, quote, grouped)
        if label != reason:
            log.info("reason for %s dropped: quote not found verbatim in its evidence", issue_id)
        kept = {**kept, issue_id: (label, holder if holder is None else _quoted(holder, quote), label != reason)}
    return kept


def _quoted(holder: Evidence, quote: str) -> Evidence:
    """The quote as the model gave it (it is verbatim in `holder`), with the holder's source and date."""
    return Evidence(issue_id=holder.issue_id, source=holder.source, text=quote.strip(), url=holder.url,
                    posted_at=holder.posted_at)


def _spill(issue: Issue, reason: str, quote: Evidence | None) -> Spill:
    return Spill(issue_id=issue.id, title=issue.title, url=issue.url, estimate=issue.estimate, reason=reason,
                 quote=quote.text if quote else "", source=quote.source if quote else "",
                 posted_at=quote.posted_at if quote else "")
