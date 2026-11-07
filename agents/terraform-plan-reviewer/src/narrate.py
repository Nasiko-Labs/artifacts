"""The three model steps and the code checks on what they return.

- intent_check: which changes look unrelated to the stated intent (ids must exist; unknown ids ignored).
- narrate: the summary and one question per finding; any address-like token or number not in the facts, or
  "safe" on a non-safe verdict, makes code fall back to its own template (summary) or the rule's question.
- ask: a follow-up answered only from the stored facts, checked the same way.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

import prompts
from changes import Change
from llm import LlmError, TurnDeps, complete_json

ADDRESS_TOKEN = re.compile(r"(?:module\.[\w-]+(?:\[[^\]]*\])?\.)*(?:data\.)?[a-z][a-z0-9]*_[a-z0-9_]+\.[\w\-.]+"
                           r"(?:\[[^\]]*\])?")
NUMBER_TOKEN = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")
SAFE_WORDS = re.compile(r"\b(?:safe|no risks?|risk-free|harmless)\b", re.IGNORECASE)
MAX_SUMMARY_WORDS = 70
MAX_QUESTION_WORDS = 30
MAX_REASON_WORDS = 20
MAX_ANSWER_WORDS = 110
INTENT_GROUP = 5


@dataclass(frozen=True)
class Narration:
    summary: str
    questions: dict[str, str]     # finding id -> checked question
    fell_back: bool


def grounded(text: str, facts: str) -> bool:
    """Every address-like token and every number in `text` appears in the facts."""
    for token in ADDRESS_TOKEN.findall(text):
        if token.rstrip(".") not in facts:
            return False
    numbers = set(NUMBER_TOKEN.findall(facts))
    return all(n in numbers for n in NUMBER_TOKEN.findall(text))


async def narrate(deps: TurnDeps, digest: dict, policy: Sequence[str]) -> Narration | str:
    """The checked narration, or "off_topic"; raises LlmError when the model could not be used."""
    facts = json.dumps(digest, ensure_ascii=False)
    ids = {f["id"] for f in digest.get("findings") or ()}
    verdict = digest.get("verdict")

    def validate(data):
        if not isinstance(data, dict):
            return None
        if data.get("status") == "off_topic":
            return data
        return data if isinstance(data.get("summary"), str) else None

    data = await complete_json(deps, prompts.narrate_messages(digest, policy), prompts.NARRATE_CONTRACT, validate)
    if data.get("status") == "off_topic":
        return "off_topic"
    summary = " ".join(str(data.get("summary") or "").split())
    ok = (summary and len(summary.split()) <= MAX_SUMMARY_WORDS and grounded(summary, facts)
          and not (verdict != "safe" and SAFE_WORDS.search(summary)))
    questions: dict[str, str] = {}
    for item in data.get("questions") or ():
        if not isinstance(item, dict):
            continue
        fid, q = str(item.get("finding_id") or ""), " ".join(str(item.get("q") or "").split())
        if fid in ids and fid not in questions and q and len(q.split()) <= MAX_QUESTION_WORDS and grounded(q, facts):
            questions[fid] = q
    return Narration(summary if ok else "", questions, not ok)


async def intent_check(deps: TurnDeps, intent: str, changes: Sequence[Change],
                       policy: Sequence[str]) -> list[tuple[list[str], str]]:
    """Groups of (addresses, reason) the model judged unrelated to the intent; at most five per group."""
    acting = [c for c in changes if c.action not in ("noop", "read")]
    rows = [f"r{n}: {c.address} ({c.resource_type}, {c.action})" for n, c in enumerate(acting, start=1)]
    by_id = {f"r{n}": c for n, c in enumerate(acting, start=1)}

    def validate(data):
        return data if isinstance(data, dict) and isinstance(data.get("unrelated", []), list) else None

    data = await complete_json(deps, prompts.intent_messages(intent, rows, policy), prompts.INTENT_CONTRACT, validate)
    reasons = data.get("reason") if isinstance(data.get("reason"), dict) else {}
    facts = "\n".join(rows) + "\n" + intent
    picked: list[tuple[str, str]] = []
    for rid in dict.fromkeys(str(r) for r in data.get("unrelated") or ()):
        change = by_id.get(rid)
        if change is None:
            continue
        reason = " ".join(str(reasons.get(rid) or "").split())
        if not reason or len(reason.split()) > MAX_REASON_WORDS or not grounded(reason, facts):
            reason = "not obviously part of the stated intent"
        picked.append((change.address, reason))
    groups = []
    for start in range(0, len(picked), INTENT_GROUP):
        chunk = picked[start:start + INTENT_GROUP]
        groups.append(([a for a, _ in chunk], "; ".join(f"{a}: {r}" for a, r in chunk)))
    return groups


async def ask(deps: TurnDeps, question: str, digest: dict | None, policy: Sequence[str]) -> tuple[str, str]:
    """(status, answer): status is ok | need_plan | off_topic | failed."""
    def validate(data):
        if not isinstance(data, dict) or data.get("status") not in ("ok", "need_plan", "off_topic"):
            return None
        return data if data["status"] != "ok" or isinstance(data.get("answer"), str) else None

    try:
        data = await complete_json(deps, prompts.ask_messages(question, digest, policy), prompts.ASK_CONTRACT, validate)
    except LlmError:
        return "failed", prompts.FAILED_ANSWER
    if data["status"] != "ok":
        return data["status"], ""
    if digest is None:
        return "need_plan", ""
    answer = " ".join(data["answer"].split())
    facts = json.dumps(digest, ensure_ascii=False)
    if not answer or len(answer.split()) > MAX_ANSWER_WORDS or not grounded(answer, facts):
        return "ok", prompts.NOT_IN_REVIEW
    return "ok", answer
