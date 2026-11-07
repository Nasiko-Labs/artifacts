"""Follow-ups on the stored comparison, read in code (zero model calls), plus the one scope check used when a
message is neither data nor a known follow-up."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import llm
import prompts
from llm import LlmError, TurnDeps
from match import jaccard

IN_SCOPE, OFF_TOPIC = "in_scope", "off_topic"
ITEM_MATCH_MIN = 0.25
BLOCKED_VS_DONE = ("D1", "D2", "D8")

_JSON = re.compile(r"^\W*(?:(?:show|give|return)(?: it| me| that| the result)? )?(?:as|in) json\W*$|^\W*json(?: only)?\W*$",
                   re.IGNORECASE)
_RISK = re.compile(r"\bonly (?:the )?(high|medium|low)(?:[- ]risk)?\b", re.IGNORECASE)
_BLOCKED_DONE = re.compile(r"\bonly (?:the )?blocked(?:[- ](?:vs\.?|versus)[- ]done)?\b", re.IGNORECASE)
_ALL = re.compile(r"\b(?:show|list) (?:me )?(?:all|everything)\b|\ball disagreements\b|\bfull (?:list|table)\b",
                  re.IGNORECASE)
_UNMATCHED = re.compile(r"\bunmatched\b|\bnot matched\b|\bno ticket found\b", re.IGNORECASE)
_TICKETS_FOR = re.compile(r"\b(?:show|list|which|what)\b.*?\btickets?\b.*?\b(?:for|of|behind|on)\s+(.+?)\W*$",
                          re.IGNORECASE)
_WHY = re.compile(r"\bwhy\b.*?\b(?:flag|flagged|flagging|disagree|mark|marked|risk)\w*\s+(.+?)(?:\s+(?:as|flagged)\b.*)?"
                  r"\W*$", re.IGNORECASE)
_WHY_SUBJECT_FIRST = re.compile(r"\bwhy (?:is|was|did) (.+?) (?:flagged|high|medium|low|a disagreement)\b",
                                re.IGNORECASE)
_ITEM_NUMBER = re.compile(r"^(?:item|#)\s*(\d{1,3})$", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str       # json | risk | blocked_done | all | unmatched | tickets | why
    arg: str = ""


def parse(text: str) -> Command | None:
    message = (text or "").strip()
    if not message:
        return None
    if _JSON.match(message):
        return Command("json")
    if match := _RISK.search(message):
        return Command("risk", match.group(1).casefold())
    if _BLOCKED_DONE.search(message):
        return Command("blocked_done")
    if match := _TICKETS_FOR.search(message):
        return Command("tickets", match.group(1).strip(" '\""))
    if match := _WHY_SUBJECT_FIRST.search(message) or _WHY.search(message):
        return Command("why", match.group(1).strip(" '\""))
    if _UNMATCHED.search(message):
        return Command("unmatched")
    if _ALL.search(message):
        return Command("all")
    return None


def find_item(items: Sequence[dict], ref: str) -> dict | None:
    """A report item by number ("item 3") or by name (exact, contained, then the closest title overlap)."""
    wanted = " ".join(ref.casefold().split())
    if match := _ITEM_NUMBER.match(wanted):
        n = int(match.group(1))
        return items[n - 1] if 1 <= n <= len(items) else None
    for item in items:
        if item["item"].casefold() == wanted:
            return item
    for item in items:
        name = item["item"].casefold()
        if wanted in name or name in wanted:
            return item
    best = max(items, key=lambda i: jaccard(i["item"], wanted), default=None)
    return best if best is not None and jaccard(best["item"], wanted) >= ITEM_MATCH_MIN else None


async def classify(deps: TurnDeps, text: str, has_result: bool, policy: Sequence[str]) -> str:
    """One call: in scope or off topic. A failed call counts as in scope (the reply then just explains)."""
    try:
        answer = await llm.complete_json(deps, prompts.classify_messages(text, has_result, policy),
                                         prompts.CLASSIFY_CONTRACT, _validate)
    except LlmError:
        return IN_SCOPE
    return answer["status"]


def _validate(data: object) -> dict | None:
    if isinstance(data, dict) and data.get("status") in (IN_SCOPE, OFF_TOPIC):
        return {"status": data["status"]}
    return None
