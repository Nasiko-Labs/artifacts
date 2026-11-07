"""Follow-ups on a stored thread: re-render commands parsed in code (zero LLM calls), or one checked
question about the thread (one call, plus one repair)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import checks
import chunking
import envelope
import grounding as g
import llm
import prompts
import smalltalk
from model import Untangled
from thread import Thread

ASK_STATUSES = ("ok", "not_in_input", "off_topic")
COMMAND_MAX_WORDS = 10
ANSWER_MAX_WORDS = 80
EXCERPT_CHARS = 200

_FILLER = re.compile(r"\b(?:please|pls|can you|could you|would you|show me|give me|just|only|me|now|again|the|"
                     r"list|show|with|include|including|and|in|it|as|a|all|of|them|those|what|are|were|"
                     r"in this thread|thread|items?|json)\b")
_SOURCES = re.compile(r"\b(?:sources?|quotes?|evidence|citations?|receipts)\b")
_VIEWS = (
    ("mine", re.compile(r"\b(?:my (?:items?|action items?|actions?|tasks?|todos?|stuff|work)|mine|i own|"
                        r"assigned to me|on me)\b")),
    ("owners", re.compile(r"\bwho (?:owns|is doing|does) what\b|\bwho's doing what\b|\bby owner\b|^owners?$")),
    ("reversals", re.compile(r"\b(?:reversals?|reversed|overridden|overturned|changed decisions?)\b")),
    ("questions", re.compile(r"\b(?:open questions?|questions?|unanswered)\b")),
    ("decisions", re.compile(r"\b(?:decisions?|decided)\b")),
    ("default", re.compile(r"^(?:everything|full|all|default|untangle|summary|tl ?;? ?dr|tldr)$")),
)
_QUESTION_START = re.compile(r"^(?:what|who|whom|whose|when|where|why|how|which|is|are|was|were|do|does|did|can|"
                             r"could|should|would|will|tell|write|translate|explain|give|calculate|compute|list|"
                             r"name|define|describe|compose|draft|generate|create|make)\b")
_UNTANGLE_WORDS = re.compile(r"\b(?:untangle|thread|chain|email|slack|summari[sz]e|tl ?;? ?dr|recap)\b")
_WORDS = re.compile(r"[^a-z0-9;' ]+")


@dataclass(frozen=True)
class Command:
    view: str = "default"
    sources: bool = False
    as_json: bool = False


def _plain(text: str) -> str:
    return " ".join(_WORDS.sub(" ", (text or "").casefold()).split())


def parse_command(text: str) -> Command | None:
    """A short re-render message ("only my items", "show sources", "as json"), or None."""
    plain = _plain(text)
    if not plain or len(plain.split()) > COMMAND_MAX_WORDS:
        return None
    as_json = envelope.wants_json(text)
    asks = bool(_QUESTION_START.match(plain)) and plain.split()[0] not in ("show", "list", "give")
    sources = bool(_SOURCES.search(plain)) and not asks
    core = " ".join(_SOURCES.sub(" ", _FILLER.sub(" ", plain)).split())
    for view, pattern in _VIEWS:
        target = plain if view in ("owners", "mine", "reversals") else core
        if not pattern.search(target):
            continue
        if asks and view not in ("reversals", "owners", "mine") and not pattern.fullmatch(core):
            continue  # "what was decided about pricing?" is a question, not a re-render
        return Command(view, sources, as_json)
    if (sources or as_json) and not core:
        return Command("default", sources, as_json)
    return None


def is_untangle_request(text: str) -> bool:
    return bool(_UNTANGLE_WORDS.search(_plain(text)))


def is_question_like(text: str) -> bool:
    return (text or "").strip().endswith("?") or bool(_QUESTION_START.match(_plain(text)))


async def ask(deps: llm.TurnDeps, question: str, thread: Thread, result: Untangled,
              context: Sequence[str] = ()) -> str:
    """One checked answer from the stored thread (its messages, or its checked items when it is long)."""
    try:
        parsed = await llm.complete_json(deps, prompts.ask_messages(material(thread, result), question, context),
                                         prompts.ASK_CONTRACT, parse_answer, llm.RepairBudget(1))
    except llm.LlmError:
        return prompts.MODEL_FAILED
    if parsed["status"] == "off_topic":
        return smalltalk.OFF_TOPIC_REPLY
    return answer_text(parsed, thread)


async def scope_check(deps: llm.TurnDeps, text: str, context: Sequence[str] = ()) -> str:
    """No thread stored yet: one call decides off-topic (decline) or not (ask for the thread)."""
    try:
        parsed = await llm.complete_json(deps, prompts.scope_messages(text, context), prompts.ASK_CONTRACT,
                                         parse_answer, llm.RepairBudget(1))
    except llm.LlmError:
        return prompts.MODEL_FAILED
    return smalltalk.OFF_TOPIC_REPLY if parsed["status"] == "off_topic" else smalltalk.NO_THREAD_YET


def material(thread: Thread, result: Untangled) -> str:
    lines = chunking.block(thread.content)
    if len(lines) <= chunking.SINGLE_PASS_MAX_CHARS:
        return lines
    records = [*(f'[{d.message_id}] {d.speaker} decided: "{d.quote}"' for d in result.decisions),
               *(f'[{q.message_id}] {q.asked_by} asked: "{q.quote}"' for q in result.questions),
               *(f'[{a.message_id}] {a.owner} to do: "{a.quote}"' for a in result.actions)]
    return "\n".join(records)


def parse_answer(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ASK_STATUSES:
        return None
    return {"status": data["status"], "answer": str(data.get("answer") or ""),
            "message_id": str(data.get("message_id") or "")}


def answer_text(parsed: Mapping[str, str], thread: Thread) -> str:
    """`ok` stands only when its message exists and the answer adds no number, date or name of its own."""
    full = "\n".join(m.text for m in thread.messages)
    answer = checks.clean_text(parsed["answer"], full)
    message = thread.get(parsed["message_id"])
    if parsed["status"] != "ok" or not answer or message is None or message.noise:
        return prompts.NOT_IN_INPUT
    if not g.is_grounded(answer, g.normalise(full)):
        return prompts.NOT_IN_INPUT
    excerpt = " ".join(message.text.split())
    excerpt = excerpt if len(excerpt) <= EXCERPT_CHARS else excerpt[:EXCERPT_CHARS].rstrip() + "…"
    return (f"{checks.cut_words(answer, ANSWER_MAX_WORDS)}\n\n"
            f'> "{checks.clean_text(excerpt, excerpt)}" ({message.id}, {message.speaker})')
