"""Pure: the retro's discussion prompts, picked by fixed rules from the computed numbers and the spill
reasons. Never generated: no invented problems, the same numbers always give the same prompts."""

from __future__ import annotations

from collections.abc import Sequence

from metrics import Metrics
from records import NO_REASON, Spill

MAX_PROMPTS = 5
CAPACITY = "What made the plan larger than our capacity?"
URGENT = "Which added work was truly urgent, and which could have waited?"
UNBLOCK = "What would have unblocked these earlier?"
ESTIMATES = "Which estimates were off and why?"
CAPTURE = "Nobody wrote down why these slipped; what should we capture next time?"
KEEP = "What made this cycle go well that we should keep?"
ALWAYS = "One thing to change next cycle?"


def discussion_prompts(metrics: Metrics, spills: Sequence[Spill]) -> tuple[str, ...]:
    rate, added = metrics.completion_rate, metrics.scope_added_pct
    reasons = [s.reason for s in spills]
    rules = (
        (rate is not None and rate < 70, CAPACITY),
        (added is not None and added >= 20, URGENT),
        (reasons.count("blocked_dependency") >= 2, UNBLOCK),
        (reasons.count("underestimated") >= 2, ESTIMATES),
        (reasons.count(NO_REASON) >= 2, CAPTURE),
        (rate is not None and rate >= 90 and added is not None and added < 10, KEEP),
    )
    picked = [text for hit, text in rules if hit][:MAX_PROMPTS - 1]
    return (*picked, ALWAYS)
