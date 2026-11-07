"""Unknowns, rendered as questions and never as guesses:
1. sub-questions of the research question (one model call) that no finding answers: a sub-question is answered
   when some finding's statement shares >= ANSWERED_MIN_WORDS content words with it;
2. gaps found in code: single-source findings, findings whose newest source date is over STALE_MONTHS old
   (dated sources only), every source of one type, question words no claim or source mentions;
3. conflicts left unresolved; plus topics a follow-up found the sources don't cover."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import grounding as g
import llm
import prompts
from model import Claim, Finding, Source
from sources import months_between

log = logging.getLogger("research-synthesizer.unknowns")

MAX_SUB_QUESTIONS = 8
ANSWERED_MIN_WORDS = 2
STALE_MONTHS = 24
QUESTION_MAX_CHARS = 200
# Question words that frame the question rather than name its subject ("what drives ...", "how does X affect").
FRAMING_WORDS = frozenset({
    "drive", "cause", "affect", "impact", "make", "matter", "change", "lead", "help", "work", "need", "want",
    "think", "happen", "influence", "explain", "reason", "factor", "main", "biggest", "key",
})
TYPE_GAPS = {"interview": "only interviews: no quantitative data", "forum": "only forum posts: no first-hand data",
             "internal": "only internal documents: no outside view"}


async def sub_questions(deps: llm.TurnDeps, question: str, context: Sequence[str] = ()) -> tuple[str, ...]:
    if not question.strip():
        return ()
    body = "List the sub-questions the research question implies."
    msgs = prompts.messages("UNKNOWNS", body, prompts.UNKNOWNS_CONTRACT, [prompts.question_block(question)], context)
    try:
        parsed = await llm.complete_json(deps, msgs, prompts.UNKNOWNS_CONTRACT, parse)
    except llm.LlmError:
        log.warning("unknowns call failed; code gaps only")
        return ()
    return tuple(parsed["sub_questions"])


def parse(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or not isinstance(data.get("sub_questions"), list):
        return None
    cleaned = [_as_question(prompts.strip_markers(str(q))) for q in data["sub_questions"] if str(q).strip()]
    return {"sub_questions": [q for q in cleaned if q][:MAX_SUB_QUESTIONS]}


def _as_question(text: str) -> str:
    plain = " ".join(text.split())[:QUESTION_MAX_CHARS].rstrip(" .")
    return plain if not plain or plain.endswith("?") else plain + "?"


def unanswered(subs: Sequence[str], findings: Sequence[Finding]) -> list[str]:
    answered = [g.content_words(f.statement) for f in findings]
    return [q for q in subs if not any(len(g.content_words(q) & words) >= ANSWERED_MIN_WORDS for words in answered)]


def code_gaps(question: str, findings: Sequence[Finding], claims: Sequence[Claim], sources: Sequence[Source],
              today: date) -> list[str]:
    by_id = {s.id: s for s in sources}
    gaps = []
    for f in findings:
        if f.agreement == "conflict":
            a, b = f.conflict["a"], f.conflict["b"]
            owners = {c.id: c.source_id for c in claims}
            gaps.append(f"Which is right on \"{f.statement}\": {owners.get(a, '?')} or {owners.get(b, '?')}? "
                        "(unresolved conflict)")
        elif len(f.source_ids) == 1:
            gaps.append(f"Does any other source confirm that {_lower(f.statement)}? (only {f.source_ids[0]} says so)")
        newest = max((by_id[s].date for s in f.source_ids if s in by_id and by_id[s].date), default="")
        if newest and months_between(newest, today) > STALE_MONTHS:
            gaps.append(f"Is it still true that {_lower(f.statement)}? (newest source dated {newest}, over "
                        f"{STALE_MONTHS} months ago)")
    types = {s.type for s in sources}
    if len(sources) > 1 and len(types) == 1 and "" not in types:
        kind = types.pop()
        gaps.append(f"What would other kinds of source show? ({TYPE_GAPS.get(kind, f'only {kind} sources')})")
    missing = _missing_question_words(question, claims, sources)
    if missing:
        gaps.append(f"What do sources say about {', '.join(missing)}? (no source mentions it)")
    return gaps


def _missing_question_words(question: str, claims: Sequence[Claim], sources: Sequence[Source]) -> list[str]:
    """Question words that no claim and no source text mentions (a word only in the question's phrasing,
    like "drives", is in the sources' vocabulary often enough that it is not reported)."""
    texts = [f"{c.text} {c.quote}" for c in claims] + [s.text for s in sources]
    said = set().union(*(g.content_words(t) for t in texts)) if texts else set()
    words = [w for w in g.content_words(question) if len(w) >= 4 and w not in said and w not in FRAMING_WORDS]
    return sorted(words)


def _lower(statement: str) -> str:
    """Lower-cases the first letter so the statement reads inside a question, unless it starts an acronym."""
    text = statement.rstrip(" .")
    return text if text[1:2].isupper() else text[:1].lower() + text[1:]
