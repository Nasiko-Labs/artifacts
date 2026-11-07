"""One review run: parse -> batch -> review (map, concurrent) + spec -> check -> merge -> envelope.
Budget: at most 6 review calls + 1 spec call + 2 repairs = 9 per turn, under the turn deadline."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

import batching
import envelope
import merge
from checks import Context, norm
from diff_parse import FileDiff, Parsed
from inputs import Asks
from llm import TurnDeps
from review import BatchResult, review_batch
from spec import check_spec


@dataclass(frozen=True)
class Outcome:
    status: str                  # ok | off_topic | failed | empty
    envelope: dict | None = None


def context_for(files: Sequence[FileDiff], asks: Asks | None) -> Context:
    asks = asks or Asks()
    vocabulary = "\n".join(norm(t) for f in files for t in (*f.added.values(), *f.context.values(), *f.removed))
    vocabulary += "\n" + "\n".join(f.path for f in files) + "\n" + norm(asks.standards)
    return Context(files={f.path: f for f in files}, vocabulary=vocabulary, standards=asks.standards, nits=asks.nits)


async def run(deps: TurnDeps, parsed: Parsed, asks: Asks, policy: Sequence[str], truncated: bool = False) -> Outcome:
    files = parsed.files
    plan = batching.plan(files)
    if not plan.batches:
        return Outcome("empty")
    ctx = context_for(files, asks)
    others = [batching.describe(f) for f in batching.ranked(files)]
    reviewed = [f for f in batching.ranked(files) if any(f.path in paths for _, paths in plan.batches)]
    jobs = [review_batch(deps, text, others, asks, ctx, policy) for text, _ in plan.batches]
    if asks.spec:
        jobs.append(check_spec(deps, asks.spec, reviewed, policy))
    results = await asyncio.gather(*jobs)
    batches: list[BatchResult] = list(results[:len(plan.batches)])
    spec = results[-1] if asks.spec else None
    if all(b.status == "off_topic" for b in batches):
        return Outcome("off_topic")
    if all(b.status == "failed" for b in batches):
        return Outcome("failed")
    return Outcome("ok", _assemble(batches, spec, files, plan, asks, truncated))


def _assemble(batches: list[BatchResult], spec: dict | None, files: Sequence[FileDiff], plan: batching.Plan,
              asks: Asks, truncated: bool) -> dict:
    checked = [c for b in batches for c in b.comments]
    if asks.axis:
        checked = [c for c in checked if c.axis == asks.axis]
    checked += merge.lost_assertions([f for f in files if any(f.path in p for _, p in plan.batches)])
    comments, cut = merge.ordered(merge.merged(checked), files)
    proposed = sum(b.proposed for b in batches)
    survived = sum(len(b.comments) for b in batches)
    stats = merge.Stats(proposed=proposed, survived=min(survived, proposed), repairs=sum(b.repaired for b in batches),
                        skipped_files=sum(1 for s in plan.skipped if s.endswith("(size cap)")),
                        partial_files=len(plan.partial) + sum(b.status == "failed" for b in batches),
                        truncated=truncated)
    counts = envelope.Counts(dropped=sum(b.dropped for b in batches), formatting=sum(b.formatting for b in batches),
                             cut=cut, failed_batches=sum(b.status == "failed" for b in batches))
    return envelope.build(comments, spec, files, plan, stats, counts, asks)
