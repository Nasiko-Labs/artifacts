"""Follow-ups on a stored result: re-render commands parsed in code (zero calls), "ignore <segment>" (recompute
in code), "add event: …" (handled by the caller), or one scoped question answered only from the evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import llm
import prompts
import smalltalk
from fmt import numbers_in
from text import clean_text, ids_of

COMMAND_MAX_WORDS = 10
ANSWER_MAX_WORDS = 80
ASK_STATUSES = ("ok", "not_in_input", "off_topic")
_WORDS = re.compile(r"[^a-z0-9() ]+")
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_TOP = re.compile(r"\btop\s+(\d|one|two|three|four|five|six)\b")
_NUMBERS = re.compile(r"^(?:(?:please|just|only|now|again|me|the|show|give|list|see|all)\s+)*"
                      r"(?:numbers|evidence|sources|details|breakdown|the table|table|tables|data|calculations?|math)"
                      r"(?:\s+(?:only|please|again))*$")
_DEFAULT = re.compile(r"^(?:as json|json|json only|in json|again|show it again|summary|full report|all)$")
_IGNORE = re.compile(r"^(?:please\s+)?(?:ignore|exclude|drop|remove|leave out|skip|without)\b(.*)$")
_BLANK = re.compile(r"\b(?:blank|empty|unknown|missing)\b")


def plain(text: str) -> str:
    return " ".join(_WORDS.sub(" ", (text or "").casefold()).split())


def parse_command(text: str, dims: Sequence[str]) -> str | None:
    words = plain(text)
    if not words or len(words.split()) > COMMAND_MAX_WORDS:
        return None
    top = _TOP.search(words)
    if top:
        n = _NUMBER_WORDS.get(top.group(1)) or int(top.group(1))
        return f"top:{max(1, min(n, 6))}"
    for dim in sorted(dims, key=len, reverse=True):
        name = plain(dim)
        if re.fullmatch(rf"(?:(?:show|just|only|please|the|split)\s+)*(?:by\s+)?{re.escape(name)}(?:\s+only)?", words):
            return f"dim:{dim}"
    if _NUMBERS.match(words):
        return "numbers"
    if _DEFAULT.match(words):
        return "default"
    return None


def ignore_targets(text: str, segments: Iterable[str]) -> list[str] | None:
    """Segments to drop, [] when it asks to ignore something unknown, None when it isn't an ignore request."""
    match = _IGNORE.match(plain(text))
    if not match:
        return None
    rest = f" {match.group(1)} "
    found = [s for s in sorted(segments) if f" {plain(s)} " in rest]
    if "(blank)" in segments and _BLANK.search(rest) and "(blank)" not in found:
        found.append("(blank)")
    return found


async def ask(deps: llm.TurnDeps, question: str, envelope: Mapping[str, Any], context: Sequence[str] = ()) -> str:
    evidence = {e["id"]: e["text"] for e in envelope.get("evidence") or []}
    hyps = "\n".join(f"{h['rank']}. {h['type']}: {h['hypothesis']}" for h in envelope.get("hypotheses") or [])
    messages = prompts.ask_messages("\n".join(f"{k}: {v}" for k, v in evidence.items()), hyps, question, context)
    try:
        parsed = await llm.complete_json(deps, messages, prompts.ASK_CONTRACT, parse_answer)
    except llm.LlmError:
        return prompts.MODEL_FAILED
    if parsed["status"] == "off_topic":
        return smalltalk.OFF_TOPIC_REPLY
    return answer_text(parsed, evidence)


def parse_answer(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ASK_STATUSES:
        return None
    return {"status": data["status"], "answer": str(data.get("answer") or ""),
            "evidence_ids": ids_of(data.get("evidence_ids"), "e")}


def answer_text(parsed: Mapping[str, Any], evidence: Mapping[str, str]) -> str:
    """`ok` stands only when it cites known evidence and adds no number of its own."""
    ids = [i for i in dict.fromkeys(parsed["evidence_ids"]) if i in evidence]
    answer = clean_text(parsed["answer"])
    cited = " ".join(evidence[i] for i in ids)
    if parsed["status"] != "ok" or not answer or not ids or not numbers_in(answer) <= numbers_in(cited):
        return prompts.NOT_IN_INPUT
    short = " ".join(answer.split()[:ANSWER_MAX_WORDS])
    return f"{short}\n\nEvidence: " + "; ".join(f"{i}: {evidence[i]}" for i in ids)
