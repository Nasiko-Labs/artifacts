"""The one model call of a queue turn: a two-sentence headline and a short reason per stale pull request,
written from facts computed in code. Every number, @login and #number the model writes must be in
those facts; otherwise the code's own template sentence replaces it."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence

import httpx
import openai

import prompts
import render
from ask import ask_once
from deps import TurnDeps
from queue_rules import Assessed
from view import View

log = logging.getLogger("pr_review_queue.summary")

MAX_REASONS = 10
MAX_REASON_WORDS = 20
_NUMBERS = re.compile(r"\d+")
_MENTIONS = re.compile(r"@([A-Za-z0-9_.\[\]-]+)")


def template_headline(v: View) -> str:
    ready = sum(1 for a in v.stale if any(b.kind == "ready_to_merge" for b in a.blocked))
    head = f"{len(v.stale)} of {v.open_count} open PRs are stale"
    return head + (f"; {ready} are ready to merge and waiting on their authors." if ready else ".")


def template_reason(a: Assessed) -> str:
    text = render.waiting_text(a)
    return text[:1].upper() + text[1:] + "."


def facts_text(v: View) -> str:
    lines = [f"open pull requests: {v.open_count}", f"stale pull requests: {len(v.stale)}",
             f"stale threshold days: {v.settings.stale_days}"]
    kinds: dict[str, int] = {}
    for a in v.stale:
        for b in a.blocked:
            kinds[b.kind] = kinds.get(b.kind, 0) + 1
    lines += [f"stale pull requests blocked by {k}: {n}" for k, n in kinds.items()]
    for rank, a in enumerate(v.stale[:MAX_REASONS], start=1):
        blockers = "; ".join(f"{b.kind} who={b.who} since_days={b.since_days}" for b in a.blocked) or "none"
        lines.append(f"rank {rank}: {a.facts.repo}#{a.facts.pr.number} author={a.facts.pr.author} "
                     f"quiet_days={a.quiet_days} age_days={a.age_days} priority={a.priority} blockers: {blockers}")
    return "\n".join(lines)


async def write(deps: TurnDeps, v: View) -> tuple[str, dict[int, str]]:
    """(headline, rank → reason); the templates when the model fails or writes anything unchecked."""
    if not v.stale:
        return template_headline(v), {}
    facts = facts_text(v)
    try:
        raw = await _ask(deps, facts)
    except (openai.OpenAIError, httpx.HTTPError) as e:  # a failed summary never fails the turn: the code has every number already
        log.warning("summary call failed: %s", type(e).__name__)
        return template_headline(v), {}
    data = _load(raw)
    headline = str(data.get("headline") or "")
    allowed_numbers, allowed_people = _allowed(facts, v.stale)
    if not headline or not _checked(headline, allowed_numbers, allowed_people) or len(headline) > 400:
        headline = template_headline(v)
    reasons = {}
    given = data.get("reasons") if isinstance(data.get("reasons"), Mapping) else {}
    for rank, a in enumerate(v.stale[:MAX_REASONS], start=1):
        text = str(given.get(str(rank)) or "").strip()
        nums, people = _allowed(facts_text_for(a), [a])
        ok = text and len(text.split()) <= MAX_REASON_WORDS and _checked(text, nums, people)
        reasons[rank] = render.title(text) if ok else template_reason(a)
    return headline, reasons


def facts_text_for(a: Assessed) -> str:
    blockers = " ".join(f"{b.who} {b.since_days}" for b in a.blocked)
    return f"{a.facts.pr.number} {a.quiet_days} {a.age_days} {a.priority} {blockers}"


def _allowed(facts: str, stale: Sequence[Assessed]) -> tuple[set[str], set[str]]:
    people = {p.casefold() for a in stale for p in (a.facts.pr.author, *(b.who for b in a.blocked),
                                                     *(r.author for r in a.facts.reviews), *a.facts.pr.requested) if p}
    return set(_NUMBERS.findall(facts)), people


def _checked(text: str, numbers: set[str], people: set[str]) -> bool:
    if any(n not in numbers for n in _NUMBERS.findall(text)):
        return False
    return all(m.casefold().rstrip(".") in people for m in _MENTIONS.findall(text))


async def _ask(deps: TurnDeps, facts: str) -> str:
    return await ask_once(deps, prompts.SUMMARY_SYSTEM, prompts.fence_facts(facts))


def _load(raw: str) -> dict:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
