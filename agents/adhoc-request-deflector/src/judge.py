"""The judge: one model call per request over the BM25 shortlist, then code decides. The model picks a
row (only from the shortlist) and a verdict; code computes what the row covers from its tokens and
downgrades: `exact` needs every term covered, BM25 rank 1 and no time-grain mismatch, else `partial`;
`partial` needs at least one metric covered, else `none`. When the model picks nothing, the top-ranked
row is still `partial` if it covers a metric (never `exact`). Covers, missing and why are written by code.
Anything the model names that is not in the shortlist or the request is dropped and counted."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from deps import TurnDeps
from index import tokens
from intent import grains_in
from llm import ask_once, parse_answer
from models import Intent, Match, Row

log = logging.getLogger("deflector.judge")

GRAIN_TOKENS = frozenset({"hourly", "daily", "weekly", "monthly", "quarterly", "yearly", "annual", "annually",
                          "dau", "wau", "mau"})
_VERDICTS = ("exact", "partial", "none")


@dataclass(frozen=True)
class Judged:
    match: Match
    row: Row | None
    dropped: int = 0  # model references not backed by the shortlist or the request


def covered(term: str, row: Row) -> bool:
    words = [t for t in tokens(term) if t not in GRAIN_TOKENS]
    return bool(words) and set(words) <= row.tokens


def none_match(intent: Intent, why: str | None = None) -> Match:
    asked = ", ".join(intent.metric_terms or intent.terms) or "this"
    return Match(dashboard=None, url=None, confidence="none", covers=[], missing=list(intent.terms),
                 why=why or f"No dashboard in the index names {asked}.")


async def judge(deps: TurnDeps, request: str, intent: Intent, shortlist: Sequence[tuple[Row, float]]) -> Judged:
    if not shortlist:
        return Judged(none_match(intent), None)
    candidates = "\n".join(_line(row) for row, _ in shortlist)
    answer = parse_answer(await ask_once(deps, (
        {"role": "system", "content": prompts.JUDGE_SYSTEM},
        {"role": "user", "content": prompts.judge_request(request, list(intent.metric_terms),
                                                         list(intent.dimension_terms), intent.time_grain,
                                                         candidates)},
    )))
    return decide(answer or {}, request, intent, shortlist)


def decide(answer: Mapping[str, Any], request: str, intent: Intent,
           shortlist: Sequence[tuple[Row, float]]) -> Judged:
    """Pure: the code verdict from the model's pick."""
    by_id = {row.id: (rank, row) for rank, (row, _) in enumerate(shortlist, start=1)}
    picked = str(answer.get("dashboard") or "").strip()
    verdict = str(answer.get("verdict") or "none").strip().casefold()
    verdict = verdict if verdict in _VERDICTS else "none"
    dropped = _unbacked_terms(answer, request)
    if picked and picked not in by_id:
        log.info("judge named %r, which is not in the shortlist", picked)
        return Judged(none_match(intent), None, dropped + 1)
    if verdict == "none" or not picked:
        # Code decides partial-vs-none: the top-ranked row still counts as partial when it covers a metric.
        top = shortlist[0][0]
        match = _graded("partial", 1, top, intent)
        return Judged(match, top if match.confidence == "partial" else None, dropped)
    rank, row = by_id[picked]
    return Judged(_graded(verdict, rank, row, intent), row, dropped)


def _graded(verdict: str, rank: int, row: Row, intent: Intent) -> Match:
    terms = (*intent.terms, *intent.filters)
    covers = [t for t in terms if covered(t, row)]
    missing = [t for t in terms if t not in covers]
    row_grains = grains_in(row.text)
    if intent.time_grain and row_grains and intent.time_grain not in row_grains:
        shown = "/".join(sorted(row_grains))
        missing.append(f"{intent.time_grain} view (the dashboard is {shown})")
    if any(m.startswith(f"{intent.time_grain} view") for m in missing):
        covers = [_without_grain(t) for t in covers]
    metrics = intent.metric_terms or intent.terms
    metric_hit = any(covered(t, row) for t in metrics)
    if verdict == "exact" and (missing or rank != 1):
        verdict = "partial"
    if not metric_hit:
        return none_match(intent, f"\"{row.name}\" does not name {', '.join(metrics) or 'what was asked'}.")
    why = (f"\"{row.name}\" names {', '.join(covers)}." if verdict == "exact"
           else f"\"{row.name}\" covers {', '.join(covers) or 'part of it'}; not {', '.join(missing) or 'all of it'}.")
    return Match(dashboard=row.name, url=row.url, confidence=verdict, covers=covers, missing=missing, why=why)


def _without_grain(term: str) -> str:
    """'daily active users' → 'active users' when the row has another grain."""
    kept = [w for w in term.split() if w.casefold() not in GRAIN_TOKENS]
    return " ".join(kept) or term


def _unbacked_terms(answer: Mapping[str, Any], request: str) -> int:
    words = frozenset(tokens(request))
    listed = [*(answer.get("covers") or []), *(answer.get("missing") or [])] \
        if isinstance(answer.get("covers") or [], list) and isinstance(answer.get("missing") or [], list) else []
    return sum(1 for t in listed if isinstance(t, str) and tokens(t) and not set(tokens(t)) <= words)


def _line(row: Row) -> str:
    parts = {"name": row.name, "description": row.description, "metrics": row.metrics,
             "dimensions": row.dimensions, "tags": row.tags}
    return f"{row.id}: " + json.dumps({k: v for k, v in parts.items() if v}, ensure_ascii=False)
