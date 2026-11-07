"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                              -> canned intro (no call)
    sources found ─┬─ over 40 sources / 120k chars      -> pause Q1
                   ├─ a brief is stored + "add this source" -> add: extract the new ones, re-run from cluster
                   └─ otherwise                         -> synthesize
    no sources, brief stored ─┬─ view command           -> re-render (no call)
                              └─ anything else          -> one quote-checked question
    no sources, no brief                                -> one scope call (decline, or pause Q2)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pauses
import prompts
import smalltalk
import sources as source_parser
from followup import parse_command
from inputs import Incoming
from model import Source

MIN_MARKED_WORDS = 20      # explicitly marked sources
MIN_SOURCE_WORDS = 40      # unmarked text

_ADD = re.compile(r"\badd\b|\balso\b|\banother source\b|\bmore sources?\b|\bnew sources?\b|\bone more\b", re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    route: str                                  # reply | pause | synth | add | view | ask | scope
    reply: str = ""
    pending: pauses.Pending | None = None
    question: str = ""
    sources: tuple[Source, ...] = ()
    has_links: bool = False
    view: str = "default"


def plan_turn(incoming: Incoming, stored: dict | None, next_source: int = 1) -> Plan:
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned)
    parsed = source_parser.parse(incoming.text, incoming.files)
    if has_sources(parsed):
        return _with_sources(parsed, incoming, stored, next_source)
    if stored:
        view = parse_command(incoming.text)
        return Plan("view", view=view) if view is not None else Plan("ask")
    return Plan("scope")


def has_sources(parsed: source_parser.Parsed) -> bool:
    words = sum(s.words for s in parsed.sources)
    return bool(parsed.sources) and words >= (MIN_MARKED_WORDS if parsed.has_headers else MIN_SOURCE_WORDS)


def _with_sources(parsed: source_parser.Parsed, incoming: Incoming, stored: dict | None, next_source: int) -> Plan:
    if pauses.over_cap(parsed.sources):
        pending = pauses.Pending(pauses.TOO_MANY, question=parsed.question,
                                 sources=tuple(s.to_dict() for s in parsed.sources), wants_json=incoming.wants_json,
                                 has_links=parsed.has_links)
        return Plan("pause", pending=pending)
    if stored and _ADD.search(parsed.question):
        renumbered = source_parser.renumber(parsed.sources, next_source)
        return Plan("add", question=stored.get("question") or "", sources=renumbered, has_links=parsed.has_links)
    return Plan("synth", question=parsed.question, sources=parsed.sources, has_links=parsed.has_links)


def plan_resume(answer: pauses.Answer) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "give_up":
        return Plan("reply", prompts.NO_SOURCES_GIVEN_UP)
    if answer.action == "reask":
        return Plan("pause", pending=answer.pending)
    return Plan("synth", question=answer.question, sources=answer.sources, has_links=answer.has_links)
