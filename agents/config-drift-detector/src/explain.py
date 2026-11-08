"""The one model step in a report: a <= 2 sentence headline and a <= 25-word "why it matters" line for the
top drifts. Facts go in computed; every number, quoted value or identifier the model writes must already
be in those facts, or the line is dropped. A failed or unusable call leaves the report without them."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import replace

import drift as dr
import prompts
from deps import TurnDeps
from llm import ask_json

log = logging.getLogger("drift.explain")

TOP = 10
MAX_WHY_WORDS = 25
MAX_HEADLINE_SENTENCES = 2
_TOKENS = re.compile(r"`([^`]+)`|\"([^\"]+)\"|(\b[\w./:@-]*\d[\w./:@-]*\b)")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def facts(drifts: Sequence[dr.Drift]) -> list[dict]:
    return [{"id": d.id, "resource": d.resource, "attribute": d.attribute, "severity": d.severity, "kind": d.kind,
             "expected": d.expected, "actual": d.actual, "environment": d.environment or "unknown",
             "changed_by": d.changed_by} for d in drifts[:TOP]]


async def explain(deps: TurnDeps, drifts: Sequence[dr.Drift], summary: str) -> tuple[tuple[dr.Drift, ...], str]:
    """(drifts with `why` set where a checked line came back, checked headline or "")."""
    if not drifts:
        return tuple(drifts), ""
    data = json.dumps({"summary": summary, "drifts": facts(drifts)}, default=str)
    messages = ({"role": "system", "content": prompts.EXPLAIN_SYSTEM},
                {"role": "user", "content": prompts.fence_facts(data)})
    try:
        answer = await ask_json(deps, messages)
    except Exception:
        log.exception("explain call failed; rendering without explanations")
        return tuple(drifts), ""
    if not answer:
        return tuple(drifts), ""
    why = answer.get("why") if isinstance(answer.get("why"), dict) else {}
    checked = tuple(replace(d, why=line) if (line := _checked(why.get(d.id), data, MAX_WHY_WORDS)) else d
                    for d in drifts)
    return checked, _headline(answer.get("headline"), data)


def _checked(text, facts_text: str, max_words: int) -> str:
    if not isinstance(text, str) or not text.strip() or prompts.OFF_TOPIC in text:
        return ""
    words = " ".join(text.split()).split(" ")
    clean = " ".join(words[:max_words])
    return clean if grounded(clean, facts_text) else ""


def _headline(text, facts_text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return ""
    sentences = _SENTENCE.split(" ".join(text.split()))
    clean = " ".join(sentences[:MAX_HEADLINE_SENTENCES])
    return clean if grounded(clean, facts_text) and prompts.OFF_TOPIC not in clean else ""


def grounded(text: str, facts_text: str) -> bool:
    """Every number, quoted value and identifier-like token appears verbatim in the facts."""
    for m in _TOKENS.finditer(text):
        token = next(g for g in m.groups() if g)
        if token.strip(".,;:") not in facts_text:
            return False
    return True
