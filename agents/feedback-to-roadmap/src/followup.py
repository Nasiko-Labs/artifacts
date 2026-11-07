"""Follow-ups over the stored result. Answered in code when possible ("as json", "sort by count", "draft an issue
for theme 2"); otherwise one tool-less call that answers from the stored result only, or returns the
NEEDS_PULL / OFF_TOPIC sentinel. Every number in a model answer must appear in the stored result."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

import prompts
import render
import replies
import smalltalk
from budget import TurnLimitReached
from deps import TurnDeps
from parse_paste import Request
from reader import ask_once

_SORT = re.compile(r"\b(?:sort|order|rank)(?:ed)?\s+(?:them\s+|it\s+|themes\s+)?by\s+(\w+)", re.IGNORECASE)
_DRAFT = re.compile(r"\bdraft\b.*?\bissue\b.*?\btheme\s*#?(\d{1,2})\b", re.IGNORECASE)
_NEW_PULL = re.compile(r"\b(?:pull|refresh|again|re-?run|fetch|latest|new feedback|since last run)\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_SORT_KEYS = {"weight": "weight", "weighted": "weight", "score": "weight", "arr": "weight", "count": "count",
              "items": "count", "volume": "count", "customers": "customers", "accounts": "customers"}


@dataclass(frozen=True)
class FollowUp:
    reply: str = ""
    data: dict | None = None
    pull: bool = False


def wants_new_pull(request: Request) -> bool:
    return bool(request.pasted or request.channels or request.envelope or _NEW_PULL.search(request.text)
                or _window_named(request.text))


def _window_named(text: str) -> bool:
    return bool(re.search(r"\b(?:last|past|this|since)\s+\w+|\d{4}-\d{2}-\d{2}", text, re.IGNORECASE))


def sorted_themes(env: Mapping, by: str) -> dict:
    keys = {"weight": lambda t: (-t["weighted_score"], -t["count"], -t["distinct_customers"], t["name"].casefold()),
            "count": lambda t: (-t["count"], -t["weighted_score"], -t["distinct_customers"], t["name"].casefold()),
            "customers": lambda t: (-t["distinct_customers"], -t["weighted_score"], -t["count"], t["name"].casefold())}
    themes = sorted(env.get("themes") or [], key=keys[by])
    return {**env, "themes": themes, "items": themes}


async def followup_step(deps: TurnDeps, stored: dict, request: Request) -> FollowUp:
    text = request.text
    if match := _DRAFT.search(text):
        n = int(match.group(1))
        themes = stored.get("themes") or []
        if not 1 <= n <= len(themes):
            return FollowUp(replies.UNKNOWN_THEME.format(n=n))
        return FollowUp(render.issue_draft(n, themes[n - 1]))
    if match := _SORT.search(text):
        env = sorted_themes(stored, _SORT_KEYS.get(match.group(1).casefold(), "weight"))
        return FollowUp(render.as_json(env) if request.json_mode else render.report(env), env)
    if request.json_mode:
        return FollowUp(render.as_json(stored), stored)
    try:
        answer = await ask_once(deps, prompts.followup_messages(_prior(stored), text))
    except TurnLimitReached:
        return FollowUp(replies.STEP_LIMIT_NOTE)
    if prompts.NEEDS_PULL in answer:
        return FollowUp(pull=True)
    if prompts.OFF_TOPIC in answer or not answer:
        return FollowUp(smalltalk.OFF_TOPIC_REPLY)
    return FollowUp(answer if numbers_backed(answer, _prior(stored)) else replies.CANNOT_ANSWER)


def numbers_backed(answer: str, prior: str) -> bool:
    known = set(_NUMBER.findall(prior)) | {str(n) for n in range(1, 13)}  # theme numbers
    return all(n in known for n in _NUMBER.findall(answer))


def _prior(stored: Mapping) -> str:
    return json.dumps({k: stored.get(k) for k in ("summary", "window", "sources", "themes", "churn_signals", "caveats",
                                                  "confidence")}, ensure_ascii=False)[:30_000]
