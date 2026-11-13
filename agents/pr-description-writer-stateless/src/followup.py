"""Follow-ups on the stored draft and messages without a diff: the restyle call (one call over the stored
result), the scope classifier, and the zero-call answers (JSON re-render, split advice)."""

from __future__ import annotations

import json
import re
from collections import Counter

import draft
import llm
import prompts
from facts import GENERIC_DIRS, Facts
from llm import TurnDeps

_JSON_ONLY = re.compile(r"^(?:(?:show|give|send|return)(?: me)?(?: it| that| the draft| this)?\s*)?"
                        r"(?:as json|json only|in json|json)(?: please)?$")
_SPLIT = re.compile(r"\bsplit\b.*\b(?:prs?|pull requests?|this|it)\b|\b(?:two|2|separate|smaller) (?:prs|pull "
                    r"requests)\b", re.IGNORECASE)
_PR_WORDS = re.compile(r"\b(?:pr|prs|pull requests?|diff|patch|description|template|test plan|commit|"
                       r"changelog|merge request|mr)\b", re.IGNORECASE)
_WHY_LEAD = re.compile(r"^\s*(?:why|because|context|motivation|ticket|reason)\b\s*:?", re.IGNORECASE)


def is_json_only(text: str) -> bool:
    return bool(_JSON_ONLY.match(" ".join(re.sub(r"[^\w ]+", " ", text.casefold()).split())))


def asks_split(text: str) -> bool:
    return bool(_SPLIT.search(text or ""))


def mentions_pr(text: str) -> bool:
    return bool(_PR_WORDS.search(text or ""))


def adds_why(text: str) -> bool:
    return bool(_WHY_LEAD.match(text or ""))


def split_advice(facts: Facts) -> str:
    """Advice only, from the facts: the areas the diff touches and the code's split notes."""
    areas = Counter(_area(path) for path, kind in zip(facts.paths, facts.kinds) if kind != "generated")
    lines = [f"This diff touches {len(areas)} area{'s' if len(areas) != 1 else ''}: "
             + ", ".join(f"`{a}` ({n} file{'s' if n != 1 else ''})" for a, n in areas.most_common()) + "."]
    if len(areas) > 1:
        lines.append("One way to split it: one PR per area above, starting with the area the others depend on. "
                     "Tests and docs go with the code they cover.")
    else:
        lines.append("It stays in one area, so one PR is reasonable.")
    lines += [note for note in (facts.split_note, facts.size_note) if note]
    lines.append(f"Size now: {facts.files} files, +{facts.added} −{facts.removed}. This is advice only; I changed "
                 "nothing.")
    return "\n\n".join(lines)


def _area(path: str) -> str:
    parts = path.split("/")[:-1]
    while len(parts) > 1 and parts[0].casefold() in GENERIC_DIRS:
        parts = parts[1:]
    return parts[0] if parts else "(root)"


async def restyle(deps: TurnDeps, current: dict, facts_text: str, context: str, request: str,
                  policy: tuple[str, ...]) -> dict:
    context_part = prompts.fence(context, "CONTEXT") if context else "(none: the user gave no reason)"
    shown = {k: v for k, v in current.items() if k != "status"}
    messages = [{"role": "system", "content": prompts.RESTYLE_SYSTEM + prompts.org_block(policy)},
                {"role": "user", "content": f"{facts_text}\n\nCONTEXT:\n{context_part}\n\nCURRENT DRAFT:\n"
                                            f"{prompts.fence(json.dumps(shown, ensure_ascii=False))}\n\nREQUEST:\n"
                                            f"{prompts.fence(request)}"}]
    return await llm.complete_json(deps, messages, prompts.SCHEMA, draft.validate)


async def classify(deps: TurnDeps, text: str, policy: tuple[str, ...]) -> str:
    """"needs_diff" or "off_topic" for a message that has no diff and no stored draft."""
    messages = [{"role": "system", "content": prompts.CLASSIFY_SYSTEM + prompts.org_block(policy)},
                {"role": "user", "content": "MESSAGE:\n" + prompts.fence(text)}]

    def valid(data) -> dict | None:
        if isinstance(data, dict) and data.get("status") in ("needs_diff", "off_topic"):
            return {"status": data["status"]}
        return None

    result = await llm.complete_json(deps, messages, '{"status": "needs_diff|off_topic"}', valid, max_tokens=40)
    return result["status"]
