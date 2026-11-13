"""Follow-ups: re-render commands parsed in code (zero LLM calls), or one quote-checked question about a
text still in the conversation history, or an earlier summary reply (one call, plus one repair). Nothing is
stored between messages."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import checks
import grounding as g
import llm
import prompts
import smalltalk

ASK_STATUSES = ("ok", "not_in_input", "off_topic")
COMMAND_MAX_WORDS = 10
NO_TEXT_MATERIAL = "(no text has been provided yet)"

_FILLER = re.compile(r"\b(?:please|pls|can you|could you|would you|show me|give me|just|only|me|now|again|the|"
                     r"list|show|what (?:are|were)|with|include|including|and|in|into|it|as|a)\b")
_COMMANDS = (
    ("sources", re.compile(r"\b(?:sources?|quotes?|evidence|citations?|receipts)\b")),
    ("owners", re.compile(r"\bwho (?:owns|is doing|does) what\b|\bwho's doing what\b|\bby owner\b|^owners?$")),
    ("table", re.compile(r"^(?:table|tabular|tabular form)$")),
    ("actions", re.compile(r"^(?:action items?|actions?|todos?|to-?dos|tasks|next steps)(?: only)?$")),
    ("decisions", re.compile(r"^(?:decisions?|what was decided|decided)(?: only)?$")),
    ("tldr", re.compile(r"^(?:shorter|tl ?;? ?dr|tldr|brief(?:er)?|short(?:er)? version|summary only)(?: only)?$")),
)
_SUMMARIZE_WORDS = re.compile(r"\b(?:summari[sz]e|summary|tl ?;? ?dr|recap|key points|action items|minutes|"
                              r"digest|sum up|boil (?:it|this) down)\b")
_QUESTION_START = re.compile(r"^(?:what|who|whom|whose|when|where|why|how|which|is|are|was|were|do|does|did|can|"
                             r"could|should|would|will|tell|write|translate|explain|give|calculate|compute|list|"
                             r"name|define|describe|compose|draft|generate|create|make)\b")
_WORDS = re.compile(r"[^a-z0-9;' ]+")


def _plain(text: str) -> str:
    return " ".join(_WORDS.sub(" ", (text or "").casefold()).split())


def parse_command(text: str) -> tuple[str, bool] | None:
    """(view, with_sources) for a short re-render message, or None. "show sources" alone is the full
    summary with quotes; "only the action items, with sources" is one section with quotes."""
    plain = _plain(text)
    if not plain or len(plain.split()) > COMMAND_MAX_WORDS:
        return None
    sources = bool(_COMMANDS[0][1].search(plain)) and not _QUESTION_START.match(plain)
    core = " ".join(_COMMANDS[0][1].sub(" ", _FILLER.sub(" ", plain)).split())
    for view, pattern in _COMMANDS[1:]:
        if pattern.search(plain if view == "owners" else core):
            return view, sources
    return ("default", True) if sources and not core else None


def is_summarize_request(text: str) -> bool:
    return bool(_SUMMARIZE_WORDS.search(_plain(text)))


def is_question_like(text: str) -> bool:
    plain = _plain(text)
    return (text or "").strip().endswith("?") or bool(_QUESTION_START.match(plain))


async def ask(deps: llm.TurnDeps, question: str, source: str, context: Sequence[str] = ()) -> str:
    """One quote-checked answer from `source`: a text from the history, or an earlier summary reply."""
    name, material = "SOURCE_TEXT", source or NO_TEXT_MATERIAL
    try:
        parsed = await llm.complete_json(deps, prompts.ask_messages(name, material, question, context),
                                         prompts.ASK_CONTRACT, parse_answer)
    except llm.LlmError:
        return prompts.MODEL_FAILED
    if parsed["status"] == "off_topic":
        return smalltalk.OFF_TOPIC_REPLY
    if not source:
        return smalltalk.NO_TEXT_YET
    return answer_text(parsed, source)


def parse_answer(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ASK_STATUSES:
        return None
    return {"status": data["status"], "answer": str(data.get("answer") or ""), "snippet": str(data.get("snippet") or "")}


def answer_text(parsed: Mapping[str, str], source: str) -> str:
    """`ok` stands only when its quote is in the source and the answer adds no number or date of its own."""
    norm = g.normalise(source)
    answer = checks.clean_text(parsed["answer"], source)
    snippet = " ".join(parsed["snippet"].split())
    if parsed["status"] != "ok" or not answer or g.locate(snippet, norm) is None or g.ungrounded_tokens(answer, norm):
        return prompts.NOT_IN_INPUT
    quote = checks.clean_text(checks.cap_snippet(snippet), snippet)
    return f'{checks.cut_words(answer, 80)}\n\n> "{quote}"'
