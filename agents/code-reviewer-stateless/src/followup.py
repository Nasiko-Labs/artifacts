"""Follow-ups on the stored review. Re-render commands cost no call; "explain comment N" and "suggest a fix for N"
cost one call (a one-step crew) over the stored comment and its lines; the scope check costs one call."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

import prompts
import suggestion as sug
from checks import Context, clean_comment
from inputs import fence_text
from llm import LlmError, TurnDeps, UnusableAnswer
from repair import Check, kickoff_checked

log = logging.getLogger("code-reviewer.followup")
OFF_TOPIC, IN_SCOPE, FAILED = "off_topic", "in_scope", "failed"

_FILTERS = (
    (re.compile(r"\b(?:only|just)\s+(?:the\s+)?criticals?\b|\bcriticals?\s+only\b", re.IGNORECASE), ("critical",)),
    (re.compile(r"\b(?:only|just)\s+(?:the\s+)?(?:blocking|blockers|required)\b|\bblocking\s+only\b", re.IGNORECASE),
     ("critical", "required")),
)
_ALL = re.compile(r"^\s*(?:show\s+)?(?:all|everything|the full review|full review|all comments)\s*[.!]?\s*$",
                  re.IGNORECASE)
_JSON = re.compile(r"^\s*(?:(?:give|show)(?: me)?(?: it| that| the review)?\s+)?(?:as json|json only|in json|json)"
                   r"\s*[.!]?\s*$", re.IGNORECASE)
_EXPLAIN = re.compile(r"\b(?:explain|why|elaborate on|more on)\b(?:\s+(?:comment|#|no\.?|number))?\s*#?(\d{1,2})\b",
                      re.IGNORECASE)
_FIX = re.compile(r"\b(?:suggest|give|write|propose)?\s*(?:a\s+)?(?:fix|suggestion|patch)\s+(?:for|to)\s+"
                  r"(?:comment\s+)?#?(\d{1,2})\b", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str                         # filter | all | json | explain | fix
    severities: tuple[str, ...] = ()
    number: int = 0


def parse(text: str) -> Command | None:
    for rx, levels in _FILTERS:
        if rx.search(text or ""):
            return Command("filter", levels)
    if _ALL.match(text or ""):
        return Command("all")
    if _JSON.match(text or ""):
        return Command("json")
    fix = _FIX.search(text or "")
    if fix:
        return Command("fix", number=int(fix.group(1)))
    explain = _EXPLAIN.search(text or "")
    if explain:
        return Command("explain", number=int(explain.group(1)))
    return None


def excerpt(comment: dict, lines: dict[int, str], radius: int = 6) -> str:
    start, end = comment["line"] - radius, (comment.get("end_line") or comment["line"]) + radius
    return "\n".join(f"{n}: {lines[n]}" for n in sorted(lines) if start <= n <= end)


async def answer(deps: TurnDeps, command: Command, comment: dict, lines: dict[int, str], ctx: Context,
                 policy: Sequence[str]) -> tuple[str, str | None] | None:
    """(explanation, checked suggestion or None), or None when the model failed or declined. An answer still
    unusable after the one repair raises `UnusableAnswer`."""
    ask = "Explain this comment." if command.kind == "explain" else "Suggest a fix for this comment."
    target = {k: comment[k] for k in ("file", "line", "end_line", "quote", "severity", "comment")}
    user = "\n".join([ask, f"Comment: {target}", "Code around it (new-file line numbers):",
                      fence_text("INPUT", excerpt(comment, lines))])
    try:
        step = prompts.followup_step(user, policy)
        (data,) = await kickoff_checked(deps, [step], [Check(_status_in("ok", OFF_TOPIC))])
    except UnusableAnswer:
        raise
    except LlmError:
        log.warning("follow-up call failed", exc_info=True)
        return None
    if data.get("status") != "ok":
        return None
    explanation = clean_comment(str(data.get("explanation") or ""), ctx, data.get("suggestion"))
    end = comment.get("end_line") or comment["line"]
    original = "\n".join(lines.get(n, "") for n in range(comment["line"], end + 1))
    return explanation, sug.checked(data.get("suggestion"), original, comment["file"])


async def classify(deps: TurnDeps, text: str, policy: Sequence[str]) -> str:
    step = prompts.classify_step(fence_text("INPUT", text[:4_000]), policy)
    try:  # an answer still unusable after the repair counts as off topic, as before
        (data,) = await kickoff_checked(deps, [step], [Check(_status_in(IN_SCOPE, OFF_TOPIC), required=False)])
    except LlmError:
        log.warning("scope call failed", exc_info=True)
        return FAILED
    return OFF_TOPIC if not data or data.get("status") != IN_SCOPE else IN_SCOPE


def _status_in(*statuses: str):
    def validate(data: dict | None) -> dict | None:
        return data if data is not None and data.get("status") in statuses else None
    return validate
