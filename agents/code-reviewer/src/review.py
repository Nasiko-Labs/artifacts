"""The review map: one JSON call per batch of numbered diff, each comment checked in code, and at most one
repair call per batch (two per turn) that lists the exact violations."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import prompts
from checks import Comment, Context, check
from inputs import Asks, fence_text
from llm import LlmError, TurnDeps, ask_json

log = logging.getLogger("code-reviewer.review")
FORMATTING = "formatting"


@dataclass
class BatchResult:
    status: str = "ok"                    # ok | off_topic | failed
    comments: list[Comment] = field(default_factory=list)
    proposed: int = 0
    dropped: int = 0                      # could not be tied to an added line
    formatting: int = 0                   # skipped as formatting-only
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


async def review_batch(deps: TurnDeps, batch: str, others: Sequence[str], asks: Asks, ctx: Context,
                       policy: Sequence[str]) -> BatchResult:
    user = user_prompt(batch, others, asks)
    try:
        data = await ask_json(deps, prompts.messages(prompts.REVIEW_SYSTEM, user, policy))
    except LlmError:
        log.warning("review call failed", exc_info=True)
        return BatchResult("failed")
    if data is None or (data.get("status") == "ok" and not isinstance(data.get("comments"), list)):
        return BatchResult("failed")
    if data.get("status") == "off_topic":
        return BatchResult("off_topic")
    result, violations = _checked(data["comments"], ctx)
    if violations and deps.has_time() and deps.budget.take_repair():
        await _repair(deps, user, violations, ctx, policy, result)
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


async def _repair(deps: TurnDeps, user: str, violations: list[str], ctx: Context, policy: Sequence[str],
                  result: BatchResult) -> None:
    ask = user + "\n\nProblems with your comments:\n" + "\n".join(f"- {v}" for v in violations)
    try:
        data = await ask_json(deps, prompts.messages(prompts.REPAIR_SYSTEM, ask, policy))
    except LlmError:
        log.warning("repair call failed", exc_info=True)
        return
    fixed, _ = _checked(data.get("comments") if data and isinstance(data.get("comments"), list) else [], ctx)
    result.repaired = True
    result.comments.extend(fixed.comments)
    result.dropped = max(0, result.dropped - len(fixed.comments))
