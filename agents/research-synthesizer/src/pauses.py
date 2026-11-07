"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how an
answer is read. Reading fails closed: an answer that isn't recognised is a Cancel.

Q1 over the cap (> 40 sources or > 120,000 chars): `Use the first 40 sources` / `Cancel`; custom text = which
   sources to keep, matched on their labels or ids.
Q2 no sources found (a bare question): "Paste the sources to synthesise" / `Cancel`; the answer is the paste.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import sources as source_parser
from inputs import current_message, word_count
from model import Source
from nasiko_hitl import question_metadata

METADATA_KEY = "research_synthesizer"
TOO_MANY, NEED_SOURCES = "too_many", "need_sources"
MAX_SOURCES = 40
MAX_TOTAL_CHARS = 120_000
MIN_SOURCE_WORDS = 40
CANCEL = "Cancel"
FIRST = f"Use the first {MAX_SOURCES} sources"
Q2_HEADER = "Paste the sources to synthesise"

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_YES = re.compile(r"^(?:yes|y|ok|okay|sure|go|go ahead|proceed|use the first|first)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    question: str = ""
    sources: tuple[dict, ...] = ()
    wants_json: bool = False
    has_links: bool = False
    attempts: int = 0

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "question": self.question, "sources": list(self.sources),
                               "wants_json": self.wants_json, "has_links": self.has_links, "attempts": self.attempts}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (TOO_MANY, NEED_SOURCES):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], question=str(data.get("question") or ""),
                       sources=tuple(dict(s) for s in data.get("sources") or () if isinstance(s, Mapping)),
                       wants_json=bool(data.get("wants_json")), has_links=bool(data.get("has_links")),
                       attempts=int(data.get("attempts") or 0))


@dataclass(frozen=True)
class Answer:
    action: str                       # run | cancel | reask | give_up
    question: str = ""
    sources: tuple[Source, ...] = ()
    has_links: bool = False
    pending: Pending | None = None


def over_cap(found: tuple[Source, ...]) -> bool:
    return len(found) > MAX_SOURCES or sum(len(s.text) for s in found) > MAX_TOTAL_CHARS


def fit(found: tuple[Source, ...]) -> tuple[Source, ...]:
    """The leading sources that fit both caps, renumbered from S1."""
    kept, total = [], 0
    for s in found:
        if len(kept) >= MAX_SOURCES or total + len(s.text) > MAX_TOTAL_CHARS:
            break
        kept.append(s)
        total += len(s.text)
    return source_parser.renumber(tuple(kept), 1)


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_SOURCES:
        text = ("Paste the sources you want synthesised (separate them with lines like `--- Source: name ---`) as "
                "your answer, or cancel.")
        if pending.attempts:
            text = "I still couldn't find any source text in that. " + text
        return text, question_metadata(Q2_HEADER, [(CANCEL, "")])
    count, chars = len(pending.sources), sum(len(str(s.get("text") or "")) for s in pending.sources)
    header = (f"That's {count} sources and {chars:,} characters, over my limit ({MAX_SOURCES} sources, "
              f"{MAX_TOTAL_CHARS:,} characters). What should I read?")
    text = header + " You can also type the names of the sources to keep."
    return text, question_metadata(header, [(FIRST, "Read the leading sources that fit the limit"), (CANCEL, "")])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if pending.kind == NEED_SOURCES:
        return _need_sources(pending, answer, plain)
    found = tuple(Source.from_dict(s) for s in pending.sources)
    if not plain or _CANCEL.match(plain):
        return Answer("cancel")
    if plain == FIRST.casefold() or _YES.match(plain):
        return Answer("run", pending.question, fit(found), pending.has_links)
    picked = tuple(s for s in found if _named(s, plain))
    return Answer("run", pending.question, fit(picked), pending.has_links) if picked else Answer("cancel")


def _named(source: Source, plain: str) -> bool:
    return bool(re.search(rf"\b{source.id.casefold()}\b", plain)) or source.label.casefold() in plain


def _need_sources(pending: Pending, answer: str, plain: str) -> Answer:
    parsed = source_parser.parse(answer)
    if parsed.sources and sum(s.words for s in parsed.sources) >= MIN_SOURCE_WORDS:
        return Answer("run", parsed.question or pending.question, parsed.sources, parsed.has_links)
    if _CANCEL.match(plain) and word_count(answer) <= 3:
        return Answer("cancel")
    if pending.attempts == 0:
        return Answer("reask", pending=replace(pending, attempts=1))
    return Answer("give_up")
