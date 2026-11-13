"""Follow-ups on earlier work recalled from the chat history (nothing is stored): commands parsed in code,
views cut from our last reply when the thread itself was trimmed (zero calls), or one quote-checked question
about the recalled thread or, failing that, our last reply (one call, plus one JSON repair)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import checks
import facts as fx
import grounding as g
import llm
import prompts
import smalltalk

ASK_STATUSES = ("ok", "not_in_thread", "off_topic")
COMMAND_MAX_WORDS = 10
ANSWER_MAX_WORDS = 80
ASK_MATERIAL_MAX_CHARS = 24_000

_VIEWS = (
    ("json", re.compile(r"^(?:(?:show |give |return )?(?:it |that |the result )?(?:as |in )?json(?: only)?)$")),
    ("sources", re.compile(r"\b(?:sources?|quotes?|evidence|citations?|receipts)\b|^who said (?:that|this|it)$")),
    ("ask", re.compile(r"^(?:(?:only|just) )?(?:the )?ask(?: only)?$|^what(?: is|s| s)? the ask$|"
                       r"^what do they (?:need|want) from (?:me|us)$|^what(?: is|s| s)? being asked$")),
    ("impact", re.compile(r"^(?:(?:only|just) )?(?:the )?(?:customer )?impact(?: only)?$|"
                          r"^what(?: is|s| s)? the (?:customer )?impact$")),
    ("default", re.compile(r"^(?:summary|the summary|show (?:the )?summary(?: again)?|again|full summary)$")),
)
_WHO_SAID = re.compile(r"^who (?:said|wrote|set|mentioned|asked for|raised|gave) (?:the |that |this )?"
                       r"(?P<what>deadline|due date|ask|request|impact|status|renewal|problem|issue|blocker)\b")
_WHAT_KINDS = {"deadline": ("deadline",), "due date": ("deadline",), "renewal": ("deadline", "impact"),
               "ask": ("ask",), "request": ("ask",), "impact": ("impact",), "status": ("status",),
               "problem": ("problem",), "issue": ("problem",), "blocker": ("blocker",)}
_SUMMARIZE_WORDS = re.compile(r"\b(?:summari[sz]e|summary|escalat\w*|tl ?;? ?dr|recap|brief me)\b")
_QUESTION_START = re.compile(r"^(?:what|who|whom|whose|when|where|why|how|which|is|are|was|were|do|does|did|has|"
                             r"have|tell|explain|list|name|calculate|compute|define|describe|translate|write)\b")
_WH = re.compile(r"^(?:what|who|whom|whose|when|where|why|how|which|is|are|was|were|did|does|has|have)\b")
_WORDS = re.compile(r"[^a-z0-9;' ]+")
_REPLY_LINES = {"ask": "**Ask:**", "impact": "**Customer impact:**"}


def plain(text: str) -> str:
    return " ".join(_WORDS.sub(" ", (text or "").casefold()).replace("'", "").split())


def parse_view(text: str) -> str | None:
    words = plain(re.sub(r"^(?:please|pls|ok|okay|now|can you|could you|show me|give me|just)\s+", "",
                         (text or "").strip().casefold()))
    if not words or len(words.split()) > COMMAND_MAX_WORDS:
        return None
    words = re.sub(r"^(?:show|give|list) (?:me )?", "", words)
    for view, pattern in _VIEWS:
        if pattern.search(words):
            return view
    return None


def who_said(text: str) -> tuple[str, ...] | None:
    match = _WHO_SAID.match(plain(text))
    return _WHAT_KINDS[match.group("what")] if match else None


def who_said_reply(kinds: Sequence[str], facts: Sequence[fx.Fact]) -> str:
    found = fx.of_kind(facts, *kinds)
    if not found:
        return prompts.NOT_IN_THREAD
    lines = [f'- {f.speaker or "Unknown speaker"}' + (f" ({f.message})" if f.message else "")
             + f': "{" ".join(f.snippet.split())}"' for f in found]
    return "\n".join(lines)


def is_summarize_request(text: str) -> bool:
    return bool(_SUMMARIZE_WORDS.search(plain(text)))


def is_wh_question(text: str) -> bool:
    return bool(_WH.match(plain(text)))


def is_question_like(text: str) -> bool:
    return (text or "").strip().endswith("?") or bool(_QUESTION_START.match(plain(text)))


async def ask(deps: llm.TurnDeps, question: str, thread: str, context: Sequence[str] = (),
              had_history: bool = False) -> str:
    """One quote-checked answer from the recalled material (the most recent part when it is long)."""
    material = thread[-ASK_MATERIAL_MAX_CHARS:] if thread else "(no thread has been pasted yet)"
    try:
        parsed = await llm.complete_json(deps, prompts.ask_messages("THREAD", material, question, context),
                                         prompts.ASK_CONTRACT, parse_answer)
    except llm.LlmError:
        return prompts.MODEL_FAILED
    if parsed["status"] == "off_topic":
        return smalltalk.OFF_TOPIC_REPLY
    if not thread:
        return prompts.NEED_REPASTE if had_history else smalltalk.NO_THREAD_YET
    return answer_text(parsed, thread)


def from_reply(reply: str, view: str) -> str | None:
    """A view cut from our last rendered reply (zero calls), or None when that reply can't give it."""
    if view == "default":
        return reply or None
    lead = _REPLY_LINES.get(view)
    if not lead:
        return None
    for line in (reply or "").splitlines():
        if line.startswith(lead):
            return line
    return None


def parse_answer(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ASK_STATUSES:
        return None
    return {"status": data["status"], "answer": str(data.get("answer") or ""), "snippet": str(data.get("snippet") or "")}


def answer_text(parsed: Mapping[str, str], thread: str) -> str:
    """`ok` stands only when its quote is in the thread and the answer adds no number, date or name of its
    own, and assigns no blame."""
    norm = g.normalise(thread)
    answer = fx.clean(parsed["answer"])
    snippet = " ".join(parsed["snippet"].split())
    if parsed["status"] != "ok" or not answer or g.locate(snippet, norm) is None or checks.issues(answer, norm):
        return prompts.NOT_IN_THREAD
    quote = fx.clean(snippet if len(snippet) <= 200 else snippet[:200] + "…")
    return f'{checks.cap_words(answer, ANSWER_MAX_WORDS)}\n\n> "{quote}"'
