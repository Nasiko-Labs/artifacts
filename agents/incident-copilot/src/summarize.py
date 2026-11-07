"""Summarize: one tool-less call (+ one JSON repair) over the fenced, code-built facts. Code keeps the
sentence only if every number and id in it is in the facts, and a fix paraphrase only for a past incident
that is in the facts; otherwise the template sentence and the verbatim resolution note are used."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping

import prompts
from deps import TurnDeps
from jsonutil import load_json_object, text_of
from loops import ask_once

log = logging.getLogger("incident_copilot.summarize")

MAX_SENTENCE_CHARS = 300
_NUMBERISH = re.compile(r"[A-Za-z_#-]*\d[\w.:%/-]*")
_URLISH = re.compile(r"https?://\S+")
_HEX = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def facts_text(f: Mapping) -> str:
    """What the model may use: the facts minus links it does not need."""
    view = {k: f.get(k) for k in ("incident", "t0", "service", "metrics", "log_spikes", "errors", "suspects",
                                  "similar", "notes")}
    return json.dumps(view, ensure_ascii=False, default=str)


def template(f: Mapping) -> str:
    inc = f.get("incident") or {}
    what, where = inc.get("title") or "An incident", f.get("service") or ""
    if where and where not in what:
        what = f"{what} on {where}"
    return f"{what}, started {f.get('t0')}."


def _numbers(text: str) -> set[float]:
    return {float(n) for n in _NUMBER.findall(text)}


def _number_backed(value: float, known: set[float]) -> bool:
    """30 is backed by 30.0 or 29.96 (a rounded figure), never by 31."""
    return any(value == k or value == round(k) or value == round(k, 1) for k in known)


def _backed(sentence: str, facts: str) -> bool:
    """Every URL, and every token with a digit (an id, a time, a number), comes from the facts: verbatim, or
    as a number the facts hold (possibly rounded, possibly with a unit such as "30x" or "12 min")."""
    if any(url not in facts for url in _URLISH.findall(sentence)):
        return False
    known = _numbers(_HEX.sub(" ", _URLISH.sub(" ", facts)))
    for token in (t.strip(".,;:)") for t in _NUMBERISH.findall(_URLISH.sub(" ", sentence))):
        if token and token not in facts and not all(_number_backed(n, known) for n in _numbers(token)):
            return False
    return True


def checked(answer: Mapping, f: Mapping) -> tuple[str, dict[str, str]]:
    facts = facts_text(f)
    sentence = " ".join(text_of(answer.get("broken")).split())
    if not sentence or len(sentence) > MAX_SENTENCE_CHARS or not _backed(sentence, facts):
        sentence = template(f)
    quotes = {p["id"]: p.get("resolution_quote") or "" for p in f.get("similar") or ()}
    raw = answer.get("fixes") if isinstance(answer.get("fixes"), dict) else {}
    fixes = {}
    for ident, text in raw.items():
        paraphrase = " ".join(text_of(text).split())
        if ident in quotes and quotes[ident] and paraphrase and _backed(paraphrase, quotes[ident]):
            fixes[ident] = paraphrase
    return sentence, fixes


async def summarize(deps: TurnDeps, f: Mapping) -> tuple[str, dict[str, str]]:
    messages = ({"role": "system", "content": prompts.SUMMARIZE_SYSTEM},
                {"role": "user", "content": prompts.fence_facts(facts_text(f))})
    try:
        answer = await asyncio.wait_for(_ask(deps, messages), timeout=max(deps.limits.summarize_secs, 1.0))
    except TimeoutError:
        log.warning("summary timed out; using the template sentence")
        answer = None
    return checked(answer or {}, f)


async def _ask(deps: TurnDeps, messages: tuple) -> dict | None:
    data = load_json_object(await ask_once(deps, messages))
    if data is not None:
        return data
    repair = ({"role": "user", "content": prompts.REPAIR_INSTRUCTION},)
    return load_json_object(await ask_once(deps, (*messages, *repair)))
