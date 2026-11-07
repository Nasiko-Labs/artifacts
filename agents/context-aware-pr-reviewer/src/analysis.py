"""The model's three jobs, each a small JSON step validated in code: extract the ticket's criteria
(verbatim-checked), review one diff chunk (criterion status + comments, verified by verify.py), and a
short summary (numbers checked against the code-computed facts, else a code-built sentence)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import prompts
import verify
from align import Criterion
from deps import TurnDeps
from diffing import Chunk, FilePatch
from reader import ask_once

log = logging.getLogger("pr_reviewer.analysis")

MAX_CRITERIA = 8
MAX_TICKET_CHARS = 6_000
REVIEW_SECS = 120.0
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)]|\[[ xX]\])\s+")
_SPACES = re.compile(r"\s+")
_NUMBERS = re.compile(r"\d+(?:/\d+)?")


def load_object(text: str) -> dict | None:
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    for candidate in (body, body[body.find("{"): body.rfind("}") + 1] if "{" in body else ""):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _norm(text: str) -> str:
    return _SPACES.sub(" ", text).strip().casefold()


async def extract_criteria(deps: TurnDeps, ticket_text: str) -> tuple[tuple[Criterion, ...], int]:
    """(criteria, how many the model returned that were not verbatim in the ticket and were dropped)."""
    text = ticket_text[:MAX_TICKET_CHARS]
    messages = [{"role": "system", "content": prompts.CRITERIA_SYSTEM},
                {"role": "user", "content": prompts.fence_data(text)}]
    data = load_object(await ask_once(deps, messages))
    if data is None or not isinstance(data.get("criteria"), list):
        repair = [*messages, {"role": "user", "content": "That was not the JSON object asked for. Reply with it now."}]
        data = load_object(await ask_once(deps, repair)) or {}
    raw = [c for c in data.get("criteria") or () if isinstance(c, Mapping)]
    haystack = _norm(text)
    kept, dropped = [], 0
    for c in raw:
        body = _BULLET.sub("", str(c.get("text") or "")).strip()
        if not body or _norm(body) not in haystack:
            dropped += 1
            continue
        if any(_norm(k.text) == _norm(body) for k in kept):
            continue
        kept.append(Criterion(f"C{len(kept) + 1}", body))
    return tuple(kept[:MAX_CRITERIA]), dropped


@dataclass(frozen=True)
class ChunkReview:
    criteria: tuple[Criterion, ...]
    comments: tuple[verify.Comment, ...]
    dropped: int
    ok: bool = True


async def review_chunks(deps: TurnDeps, chunks: Sequence[Chunk], criteria: Sequence[Criterion],
                        precedent_text: str, focus: str, files: Mapping[str, FilePatch], seen: str,
                        precedents: frozenset[str]) -> tuple[ChunkReview, ...]:
    jobs = [_review_one(deps, c, criteria, precedent_text, focus, files, seen, precedents) for c in chunks]
    try:
        return tuple(await asyncio.wait_for(asyncio.gather(*jobs), timeout=REVIEW_SECS))
    except TimeoutError:
        log.warning("review ran out of time")
        return tuple(ChunkReview((), (), 0, ok=False) for _ in chunks)


async def _review_one(deps, chunk: Chunk, criteria, precedent_text, focus, files, seen, precedents) -> ChunkReview:
    listing = "\n".join(f"{c.id}: {c.text}" for c in criteria) or "(no ticket criteria)"
    data_text = (f"Acceptance criteria:\n{listing}\n\nPast review feedback on the same files:\n"
                 f"{precedent_text or '(none)'}\n\nDiff chunk:\n{chunk.text}")
    messages = [{"role": "system", "content": prompts.review_system(focus)},
                {"role": "user", "content": prompts.fence_data(data_text)}]
    data = load_object(await ask_once(deps, messages))
    if data is None:
        return ChunkReview((), (), 0, ok=False)
    by_id = {c.id: c for c in criteria}
    results = []
    for r in data.get("criteria") or ():
        if not isinstance(r, Mapping) or str(r.get("id")) not in by_id:
            continue
        valid, invalid = verify.evidence(r.get("evidence"), files)
        status = verify.downgrade(str(r.get("status") or ""), valid, invalid)
        base = by_id[str(r["id"])]
        results.append(Criterion(base.id, base.text, status, valid if status != "not_found" else ()))
    raws = list(data.get("comments") or ())
    kept, dropped = verify.check_all(raws, files, seen, precedents)
    return ChunkReview(tuple(results), kept, dropped)


async def summarize(deps: TurnDeps, facts: str, fallback: str) -> str:
    data = load_object(await ask_once(deps, [{"role": "system", "content": prompts.SUMMARY_SYSTEM},
                                             {"role": "user", "content": prompts.fence_data(facts)}])) or {}
    summary = str(data.get("summary") or "").strip()
    if not summary or any(n not in facts for n in _NUMBERS.findall(summary)):
        return fallback
    return summary
