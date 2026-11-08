"""The one model-written line: a short neutral headline built from counts only. The model sees counts,
never a person, email, system or row; its headline is checked in code (every number must be one of the
given counts, at most three sentences, no address, no name) and replaced by the code-built one if not."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence

import prompts
from deps import TurnDeps
from reader import ask_once

log = logging.getLogger("access_review.narrate")

MAX_HEADLINE_CHARS = 400
MAX_SENTENCES = 3
KIND_WORDS = {
    "terminated_has_access": "terminated with access", "not_on_roster": "not on the roster", "dormant": "dormant",
    "privileged_without_role_need": "privileged without a role need", "excess_owners": "excess owners",
    "outside_collaborator_admin": "outside collaborator admin", "no_manager_or_unknown_title": "missing manager or title",
    "unmatched_identity": "unmatched identity", "service_account_review": "service account to review",
}


def facts(holders: int, privileged: int, counts: Mapping[str, int], removals: int) -> str:
    lines = [f"holders reviewed: {holders}", f"privileged holders: {privileged}",
             f"findings: {sum(counts.values())}"]
    lines += [f"{KIND_WORDS.get(k, k)}: {v}" for k, v in sorted(counts.items())]
    lines.append(f"removal requests drafted: {removals}")
    return "\n".join(lines)


def code_headline(holders: int, counts: Mapping[str, int], removals: int) -> str:
    total = sum(counts.values())
    if not total:
        return f"No issues found across {holders} holder(s) under the stated policy."
    parts = ", ".join(f"{v} {KIND_WORDS.get(k, k)}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
    return f"{total} finding(s) across {holders} holder(s): {parts}. {removals} removal request(s) drafted."


def checked(headline: str, fact_text: str, forbidden: Sequence[str]) -> bool:
    if not headline or len(headline) > MAX_HEADLINE_CHARS or "@" in headline or "http" in headline.casefold():
        return False
    if len(re.findall(r"[.!?](?:\s|$)", headline)) > MAX_SENTENCES:
        return False
    allowed = set(re.findall(r"\d+", fact_text))
    if any(n not in allowed for n in re.findall(r"\d+", headline)):
        return False
    lowered = headline.casefold()
    return not any(len(f) > 2 and f.casefold() in lowered for f in forbidden)


async def headline(deps: TurnDeps, holders: int, privileged: int, counts: Mapping[str, int], removals: int,
                   forbidden: Sequence[str]) -> str:
    fallback = code_headline(holders, counts, removals)
    if not sum(counts.values()):
        return fallback
    fact_text = facts(holders, privileged, counts, removals)
    text = await ask_once(deps, ({"role": "system", "content": prompts.HEADLINE_SYSTEM},
                                 {"role": "user", "content": prompts.fence_facts(fact_text)}))
    answer = _object(text)
    line = " ".join(str(answer.get("headline") or "").split())
    if answer.get("status") == "ok" and checked(line, fact_text, forbidden):
        return line
    log.info("model headline dropped; using the code-built one")
    return fallback


def _object(text: str) -> dict:
    body = (text or "").strip().strip("`").removeprefix("json").strip()
    start, end = body.find("{"), body.rfind("}")
    try:
        data = json.loads(body[start:end + 1]) if start != -1 and end > start else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}
