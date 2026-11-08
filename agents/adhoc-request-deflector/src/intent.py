"""Request intent: one model call for a batch of messages (one in single mode, up to 5 in a scan),
checked in code. A term survives only when all its words appear in that message (else it is dropped
and counted); the time grain is read from the message's own words in code."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from deps import TurnDeps
from index import tokens
from llm import ask_once, parse_answer
from models import GRAINS, Intent

log = logging.getLogger("deflector.intent")

MAX_TERMS = 6
_GRAIN_WORDS: Mapping[str, tuple[str, ...]] = {
    "hourly": (r"hourly", r"per hour", r"by hour", r"each hour"),
    "daily": (r"daily", r"per day", r"by day", r"each day", r"day over day", r"dau"),
    "weekly": (r"weekly", r"per week", r"by week", r"each week", r"week over week", r"wau"),
    "monthly": (r"monthly", r"per month", r"by month", r"each month", r"month over month", r"mau"),
    "quarterly": (r"quarterly", r"per quarter", r"by quarter", r"each quarter"),
    "yearly": (r"yearly", r"annual", r"annually", r"per year", r"by year", r"year over year"),
}
_GRAIN_RES = {g: re.compile(r"\b(?:" + "|".join(ws) + r")\b", re.IGNORECASE) for g, ws in _GRAIN_WORDS.items()}


@dataclass(frozen=True)
class IntentResult:
    status: str  # "ok" | "off_topic" | "unreadable"
    intents: Mapping[str, Intent]
    dropped_terms: int = 0


def grain_of(text: str) -> str | None:
    found = [g for g in GRAINS if _GRAIN_RES[g].search(text or "")]
    return found[0] if len(found) == 1 else None


def grains_in(text: str) -> frozenset[str]:
    return frozenset(g for g in GRAINS if _GRAIN_RES[g].search(text or ""))


def is_arithmetic(text: str) -> bool:
    """A bare arithmetic question ("what's 2+2?"): out of scope, decided without a model call."""
    return bool(re.fullmatch(r"\s*(?:what(?:'s|\s+is)\s+|calculate\s+|compute\s+)?[\d\s.+\-*/x×^()=]*\d"
                             r"[\d\s.+\-*/x×^()=]*[+\-*/x×^][\d\s.+\-*/x×^()=]*\d\s*\??\s*", (text or "").casefold()))


async def read_intents(deps: TurnDeps, messages: Sequence[tuple[str, str]]) -> IntentResult:
    """`messages` are (id, text). In single mode the model may answer off_topic."""
    answer = parse_answer(await ask_once(deps, (
        {"role": "system", "content": prompts.INTENT_SYSTEM},
        {"role": "user", "content": prompts.intent_request(list(messages))},
    )))
    if answer is None:
        return IntentResult("unreadable", {})
    if answer.get("status") == "off_topic":
        return IntentResult("off_topic", {})
    raw = answer.get("requests") if isinstance(answer.get("requests"), list) else []
    texts = dict(messages)
    intents: dict[str, Intent] = {}
    dropped = 0
    for item in raw:
        if not isinstance(item, Mapping) or str(item.get("id")) not in texts:
            continue
        intent, lost = checked(item, texts[str(item["id"])])
        intents[str(item["id"])] = intent
        dropped += lost
    return IntentResult("ok", intents, dropped)


def checked(item: Mapping[str, Any], text: str) -> tuple[Intent, int]:
    words = frozenset(tokens(text))
    metrics, lost_m = _terms(item.get("metric_terms"), words)
    dims, lost_d = _terms(item.get("dimension_terms"), words)
    filters, lost_f = _terms(item.get("filters"), words)
    if lost_m + lost_d + lost_f:
        log.info("dropped %d intent term(s) not found in the request", lost_m + lost_d + lost_f)
    is_request = item.get("is_data_request") is True and bool(metrics or dims)
    return Intent(is_request, metrics, dims, grain_of(text), filters), lost_m + lost_d + lost_f


def _terms(value: Any, words: frozenset[str]) -> tuple[tuple[str, ...], int]:
    raw = [str(v).strip() for v in value if isinstance(v, (str, int, float))] if isinstance(value, list) else []
    kept, lost = [], 0
    for term in raw:
        own = tokens(term)
        if own and set(own) <= words:
            kept.append(" ".join(term.split()))
        elif term:
            lost += 1
    return tuple(dict.fromkeys(kept))[:MAX_TERMS], lost
