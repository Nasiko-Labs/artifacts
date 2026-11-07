"""Query expansion: one model call returns 3-5 keyword searches, validated in code and repaired once.
Code then adds the exact distinctive tokens (error codes, identifiers) as one literal query. If the
repair also fails, code builds a single query from the question's own terms: no third call."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

import prompts
from deps import TurnDeps
from llm import ask_once, parse_answer
from terms import literals, terms

log = logging.getLogger("dedup.queries")

MIN_QUERIES, MAX_QUERIES = 3, 5
MAX_WORDS = 6
MAX_LITERALS = 3
_FORBIDDEN = re.compile(r"[:*()\[\]{}<>|~^+=@]|(?:^|\s)-\S|\b(?:AND|OR|NOT)\b")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Queries:
    status: Literal["ok", "off_topic"]
    queries: tuple[str, ...] = ()
    repaired: bool = False
    fallback: bool = False


def validate(answer: dict[str, Any] | None) -> tuple[tuple[str, ...] | None, str]:
    """(the cleaned queries, "") or (None, what was wrong)."""
    if answer is None:
        return None, "the answer was not a JSON object"
    raw = answer.get("queries")
    if not isinstance(raw, list) or not raw:
        return None, "there were no queries"
    if not MIN_QUERIES <= len(raw) <= MAX_QUERIES:
        return None, f"there were {len(raw)} queries"
    cleaned = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            return None, "a query was empty"
        if _FORBIDDEN.search(item):
            return None, f"the query {item!r} uses an operator"
        query = _SPACES.sub(" ", item.strip()).lower()
        if not 1 <= len(query.split()) <= MAX_WORDS:
            return None, f"the query {item!r} has more than {MAX_WORDS} words"
        cleaned.append(query)
    return tuple(dict.fromkeys(cleaned)), ""


def with_literals(queries: tuple[str, ...], text: str) -> tuple[str, ...]:
    tokens = literals(text)[:MAX_LITERALS]
    if not tokens:
        return queries
    literal = " ".join(f'"{t}"' for t in tokens)
    return queries if literal in queries else (*queries, literal)


def fallback_query(text: str) -> str:
    """The question's own distinctive words, in order: the last resort when the model's queries are invalid."""
    words = [w.strip("._-") for w in re.findall(r"[a-z0-9][a-z0-9_.-]*", text.casefold())]
    kept = list(dict.fromkeys(w for w in words if w and terms(w)))
    return " ".join(kept[:MAX_WORDS]) or text.strip()[:60].lower()


async def build(deps: TurnDeps, question: str, truncated: bool) -> Queries:
    messages = [{"role": "system", "content": prompts.QUERIES_SYSTEM},
                {"role": "user", "content": prompts.queries_request(question, truncated)}]
    first = await ask_once(deps, messages, response_format={"type": "json_object"})
    answer = parse_answer(first)
    if answer is not None and answer.get("status") == "off_topic":
        return Queries("off_topic")
    queries, problem = validate(answer)
    if queries is not None:
        return Queries("ok", with_literals(queries, question))
    log.info("queries rejected (%s); asking once for a repair", problem)
    repair = [*messages, {"role": "assistant", "content": first},
              {"role": "user", "content": prompts.QUERIES_REPAIR.format(problem=problem)}]
    answer = parse_answer(await ask_once(deps, repair, response_format={"type": "json_object"}))
    if answer is not None and answer.get("status") == "off_topic":
        return Queries("off_topic")
    queries, problem = validate(answer)
    if queries is not None:
        return Queries("ok", with_literals(queries, question), repaired=True)
    log.warning("repaired queries rejected too (%s); using the question's own terms", problem)
    return Queries("ok", with_literals((fallback_query(question),), question), repaired=True, fallback=True)
