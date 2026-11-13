"""Follow-ups: view commands parsed in code (zero LLM calls), or one quote-checked question about a meeting
still in the conversation history (one call, plus one repair). Nothing is stored between messages."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import checks
import chunking
import grounding as g
import llm
import prompts
import smalltalk
from items import Turn

ASK_STATUSES = ("ok", "not_in_input", "off_topic")
COMMAND_MAX_WORDS = 10
ANSWER_MAX_WORDS = 80
NO_MATERIAL = "(no meeting has been provided yet)"

_FILLER = re.compile(r"\b(?:please|pls|can you|could you|would you|show me|give me|just|only|me|now|again|the|"
                     r"list|show|with|include|including|and|in|it|a|all|full)\b")
_COMMANDS = (
    ("json", re.compile(r"^(?:as |in )?json(?: only)?$")),
    ("table", re.compile(r"^(?:as |in )?(?:a )?(?:table|tabular(?: form)?)$")),
    ("mine", re.compile(r"^(?:mine|my (?:action items?|actions?|tasks|items|follow ?ups?)|what do i own|"
                        r"what'?s mine|what am i on the hook for)$")),
    ("followups", re.compile(r"^(?:follow ?-? ?ups?)$")),
    ("actions", re.compile(r"^(?:action items?|actions?|todos?|to-?dos|tasks|next steps)$")),
    ("decisions", re.compile(r"^(?:decisions?|what was decided)$")),
    ("sources", re.compile(r"^(?:sources?|quotes?|evidence|citations?|receipts)$")),
    ("notes", re.compile(r"^(?:notes|topics|topic notes)$")),
    ("default", re.compile(r"^(?:everything|again|notes again|whole thing|minutes)$")),
)
_PROCESS_WORDS = re.compile(r"\b(?:process|transcript|meeting notes|minutes|action items|notes from|"
                            r"summari[sz]e (?:the|this|my) (?:meeting|call|sync))\b")
_QUESTION_START = re.compile(r"^(?:what|who|whom|whose|when|where|why|how|which|is|are|was|were|do|does|did|can|"
                             r"could|should|would|will|tell|write|translate|explain|give|calculate|compute|list|"
                             r"name|define|describe|compose|draft|generate|create|make)\b")
_WORDS = re.compile(r"[^a-z0-9' -]+")


def _plain(text: str) -> str:
    return " ".join(_WORDS.sub(" ", (text or "").casefold()).split())


def parse_command(text: str) -> str | None:
    """The view for a short re-render message ("only mine", "as a table", "show sources"), or None."""
    plain = _plain(text)
    if not plain or len(plain.split()) > COMMAND_MAX_WORDS:
        return None
    core = " ".join(_FILLER.sub(" ", plain).split()) or plain
    for view, pattern in _COMMANDS:
        if pattern.match(core):
            return view
    return None


def is_process_request(text: str) -> bool:
    return bool(_PROCESS_WORDS.search(_plain(text)))


def is_question_like(text: str) -> bool:
    return (text or "").strip().endswith("?") or bool(_QUESTION_START.match(_plain(text)))


async def ask(deps: llm.TurnDeps, question: str, turns: Sequence[Turn], name: str = "TRANSCRIPT",
              context: Sequence[str] = (), cite: bool = True) -> str:
    """One quote-checked answer from `turns`: a transcript's turns, or (name NOTES) the lines of an earlier
    notes reply, whose line ids mean nothing to the user and so are not cited (`cite=False`)."""
    material = chunking.as_text(turns) if turns else NO_MATERIAL
    try:
        parsed = await llm.complete_json(deps, prompts.ask_messages(name, material, question, context),
                                         prompts.ASK_CONTRACT, parse_answer)
    except llm.LlmError:
        return prompts.MODEL_FAILED
    if parsed["status"] == "off_topic":
        return smalltalk.OFF_TOPIC_REPLY
    if not turns:
        return smalltalk.NO_TEXT_YET
    return answer_text(parsed, turns, cite)


def parse_answer(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ASK_STATUSES:
        return None
    return {"status": data["status"], "answer": str(data.get("answer") or ""),
            "turn_id": str(data.get("turn_id") or ""), "quote": str(data.get("quote") or "")}


def answer_text(parsed: Mapping[str, str], turns: Sequence[Turn], cite: bool = True) -> str:
    """`ok` stands only when its quote is in the cited turn (or exactly one other) and the answer adds no
    number or date of its own."""
    index = checks.Index(turns, ())
    answer = checks.clean_text(parsed["answer"], index.source)
    if parsed["status"] != "ok" or not answer or g.ungrounded_tokens(answer, index.norm_source):
        return prompts.NOT_IN_INPUT
    found = checks.find_quote(parsed["quote"], [parsed["turn_id"]], index)
    if not found:
        return prompts.NOT_IN_INPUT
    turn = turns[index.by_id[found[0]]]
    quote = checks.clean_text(checks.cap_quote(parsed["quote"]), parsed["quote"])
    when = f", {turn.time}" if turn.time else ""
    who = f"{turn.speaker}, " if turn.speaker != "Unknown" else ""
    source = f" ({who}{turn.id}{when})" if cite else ""
    return f'{checks.cut_words(answer, ANSWER_MAX_WORDS)}\n\n> "{quote}"{source}'
