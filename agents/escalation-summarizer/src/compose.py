"""Compose the three fields from checked facts only, then check them in code.

One compose call (+1 JSON repair inside llm.complete_json). When the checks find a problem (an ungrounded
number, date or name, blame wording, a sentence citing no or unknown fact ids, fewer than two ids cited)
there is one more call with the problems listed; whatever still fails is then dropped (summary sentences)
or replaced by code-built text from the facts (ask, impact). No ask fact -> the fixed ask fallback; no
impact fact -> "Not stated in the thread": the model is never asked to infer either."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import checks
import facts as fx
import grounding as g
import llm
import prompts
from messages import index_of
from style import Style, allowed_words

log = logging.getLogger("escalation.compose")

ASK_MAX_WORDS = 40
IMPACT_MAX_WORDS = 60
MIN_CITED_FACTS = 2
_FACT_REFS = re.compile(r"\s*[(\[]\s*f\d+(?:\s*,\s*f\d+)*\s*[)\]]")  # "(f1, f2)" the model leaks into prose


@dataclass(frozen=True)
class Composed:
    sentences: tuple[tuple[str, tuple[str, ...]], ...]   # (sentence, cited fact ids); empty = bullets
    ask: str
    ask_fallback: bool
    impact: str
    impact_stated: bool
    dropped: int = 0      # summary sentences left out
    replaced: int = 0     # ask / impact rebuilt by code from the facts

    @property
    def bullets(self) -> bool:
        return not self.sentences


@dataclass(frozen=True)
class _Ctx:
    facts: tuple[fx.Fact, ...]
    norm: str
    allowed: frozenset[str]
    style: Style
    resolved: bool


async def compose(deps: llm.TurnDeps, facts: Sequence[fx.Fact], thread: str, timeline: str, style: Style,
                  resolved: bool, context: Sequence[str] = ()) -> Composed:
    ctx = _Ctx(tuple(facts), g.normalise(f"{thread}\n{timeline}"), allowed_words(style), style, resolved)
    latest = fx.latest(facts, "status")
    base = (fx.block(facts), latest.id if latest else "", style.describe(), context)
    try:
        parsed = await llm.complete_json(deps, prompts.compose_messages(*base), prompts.COMPOSE_CONTRACT,
                                         parse_compose)
    except llm.LlmError:
        log.exception("compose failed; rendering the checked facts as bullets")
        return finalize({"summary": [], "ask": None, "customer_impact": None}, ctx)
    problems = evaluate(parsed, ctx)
    if problems:
        log.info("compose check found %d problems (%s); one repair call", len(problems), _kinds(problems))
        try:
            parsed = await llm.complete_json(deps, prompts.compose_messages(*base, problems=problems),
                                             prompts.COMPOSE_CONTRACT, parse_compose)
        except llm.LlmError:
            log.warning("compose repair failed; checking the first answer")
    return finalize(parsed, ctx)


def parse_compose(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or data.get("status") not in ("ok", "off_topic"):
        return None
    summary = data.get("summary") or []
    if isinstance(summary, str):
        summary = [{"text": s, "fact_ids": []} for s in checks.sentences(summary)]
    if not isinstance(summary, list) or not all(isinstance(s, Mapping) for s in summary):
        return None
    sentences = []
    for entry in summary:  # an entry holding several sentences is split; each keeps the entry's ids
        ids = [str(i) for i in entry.get("fact_ids") or [] if isinstance(i, (str, int))]
        sentences += [{"text": text, "fact_ids": ids} for text in checks.sentences(fx.clean(entry.get("text")))]
    return {"summary": sentences, "ask": _optional(data.get("ask")), "customer_impact": _optional(data.get("customer_impact"))}


def evaluate(parsed: Mapping[str, Any], ctx: _Ctx) -> list[str]:
    known = {f.id for f in ctx.facts}
    problems: list[str] = []
    cited: set[str] = set()
    for n, sentence in enumerate(parsed["summary"], start=1):
        ids = sentence["fact_ids"]
        if not ids or not set(ids) <= known:
            problems.append(f"summary sentence {n} must cite only fact ids from the FACTS block")
        cited |= set(ids) & known
        problems += [f"summary sentence {n}: {p}" for p in checks.issues(sentence["text"], ctx.norm, ctx.allowed)]
    if len(cited) < MIN_CITED_FACTS:
        problems.append(f"the summary must cite at least {MIN_CITED_FACTS} different fact ids")
    for field, kind in (("ask", "ask"), ("customer_impact", "impact")):
        if fx.of_kind(ctx.facts, kind):
            problems += [f"{field}: {p}" for p in checks.issues(parsed.get(field) or "", ctx.norm, ctx.allowed)]
    return problems


def finalize(parsed: Mapping[str, Any], ctx: _Ctx) -> Composed:
    known = {f.id for f in ctx.facts}
    kept, dropped, words = [], 0, 0
    for sentence in parsed["summary"]:
        text = _FACT_REFS.sub("", checks.remove_hedges(sentence["text"], ctx.norm)).strip()
        ids = tuple(i for i in sentence["fact_ids"] if i in known)
        if not text or not ids or checks.issues(text, ctx.norm, ctx.allowed):
            dropped += 1
            continue
        words += len(text.split())
        if words > ctx.style.max_words and kept:
            break
        kept.append((checks.cap_words(text, ctx.style.max_words), ids))
    if len({i for _, ids in kept for i in ids}) < MIN_CITED_FACTS:
        kept = []
    if kept and ctx.resolved:
        kept.insert(0, (checks.RESOLVED_LEAD, ()))
    ask, ask_fallback, ask_replaced = _ask(parsed.get("ask"), ctx)
    impact, stated, impact_replaced = _impact(parsed.get("customer_impact"), ctx)
    return Composed(tuple(kept), ask, ask_fallback, impact, stated, dropped, ask_replaced + impact_replaced)


def _ask(text: str | None, ctx: _Ctx) -> tuple[str, bool, int]:
    asks = sorted(fx.of_kind(ctx.facts, "ask"), key=lambda f: -index_of(f.message))
    if not asks:
        return prompts.ASK_FALLBACK, True, 0
    cleaned = checks.remove_hedges(text or "", ctx.norm)
    if cleaned and not checks.issues(cleaned, ctx.norm, ctx.allowed):
        return checks.cap_words(cleaned, ASK_MAX_WORDS), False, 0
    built = asks[0].text + "".join(f" Also: {f.text}" for f in asks[1:3])
    return checks.cap_words(built, ASK_MAX_WORDS), False, int(bool(text))


def _impact(text: str | None, ctx: _Ctx) -> tuple[str, bool, int]:
    impacts = fx.of_kind(ctx.facts, "impact")
    if not impacts:
        return prompts.NOT_STATED, False, 0
    cleaned = checks.remove_hedges(text or "", ctx.norm)
    if cleaned and not checks.issues(cleaned, ctx.norm, ctx.allowed):
        return checks.cap_words(cleaned, IMPACT_MAX_WORDS), True, 0
    return checks.cap_words("; ".join(f.text.rstrip(".") for f in impacts) + ".", IMPACT_MAX_WORDS), True, int(bool(text))


def _kinds(problems: Sequence[str]) -> str:
    """Problem categories for the log (never the text itself: it is customer content)."""
    labels = {"name": "names", "is not in the thread": "values", "blame": "blame", "fact id": "ids"}
    counts = {label: sum(key in p for p in problems) for key, label in labels.items()}
    return ", ".join(f"{label}={n}" for label, n in counts.items() if n)


def _optional(value: Any) -> str | None:
    text = fx.clean(value) if isinstance(value, str) else ""
    return None if not text or text.casefold() in ("null", "none", "n/a") else text
