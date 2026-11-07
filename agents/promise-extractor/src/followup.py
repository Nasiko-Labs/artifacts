"""Follow-ups on the stored list: re-render commands parsed in code (zero LLM calls: "as of 2026-12-01",
"only overdue", "only ones to Raj", "show sources", "as json"), or one quote-checked question (one call,
plus at most one repair)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import checks
import dates
import grounding as g
import llm
import prompts
import smalltalk
from render import View

ASK_STATUSES = ("ok", "not_in_input", "off_topic")
COMMAND_MAX_WORDS = 14
ANSWER_MAX_WORDS = 80

_QUESTION_ONLY = re.compile(r"^(?:why|how|did|does|do|is|was|were|when|where|who|whom|can|could|should|would)\b")
_OVERDUE = re.compile(r"\b(?:overdue|late|past due|missed)\b")
_SOURCES = re.compile(r"\b(?:sources?|quotes?|evidence|citations?)\b")
_JSON = re.compile(r"\b(?:as|in) json\b|\bjson only\b|^json$")
_LIST = re.compile(r"\b(?:list|show|table|all|again|promises|commitments|refresh|everything)\b")
_TO = re.compile(r"\b(?:to|for|with)\s+([A-Za-z][\w.@-]*)")
_QUESTION_START = re.compile(r"^(?:what|who|whom|whose|when|where|why|how|which|is|are|was|were|do|does|did|can|"
                             r"could|should|would|will|tell|write|translate|explain|give|calculate|compute|"
                             r"name|define|describe|compose|draft|generate|create|make)\b")
_REQUEST_WORDS = re.compile(r"\b(?:promis\w*|commit\w*|owe|track|scan|extract|what did i)\b")


@dataclass(frozen=True)
class Command:
    view: View = field(default_factory=View)
    as_of: date | None = None
    as_json: bool = False


def _plain(text: str) -> str:
    return " ".join((text or "").casefold().split())


def parse_command(text: str, recipients: Sequence[str], today: date) -> Command | None:
    plain = _plain(text)
    if not plain or len(plain.split()) > COMMAND_MAX_WORDS or _QUESTION_ONLY.match(plain):
        return None
    as_of = dates.parse_as_of(text, today)
    to = _recipient(text, recipients)
    view = View(overdue_only=bool(_OVERDUE.search(plain)), to=to, sources=bool(_SOURCES.search(plain)))
    as_json = bool(_JSON.search(plain))
    if as_of or to or as_json or view.overdue_only or view.sources or _LIST.search(plain):
        return Command(view, as_of, as_json)
    return None


def _recipient(text: str, recipients: Sequence[str]) -> str:
    for match in _TO.finditer(text or ""):
        word = match.group(1).casefold()
        for name in recipients:
            if name and name.casefold().split()[0].startswith(word) and len(word) >= 2:
                return name
    return ""


def is_scan_request(text: str) -> bool:
    return bool(_REQUEST_WORDS.search(_plain(text)))


def is_question_like(text: str) -> bool:
    return (text or "").strip().endswith("?") or bool(_QUESTION_START.match(_plain(text)))


async def ask(deps: llm.TurnDeps, question: str, listing: str, source: str, context: Sequence[str] = ()) -> str:
    """One answer from the stored list and text; with nothing stored it only classifies scope."""
    try:
        parsed = await llm.complete_json(deps, prompts.ask_messages(listing or "(no list yet)", source or
                                                                    "(no text has been pasted yet)",
                                                                    question, context),
                                         prompts.ASK_CONTRACT, parse_answer)
    except llm.LlmError:
        return prompts.MODEL_FAILED
    if parsed["status"] == "off_topic":
        return smalltalk.OFF_TOPIC_REPLY
    if not source:
        return smalltalk.NO_TEXT_YET
    return answer_text(parsed, source, listing)


def parse_answer(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ASK_STATUSES:
        return None
    return {"status": data["status"], "answer": str(data.get("answer") or ""), "snippet": str(data.get("snippet") or "")}


def answer_text(parsed: Mapping[str, str], source: str, listing: str) -> str:
    """`ok` stands only when its quote is in the text or list and it adds no number or date of its own."""
    norm = g.normalise(f"{source}\n{listing}")
    answer = checks.clean_text(parsed["answer"], source)
    snippet = " ".join(parsed["snippet"].split())
    if parsed["status"] != "ok" or not answer or g.locate(snippet, norm) is None or g.ungrounded_tokens(answer, norm):
        return prompts.NOT_IN_INPUT
    return f'{checks.cut_words(answer, ANSWER_MAX_WORDS)}\n\n> "{checks.clean_text(snippet[:250])}"'
