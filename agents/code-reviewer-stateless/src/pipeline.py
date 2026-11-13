"""One review run: parse -> rank and cap the diff -> one crew kickoff (reviewer -> verifier [-> spec checker],
2 or 3 model calls) -> code checks -> merge -> envelope. An answer that is not JSON in its step's shape gets the
turn's one repair call (`repair.kickoff_checked`); a reviewer answer still unusable after it ends the run as
`unusable`, a verifier or spec-checker answer still unusable is treated as no verdicts."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import batching
import envelope
import merge
import prompts
import review
import spec
from checks import Context, norm
from diff_parse import FileDiff, Parsed
from inputs import Asks
from llm import LlmError, TurnDeps, UnusableAnswer
from repair import Check, kickoff_checked
from review import BatchResult

log = logging.getLogger("code-reviewer.pipeline")


@dataclass(frozen=True)
class Outcome:
    status: str                  # ok | off_topic | failed | unusable | empty
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
    user = review.user_prompt("\n\n".join(text for text, _ in plan.batches), others, asks)
    steps = [prompts.review_step(user, policy), prompts.verify_step(user, policy)]
    checks = [Check(review.reviewer_answer), Check(review.verifier_answer, required=False)]
    checked_spec = spec.spec_step(asks.spec, reviewed, policy) if asks.spec else None
    if checked_spec:
        steps.append(checked_spec[1])
        checks.append(Check(spec.answer, required=False))
    try:
        values = await kickoff_checked(deps, steps, checks)
    except UnusableAnswer:
        log.warning("review answer unusable", exc_info=True)
        return Outcome("unusable")
    except LlmError:
        log.warning("review crew failed", exc_info=True)
        return Outcome("failed")
    batch = review.read(values[0], values[1], ctx)
    if batch.status != "ok":
        return Outcome(batch.status)
    spec_result = spec.finish(values[2], checked_spec[0], reviewed) if checked_spec else None
    return Outcome("ok", _assemble([batch], spec_result, files, plan, asks, truncated))


def _assemble(batches: list[BatchResult], spec_result: dict | None, files: Sequence[FileDiff], plan: batching.Plan,
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
                             cut=cut, failed_batches=sum(b.status == "failed" for b in batches),
                             discarded=sum(b.discarded for b in batches))
    return envelope.build(comments, spec_result, files, plan, stats, counts, asks)
