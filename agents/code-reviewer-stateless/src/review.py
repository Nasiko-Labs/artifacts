"""The review: the reviewer's comments on the numbered diff, filtered by the verifier (`verify.py`), then each
comment checked in code against the parsed diff."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

import verify
from checks import Comment, Context, check
from inputs import Asks, fence_text

FORMATTING = "formatting"


@dataclass
class BatchResult:
    status: str = "ok"                    # ok | off_topic | failed
    comments: list[Comment] = field(default_factory=list)
    proposed: int = 0
    dropped: int = 0                      # could not be tied to an added line
    formatting: int = 0                   # skipped as formatting-only
    discarded: int = 0                    # discarded by the verifier
    repaired: bool = False


def focus_line(asks: Asks) -> str:
    parts = []
    if asks.axis:
        parts.append(f"Focus: the user asked for {asks.axis} comments only.")
    if asks.strict:
        parts.append("The user asked for a strict review: raise every real defect.")
    parts.append("Nits are welcome, including formatting." if asks.nits else "Skip nits unless they matter.")
    return " ".join(parts)


def user_prompt(batch: str, others: Sequence[str], asks: Asks) -> str:
    lines = [focus_line(asks), "All changed files in this diff (path: kind, status, +added -removed):",
             *[f"- {o}" for o in others]]
    if asks.standards:
        lines += ["The team's standards (they override the smell baseline):", fence_text("STANDARDS", asks.standards)]
    else:
        lines.append("No team standards were given: use only the smell baseline, as labelled judgement calls.")
    lines += ["The diff to review:", fence_text("INPUT", batch)]
    return "\n".join(lines)


def reviewer_answer(data: dict | None) -> dict | None:
    """The reviewer's answer when it is usable: off_topic, or a `comments` list."""
    if data is not None and (data.get("status") == "off_topic" or isinstance(data.get("comments"), list)):
        return data
    return None


def verifier_answer(data: dict | None) -> dict | None:
    """The verifier's answer when it is usable: off_topic, or a `verdicts` list."""
    if data is not None and (data.get("status") == "off_topic" or isinstance(data.get("verdicts"), list)):
        return data
    return None


def read(data: dict | None, verdicts: dict | None, ctx: Context) -> BatchResult:
    """The reviewer's checked answer, filtered by the verifier's, then every comment checked in code."""
    if data is not None and data.get("status") == "off_topic":
        return BatchResult("off_topic")
    if data is None or not isinstance(data.get("comments"), list):
        return BatchResult("failed")
    kept, discarded = verify.apply(data["comments"], verdicts)
    result, _ = _checked(kept, ctx)
    result.discarded = discarded
    return result


def _checked(raw: list, ctx: Context) -> tuple[BatchResult, list[str]]:
    """The batch's surviving comments, and the exact violations of those that were dropped."""
    result = BatchResult(proposed=len(raw))
    violations: list[str] = []
    for n, item in enumerate(raw, start=1):
        comment, reason = check(item, ctx)
        if comment is not None:
            result.comments.append(comment)
        elif reason == FORMATTING:
            result.formatting += 1
        else:
            result.dropped += 1
            violations.append(f"comment {n} ({json.dumps(item, ensure_ascii=False)[:600]}): {reason}")
    return result, violations
