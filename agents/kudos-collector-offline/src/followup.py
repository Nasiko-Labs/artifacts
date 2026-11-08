"""Turns without new records. With a stored result, filters and views are read in code with no model call:
"only Priya Dev", "only PRs", "show the quotes", "which praise was too general", "unattributed records", "people
with fewer than 2 examples", "show all", "as json". Ranking requests are declined in code. Anything else gets one
scope call: off-topic -> decline; in scope -> what can be asked (or how to paste, when nothing is stored)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import llm
import prompts
import smalltalk
import turns
from inputs import Incoming
from llm import TurnDeps
from pipeline import Stored
from records import PR, PRAISE, SHIPPED
from turns import TurnResult, reply
from view import UNATTRIBUTED, VAGUE, View

STATUSES = ("in_scope", "ranking", "off_topic")
KIND_WORDS = {PR: r"prs?|pull requests?|code", PRAISE: r"praise|kudos|shout-?outs?|quotes?", SHIPPED:
              r"shipped|features?|launches"}
_KIND = re.compile(rf"\b(?:only|just)\s+(?:the\s+)?({'|'.join(KIND_WORDS.values())})\b", re.IGNORECASE)
_QUOTES = re.compile(r"\bshow\b.{0,12}\bquotes?\b", re.IGNORECASE)
_VAGUE = re.compile(r"\b(?:too general|vague|unspecific|not specific|generic)\b", re.IGNORECASE)
_UNATTRIBUTED = re.compile(r"\b(?:unattributed|no author|without (?:an )?author|not attributed)\b", re.IGNORECASE)
_FEWER = re.compile(r"\b(?:fewer|less) than (\d{1,2})\b", re.IGNORECASE)
_ALL = re.compile(r"\b(?:show|see|list) (?:me )?(?:all|everything|every example)\b|\ball examples\b", re.IGNORECASE)
_JSON_ONLY = re.compile(r"^\s*(?:as json|json only|in json|show (?:it )?as json)\s*[.!?]?\s*$", re.IGNORECASE)
_NAME = re.compile(r"^\s*(?:(?:only|just)(?: show)?|show(?: me)?(?: only)?|filter (?:to|by)|for)\s+"
                   r"(?P<names>[A-Za-z@][\w.'@ ,-]{0,80}?)(?:'s)?(?:\s+examples?)?\s*[.!?]*\s*$", re.IGNORECASE)
NOT_NAMES = re.compile(rf"^(?:{'|'.join(KIND_WORDS.values())}|all|everything|everyone|the quotes|more|json)$",
                       re.IGNORECASE)


def parse_scope(data: Any) -> dict | None:
    return {"status": data["status"]} if isinstance(data, Mapping) and data.get("status") in STATUSES else None


def view_for(text: str, base: View) -> View | None:
    """The view a follow-up asks for, or None when it isn't one of the code-read follow-ups."""
    if _JSON_ONLY.match(text):
        return base
    if _UNATTRIBUTED.search(text):
        return base.but(section=UNATTRIBUTED)
    if _VAGUE.search(text):
        return base.but(section=VAGUE)
    if _QUOTES.search(text):
        return base.but(kinds=(PRAISE,), show_quotes=True, show_all=True)
    if m := _FEWER.search(text):
        return base.but(fewer_than=int(m.group(1)), min_examples=1)
    if m := _KIND.search(text):
        word = m.group(1).casefold()
        kind = next(k for k, pattern in KIND_WORDS.items() if re.fullmatch(pattern, word))
        return base.but(kinds=(kind,))
    if _ALL.search(text):
        return base.but(show_all=True)
    if (m := _NAME.match(text)) and not NOT_NAMES.match(m["names"].strip()):
        names = tuple(n.strip() for n in re.split(r",|\band\b|&", m["names"]) if n.strip())
        return base.but(names=names, min_examples=1)
    return None


async def _scope(deps: TurnDeps, incoming: Incoming) -> str:
    try:
        answer = await llm.complete_json(deps, prompts.scope_messages(incoming.text, incoming.context), parse_scope)
    except llm.LlmError:
        return "unknown"
    return answer["status"]


async def respond(incoming: Incoming, deps: TurnDeps, stored: Stored | None) -> TurnResult:
    text = incoming.text.strip()
    if turns.RANKING.search(text):
        return reply(prompts.NO_RANKING)
    if stored is not None:
        wanted = view_for(text, stored.view)
        if wanted is not None:
            return turns.finish(stored, incoming, wanted)
    elif view_for(text, View()) is not None:
        return reply(prompts.PASTE_INSTRUCTIONS)
    if not deps.traceparent:
        return reply(prompts.NO_TRACE_CONTEXT)
    status = await _scope(deps, incoming)
    if status == "ranking":
        return reply(prompts.NO_RANKING)
    if status != "in_scope":
        return reply(smalltalk.OFF_TOPIC_REPLY)
    return reply(prompts.FOLLOWUP_HELP if stored is not None else prompts.PASTE_INSTRUCTIONS, incoming.notes)
