"""Follow-ups on the stored prep, parsed in code, and the one-call question about the notes.

    "only the questions", "recognize only", "show sources", "as json"   -> re-render, zero calls
    "add topic: promotion path", "drop 3"                               -> change the prep, zero calls
    "what did we agree about on-call?"                                  -> one call, quote-checked
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from grounding import clean_quote, in_source
from judgement import has_judgement
from llm import LlmError, TurnDeps, complete_json

log = logging.getLogger("one-on-one-prep.followup")

OK, NOT_IN_NOTES, OFF_TOPIC, NEEDS_INPUT, FAILED = "ok", "not_in_notes", "off_topic", "needs_input", "failed"
MAX_TOPIC_CHARS = 200

_QUESTIONS = re.compile(r"^\W*(?:(?:only|just|show)(?: me)?(?: the)? questions|questions only)\W*$", re.IGNORECASE)
_RECOGNIZE = re.compile(r"^\W*(?:(?:only|just|show)(?: me)?(?: the)? recogni[sz]\w*|recogni[sz]\w*(?: only)?)\W*$",
                        re.IGNORECASE)
_SOURCES = re.compile(r"\b(?:show|with|list)(?: me)?(?: the)? (?:sources|quotes)\b", re.IGNORECASE)
_JSON = re.compile(r"^\W*(?:(?:show|give|return|send)(?: me)?(?: it| this| that| the result)? )?"
                   r"(?:as|in) json\W*$|^\W*json(?: only)?\W*$", re.IGNORECASE)
_TOPIC = re.compile(r"^\W*add (?:a )?(?:topic|talking point|agenda item)\s*[:\-]?\s*(.+)$", re.IGNORECASE)
_DROP = re.compile(r"^\W*(?:drop|remove|delete)(?: talking point| point| item| number| no\.?| #)?\s*#?(\d{1,2})\W*$",
                   re.IGNORECASE)
_ALL = re.compile(r"^\W*(?:show )?(?:all|everything|the full prep|full prep|show the prep)\W*$", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str           # questions | recognize | sources | json | topic | drop | all
    number: int = 0
    text: str = ""


def parse(text: str) -> Command | None:
    message = (text or "").strip()
    topic = _TOPIC.match(message)
    if topic:
        return Command("topic", text=" ".join(topic.group(1).split())[:MAX_TOPIC_CHARS])
    drop = _DROP.match(message)
    if drop:
        return Command("drop", number=int(drop.group(1)))
    for rx, kind in ((_JSON, "json"), (_QUESTIONS, "questions"), (_RECOGNIZE, "recognize"), (_ALL, "all")):
        if rx.match(message):
            return Command(kind)
    if _SOURCES.search(message):
        return Command("sources")
    return None


def _validate_ask(data: Any) -> dict | None:
    ok = isinstance(data, dict) and data.get("status") in (OK, NOT_IN_NOTES, OFF_TOPIC)
    return data if ok else None


async def ask(deps: TurnDeps, question: str, notes: str, updates: str,
              policy: Sequence[str] = ()) -> tuple[str, str, str]:
    """(status, answer, quote): `ok` only with a quote found verbatim in the notes or updates."""
    try:
        data = await complete_json(deps, prompts.ask_messages(question, notes, updates, policy), prompts.ASK_CONTRACT,
                                   _validate_ask)
    except LlmError as e:
        log.warning("ask failed: %s", e)
        return FAILED, "", ""
    if data["status"] != OK:
        return data["status"], "", ""
    quote = clean_quote(str(data.get("snippet") or ""))
    if not (in_source(quote, notes) or in_source(quote, updates)) or has_judgement(quote):
        return NOT_IN_NOTES, "", ""
    answer = " ".join(str(data.get("answer") or "").split())
    return OK, ("" if has_judgement(answer) else answer), quote


async def classify(deps: TurnDeps, text: str, policy: Sequence[str] = ()) -> str:
    """A short message with nothing stored: off topic, or a request that still needs the notes. A failed call
    counts as needs_input (asking for the notes never answers an off-topic question)."""
    def validate(data: Any) -> dict | None:
        ok = isinstance(data, dict) and data.get("status") in (OFF_TOPIC, NEEDS_INPUT)
        return data if ok else None
    try:
        data = await complete_json(deps, prompts.scope_messages(text, policy), prompts.SCOPE_CONTRACT, validate)
    except LlmError as e:
        log.warning("scope check failed: %s", e)
        return NEEDS_INPUT
    return data["status"]
