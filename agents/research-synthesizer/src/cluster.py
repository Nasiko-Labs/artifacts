"""Cluster: group checked claims into findings. One call (claims bucketed by topic hint into up to
MAX_CLUSTER_CALLS calls when there are more than BUCKET_CLAIMS). Code keeps only known claim ids, gives a claim
to the first finding that names it, drops findings left with no claim, and counts claims left out. A statement
that adds a number the claims don't contain is replaced by its first claim's text. If clustering fails, claims
are grouped by their topic hint in code."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import grounding as g
import llm
import prompts
from extract import cut_words
from model import Claim

log = logging.getLogger("research-synthesizer.cluster")

MAX_FINDINGS = 12
BUCKET_CLAIMS = 150
MAX_CLUSTER_CALLS = 4
STATEMENT_MAX_WORDS = 40


@dataclass(frozen=True)
class Group:
    statement: str
    claim_ids: tuple[str, ...]


@dataclass(frozen=True)
class Clustered:
    groups: tuple[Group, ...]
    unclustered: int = 0
    empty: int = 0
    fallback: bool = False


async def cluster(deps: llm.TurnDeps, question: str, claims: Sequence[Claim], context: Sequence[str] = ()) -> Clustered:
    raw: list[Mapping[str, Any]] = []
    try:
        for bucket in buckets(claims):
            parsed = await llm.complete_json(deps, _messages(question, bucket, context), prompts.CLUSTER_CONTRACT, parse)
            raw += parsed["findings"]
    except llm.LlmError:
        log.warning("cluster failed; grouping by topic hint")
        return by_hint(claims)
    return apply(raw, claims)


def buckets(claims: Sequence[Claim]) -> list[list[Claim]]:
    if len(claims) <= BUCKET_CLAIMS:
        return [list(claims)]
    ordered = sorted(claims, key=lambda c: (c.topic_hint.casefold(), int(c.id[1:])))
    size = max(BUCKET_CLAIMS, -(-len(ordered) // MAX_CLUSTER_CALLS))
    return [ordered[i:i + size] for i in range(0, len(ordered), size)]


def _messages(question: str, claims: Sequence[Claim], context: Sequence[str]) -> list[dict]:
    block = "\n".join(f"{c.id} ({c.source_id}) [{c.kind}] {c.text}" for c in claims)
    body = "Group the claims in the CLAIMS block into findings: claims that make the same point go together."
    return prompts.messages("CLUSTER", body, prompts.CLUSTER_CONTRACT,
                            [prompts.question_block(question), prompts.fence("CLAIMS", block)], context, kinds=True)


def parse(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or not isinstance(data.get("findings"), list):
        return None
    return {"findings": [dict(f) for f in data["findings"] if isinstance(f, Mapping)]}


def apply(raw: Sequence[Mapping[str, Any]], claims: Sequence[Claim]) -> Clustered:
    by_id = {c.id: c for c in claims}
    taken: set[str] = set()
    groups: list[Group] = []
    empty = 0
    for entry in raw:
        ids = entry.get("claim_ids") if isinstance(entry.get("claim_ids"), list) else []
        mine = tuple(dict.fromkeys(str(i) for i in ids if str(i) in by_id and str(i) not in taken))
        if not mine:
            empty += 1
            continue
        if len(groups) >= MAX_FINDINGS:
            continue
        taken.update(mine)
        groups.append(Group(_statement(entry.get("statement"), [by_id[i] for i in mine]), mine))
    return Clustered(tuple(groups), unclustered=len(by_id) - len(taken), empty=empty)


def _statement(raw: Any, claims: Sequence[Claim]) -> str:
    text = " ".join(str(raw or "").split())
    evidence = g.normalise(" ".join(f"{c.text} {c.quote}" for c in claims))
    if not text or g.ungrounded_tokens(text, evidence):
        text = claims[0].text
    return cut_words(prompts.strip_markers(text), STATEMENT_MAX_WORDS)


def by_hint(claims: Sequence[Claim]) -> Clustered:
    hints: dict[str, list[Claim]] = {}
    for c in claims:
        hints.setdefault(c.topic_hint.casefold() or c.id, []).append(c)
    ranked = sorted(hints.values(), key=lambda cs: (-len({c.source_id for c in cs}), -len(cs), int(cs[0].id[1:])))
    groups = tuple(Group(cs[0].text, tuple(c.id for c in cs)) for cs in ranked[:MAX_FINDINGS])
    kept = sum(len(gr.claim_ids) for gr in groups)
    return Clustered(groups, unclustered=len(claims) - kept, fallback=True)
