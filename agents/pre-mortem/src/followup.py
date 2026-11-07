"""Follow-ups on the stored pre-mortem (parsed in code), the deep-dive call and the scope check for short
messages.

    "only the top 3", "show the plan lines", "as json", "show all"   -> re-render, zero calls
    "go deeper on 2"                                                -> one call (+ at most one repair)
    "add context: team of 3"                                        -> re-check + consolidate again
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from grounding import ungrounded
from llm import LlmError, TurnDeps, complete_json
from plan import numbered_block
from specific import locate_line, shares_plan_words

log = logging.getLogger("pre-mortem.followup")

MAX_SUB_CAUSES = 3
OFF_TOPIC, NEEDS_PLAN = "off_topic", "needs_plan"

_TOP = re.compile(r"\btop (\d{1,2})\b", re.IGNORECASE)
_JSON = re.compile(r"^\W*(?:(?:show|give|return|send)(?: me)?(?: it| this| that| the result)? )?"
                   r"(?:as|in) json\W*$|^\W*json(?: only)?\W*$", re.IGNORECASE)
_LINES = re.compile(r"\b(?:show|list|see)(?: me)? (?:the )?(?:numbered )?(?:plan )?lines\b|\bnumbered plan\b",
                    re.IGNORECASE)
_ALL = re.compile(r"^\W*(?:show )?(?:all|everything|the full (?:list|result)|full list)\W*$", re.IGNORECASE)
_DEEP = re.compile(r"\b(?:deeper|dig in(?:to)?|drill (?:down|into)|expand|more) (?:on |into )?"
                   r"(?:story |failure(?: mode)? |risk |number |no\.? |#)?(\d{1,2})\b", re.IGNORECASE)
_CONTEXT = re.compile(r"^\W*add context\s*[:\-]?\s*(.+)$", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class Command:
    kind: str           # top | json | lines | all | deep | context
    number: int = 0
    text: str = ""


def parse(text: str) -> Command | None:
    message = (text or "").strip()
    added = _CONTEXT.match(message)
    if added:
        return Command("context", text=added.group(1).strip())
    if _JSON.match(message):
        return Command("json")
    if _LINES.search(message):
        return Command("lines")
    deep = _DEEP.search(message)
    if deep:
        return Command("deep", number=int(deep.group(1)))
    top = _TOP.search(message)
    if top:
        return Command("top", number=int(top.group(1)))
    if _ALL.match(message):
        return Command("all")
    return None


def _validate_deep(data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None
    if data.get("status") == OFF_TOPIC:
        return {"status": OFF_TOPIC, "sub_causes": []}
    causes = data.get("sub_causes")
    return {"status": "ok", "sub_causes": causes} if isinstance(causes, list) else None


async def deep_dive(deps: TurnDeps, mode: dict, lines: tuple[str, ...], context: Sequence[str],
                    norm_ground: str, policy: Sequence[str] = ()) -> list[dict] | None:
    """2-3 checked sub-causes of one story; None when the call failed or was declined."""
    story = f"{mode['title']}: {mode['story']} (plan line L{mode['plan_line']})"
    messages = prompts.deep_messages(numbered_block(lines), context, story, policy)
    try:
        data = await complete_json(deps, messages, prompts.DEEP_CONTRACT, _validate_deep)
    except LlmError as e:
        log.warning("deep dive failed: %s", e)
        return None
    if data["status"] == OFF_TOPIC:
        return None
    kept: list[dict] = []
    for raw in data["sub_causes"]:
        if not isinstance(raw, dict) or len(kept) >= MAX_SUB_CAUSES:
            continue
        text = " ".join(str(raw.get("text") or "").split())
        ref = str(raw.get("plan_ref") or "")
        line = locate_line(ref, lines)
        if not text or line is None or not shares_plan_words(text, lines[line - 1]):
            continue
        if ungrounded(text, norm_ground):
            continue
        kept.append({"text": text, "plan_ref": " ".join(ref.split()), "plan_line": line})
    return kept


async def classify(deps: TurnDeps, text: str, policy: Sequence[str] = ()) -> str:
    """A short message with no plan: off topic, or a request that still needs the plan. A failed call
    counts as needs_plan (asking for the plan never answers an off-topic question)."""
    def validate(data: Any) -> dict | None:
        ok = isinstance(data, dict) and data.get("status") in (OFF_TOPIC, NEEDS_PLAN)
        return data if ok else None
    try:
        data = await complete_json(deps, prompts.scope_messages(text, policy), prompts.SCOPE_CONTRACT, validate)
    except LlmError as e:
        log.warning("scope check failed: %s", e)
        return NEEDS_PLAN
    return data["status"]
