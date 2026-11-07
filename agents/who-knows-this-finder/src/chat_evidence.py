"""Chat evidence, checked in code: a reply counts as an `answer` only when

1. its thread's question (the root message) contains at least 2 of the topic's terms,
2. it was written by someone other than the asker,
3. it was acknowledged: a positive reaction, a thanks-like message from the asker right after it (before
   anyone else replies), or it is the last reply of the thread,
4. the judge (LLM, at most 2 batches) says it answers the question AND returns a quote that is copied
   word for word from that reply.

Every message text, name and date must appear in this step's tool output."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import keywords
import prompts
from code_evidence import backed_who
from deps import TurnDeps
from models import Evidence, Who
from reader import BudgetExceeded, TurnBudget, ask_once
from verify import (
    appears_in,
    normalise_text,
    parse_day,
    parse_object,
    safe_link,
    text_of,
)

log = logging.getLogger("who_knows.chat")

MIN_ROOT_TERMS = 2
JUDGE_BATCH_THREADS = 6
MAX_JUDGE_BATCHES = 2
QUESTION_CHARS = 80
_THANKS = re.compile(r"\b(?:thanks|thank you|thx|ty|cheers|got it|makes sense|perfect|that helps|that worked|"
                     r"helpful|appreciate)\b", re.IGNORECASE)
POSITIVE_REACTIONS = frozenset({"+1", "thumbsup", "thumbs_up", "white_check_mark", "heavy_check_mark", "heart",
                                "tada", "raised_hands", "pray", "100", "clap", "star", "ok_hand",
                                "\U0001f44d", "✅", "\U0001f64f", "❤️", "\U0001f389", "\U0001f64c"})


@dataclass(frozen=True)
class Message:
    who: Who
    text: str
    raw_date: str
    reactions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
    """A reply that passed rules 1-3, waiting for the judge."""

    thread: int
    reply: int
    question: str
    channel: str
    link: str
    message: Message


@dataclass(frozen=True)
class Read:
    evidence: tuple[Evidence, ...]
    dropped: int = 0
    me: Who | None = None
    judged: bool = True


def candidates(data: Mapping[str, Any], seen_text: str, terms: Sequence[str]) -> tuple[tuple[Candidate, ...], int]:
    """Replies that may be answers, and how many items failed the verbatim check."""
    seen = normalise_text(seen_text)
    threads = [t for t in data.get("threads") or () if isinstance(t, Mapping)] if isinstance(
        data.get("threads"), list) else []
    found: list[Candidate] = []
    dropped = 0
    for index, thread in enumerate(threads):
        root = _message(thread.get("root"), seen)
        if root is None:
            dropped += 1
            continue
        if keywords.hits(root.text, terms) < MIN_ROOT_TERMS:
            continue
        replies = [_message(r, seen) for r in thread.get("replies") or () if isinstance(r, Mapping)] if isinstance(
            thread.get("replies"), list) else []
        dropped += sum(1 for r in replies if r is None)
        kept = [r for r in replies if r is not None]
        channel = text_of(thread.get("channel")).lstrip("#")
        link = safe_link(text_of(thread.get("link")), seen)
        question = _question(root.text)
        found.extend(Candidate(index, j, question, channel if appears_in(channel, seen) else "", link, reply)
                     for j, reply in enumerate(kept) if _acknowledged(kept, j, root.who)
                     and not same_person(reply.who, root.who))
    return tuple(found), dropped


def _message(raw: Any, seen: str) -> Message | None:
    if not isinstance(raw, Mapping):
        return None
    text = text_of(raw.get("text"))
    who = backed_who(Who(text_of(raw.get("name")), text_of(raw.get("email")), text_of(raw.get("handle"))), seen)
    raw_date = text_of(raw.get("date"))
    if who is None or not text or not appears_in(text, seen) or parse_day(raw_date) is None:
        return None
    if not appears_in(raw_date, seen):
        return None
    reactions = tuple(text_of(r).strip(":").casefold() for r in raw.get("reactions") or () if text_of(r)) \
        if isinstance(raw.get("reactions"), list) else ()
    return Message(who, text, raw_date, reactions)


def _acknowledged(replies: Sequence[Message], index: int, asker: Who) -> bool:
    reply = replies[index]
    if any(r in POSITIVE_REACTIONS for r in reply.reactions):
        return True
    if index == len(replies) - 1:
        return True
    following = replies[index + 1]
    return same_person(following.who, asker) and bool(_THANKS.search(following.text))


def same_person(a: Who, b: Who) -> bool:
    pairs = ((a.handle, b.handle), (a.email, b.email), (a.name, b.name))
    return any(x and y and x.casefold() == y.casefold() for x, y in pairs)


def _question(text: str) -> str:
    """The thread's question as shown: its first 80 characters, verbatim (cut at a word)."""
    flat = " ".join(text.split())
    if len(flat) <= QUESTION_CHARS:
        return flat
    return flat[:QUESTION_CHARS].rsplit(" ", 1)[0] + "..."


async def judge(deps: TurnDeps, budget: TurnBudget, found: Sequence[Candidate]) -> Read:
    """At most 2 judge calls of up to 6 threads each; an answer needs a verbatim quote from the reply."""
    by_thread: dict[int, list[Candidate]] = {}
    for candidate in found:
        by_thread.setdefault(candidate.thread, []).append(candidate)
    groups = list(by_thread.values())
    batches = [groups[i:i + JUDGE_BATCH_THREADS] for i in range(0, len(groups), JUDGE_BATCH_THREADS)]
    evidence: list[Evidence] = []
    dropped = 0
    judged = len(batches) <= MAX_JUDGE_BATCHES
    for batch in batches[:MAX_JUDGE_BATCHES]:
        verdicts = await _ask(deps, budget, batch)
        if verdicts is None:
            judged = False
            continue
        kept, failed = _answers(batch, verdicts)
        evidence.extend(kept)
        dropped += failed
    return Read(tuple(evidence), dropped, judged=judged)


async def _ask(deps: TurnDeps, budget: TurnBudget, batch: Sequence[Sequence[Candidate]]) -> dict | None:
    listing = [{"thread": group[0].thread, "question": group[0].question,
                "replies": [{"reply": c.reply, "author": c.message.who.label(), "text": c.message.text}
                            for c in group]} for group in batch]
    messages = ({"role": "system", "content": prompts.JUDGE_SYSTEM},
                {"role": "user", "content": prompts.fence_threads(json.dumps(listing, ensure_ascii=False))})
    try:
        return parse_object(await ask_once(deps, messages, budget))
    except BudgetExceeded:
        log.info("no LLM budget left for the chat judge")
        return None


def _answers(batch: Sequence[Sequence[Candidate]], verdicts: Mapping[str, Any]) -> tuple[list[Evidence], int]:
    rows = [v for v in verdicts.get("answers") or () if isinstance(v, Mapping)] if isinstance(
        verdicts.get("answers"), list) else []
    lookup = {(c.thread, c.reply): c for group in batch for c in group}
    kept: list[Evidence] = []
    dropped = 0
    for row in rows:
        candidate = lookup.pop((_int(row.get("thread")), _int(row.get("reply"))), None)
        if candidate is None or row.get("answers") is not True:
            continue
        quote = text_of(row.get("quote"))
        if not appears_in(quote, normalise_text(candidate.message.text)):
            dropped += 1  # the judge's quote is not in the reply: not an answer
            continue
        day = parse_day(candidate.message.raw_date)
        kept.append(Evidence("answer", candidate.message.who, candidate.question, day, candidate.link,
                             channel=candidate.channel, quote=quote))
    return kept, dropped


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1
