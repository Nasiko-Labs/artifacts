"""Relate: for each finding with claims from 2+ sources, the model labels pairs of claims from different sources
agree / conflict / unrelated (FINDINGS_PER_CALL findings per call, at most MAX_RELATE_CALLS calls). Code gates
each label: `agree` needs a content-word overlap >= AGREE_MIN_OVERLAP between the two claims' texts; `conflict`
needs both claims to be fact/statistic/prediction and a difference line that quotes both claims' numbers (or,
without numbers, a word only each claim has). Everything else is rejected and counted."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import grounding as g
import llm
import prompts
from model import CONFLICT_KINDS, Claim

log = logging.getLogger("research-synthesizer.relate")

AGREE_MIN_OVERLAP = 0.25
FINDINGS_PER_CALL = 4
MAX_RELATE_CALLS = 8
MAX_PAIRS_PER_FINDING = 6
DIFFERENCE_MAX_CHARS = 240


@dataclass(frozen=True)
class Relations:
    agree: tuple[tuple[str, str], ...] = ()
    conflicts: tuple[tuple[str, str, str], ...] = ()     # (claim a, claim b, difference)


@dataclass(frozen=True)
class Related:
    by_finding: dict[str, Relations] = field(default_factory=dict)
    rejected: int = 0


def pairs(claims: Sequence[Claim]) -> list[tuple[Claim, Claim]]:
    found = [(a, b) for a, b in combinations(claims, 2) if a.source_id != b.source_id]
    return found[:MAX_PAIRS_PER_FINDING]


async def relate(deps: llm.TurnDeps, findings: Sequence[tuple[str, Sequence[Claim]]],
                 context: Sequence[str] = ()) -> Related:
    """`findings` is (finding id, its claims); only those with pairs from different sources are sent."""
    asked = {fid: pairs(cs) for fid, cs in findings}
    asked = {fid: ps for fid, ps in asked.items() if ps}
    ids = list(asked)
    batches = [ids[i:i + FINDINGS_PER_CALL] for i in range(0, len(ids), FINDINGS_PER_CALL)][:MAX_RELATE_CALLS]
    raw: list[Mapping[str, Any]] = []
    for batch in batches:
        try:
            parsed = await llm.complete_json(deps, _messages(batch, asked, context), prompts.RELATE_CONTRACT, parse)
        except llm.LlmError:
            log.warning("relate call failed for %s", ",".join(batch))
            continue
        raw += parsed["relations"]
    return gate(raw, asked)


def _messages(batch: Sequence[str], asked: Mapping[str, list[tuple[Claim, Claim]]], context: Sequence[str]) -> list[dict]:
    lines = []
    for fid in batch:
        for a, b in asked[fid]:
            lines.append(f"{fid}: {a.id} ({a.source_id}, {a.kind}) \"{a.text}\" | {b.id} ({b.source_id}, {b.kind}) "
                         f"\"{b.text}\"")
    body = "For each listed pair of claims (from different sources), say whether they agree, conflict or are unrelated."
    return prompts.messages("RELATE", body, prompts.RELATE_CONTRACT, [prompts.fence("PAIRS", "\n".join(lines))],
                            context, kinds=True)


def parse(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or not isinstance(data.get("relations"), list):
        return None
    return {"relations": [dict(r) for r in data["relations"] if isinstance(r, Mapping)]}


def gate(raw: Sequence[Mapping[str, Any]], asked: Mapping[str, list[tuple[Claim, Claim]]]) -> Related:
    agree: dict[str, list[tuple[str, str]]] = {}
    conflicts: dict[str, list[tuple[str, str, str]]] = {}
    rejected = 0
    seen: set[tuple[str, str, str]] = set()
    for entry in raw:
        fid, relation = str(entry.get("finding") or ""), str(entry.get("relation") or "").casefold()
        pair = _asked_pair(asked.get(fid, []), str(entry.get("a") or ""), str(entry.get("b") or ""))
        if pair is None or relation not in ("agree", "conflict", "unrelated"):
            rejected += 1
            continue
        a, b = pair
        if (fid, a.id, b.id) in seen or relation == "unrelated":
            continue
        seen.add((fid, a.id, b.id))
        difference = " ".join(str(entry.get("difference") or "").split())[:DIFFERENCE_MAX_CHARS]
        if relation == "agree" and accepts_agree(a, b):
            agree.setdefault(fid, []).append((a.id, b.id))
        elif relation == "conflict" and accepts_conflict(a, b, difference):
            conflicts.setdefault(fid, []).append((a.id, b.id, prompts.strip_markers(difference)))
        else:
            rejected += 1
    by_finding = {fid: Relations(tuple(agree.get(fid, ())), tuple(conflicts.get(fid, ())))
                  for fid in set(agree) | set(conflicts)}
    return Related(by_finding, rejected)


def _asked_pair(asked: Sequence[tuple[Claim, Claim]], a: str, b: str) -> tuple[Claim, Claim] | None:
    for x, y in asked:
        if {x.id, y.id} == {a, b}:
            return x, y
    return None


def accepts_agree(a: Claim, b: Claim) -> bool:
    return g.overlap(a.text, b.text) >= AGREE_MIN_OVERLAP


def accepts_conflict(a: Claim, b: Claim, difference: str) -> bool:
    if a.kind not in CONFLICT_KINDS or b.kind not in CONFLICT_KINDS or not difference:
        return False
    na, nb = g.numbers(f"{a.text} {a.quote}"), g.numbers(f"{b.text} {b.quote}")
    nd = g.numbers(difference)
    if na or nb:
        if not nd <= (na | nb):
            return False  # the difference cites a number neither claim has
        return bool(nd & na or not na) and bool(nd & nb or not nb) and bool(nd)
    wa, wb, wd = g.content_words(a.text), g.content_words(b.text), g.content_words(difference)
    return bool(wd & (wa - wb)) and bool(wd & (wb - wa))
