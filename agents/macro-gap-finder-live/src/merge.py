"""Merge step (at most one model call): only when there are >= 12 clusters of size >= min. The model sees one
code-chosen label sentence per cluster (its medoid, first 200 chars) and groups clusters that answer the same
question; code accepts a group only when every pair of its centroids has cosine >= 0.30, then unions members."""

from __future__ import annotations

import itertools
from collections.abc import Sequence

import cluster as clustering
import prompts
from llm import LlmError, TurnDeps, complete_json
from prepare import Prepared
from vectors import centroid, dot

MERGE_MIN_CLUSTERS = 12
MERGE_MAX_SHOWN = 40
MERGE_MIN_COSINE = 0.30
LABEL_CHARS = 200


def needed(clusters: Sequence[Sequence[int]], min_freq: int) -> bool:
    return sum(1 for c in clusters if len(c) >= min_freq) >= MERGE_MIN_CLUSTERS


def _validate(known: set[str]):
    def check(data) -> dict | None:
        if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
            return None
        groups, used = [], set()
        for group in data["groups"]:
            if not isinstance(group, list):
                continue
            ids = [str(g) for g in group if str(g) in known and str(g) not in used]
            if len(set(ids)) >= 2:
                groups.append(list(dict.fromkeys(ids)))
                used.update(ids)
        return {"groups": groups}
    return check


def accept(groups: Sequence[Sequence[int]], clusters: Sequence[Sequence[int]], prepared: Prepared) -> list[list[int]]:
    """Groups (as cluster positions) whose centroids are pairwise >= MERGE_MIN_COSINE."""
    centers = {i: centroid(prepared.docs[d].vector for d in clusters[i]) for g in groups for i in g}
    return [list(g) for g in groups
            if all(dot(centers[a], centers[b]) >= MERGE_MIN_COSINE for a, b in itertools.combinations(g, 2))]


def apply(clusters: Sequence[Sequence[int]], accepted: Sequence[Sequence[int]]) -> list[tuple[int, ...]]:
    merged_into = {i: g[0] for g in accepted for i in g}
    unions: dict[int, list[int]] = {}
    for i, members in enumerate(clusters):
        unions.setdefault(merged_into.get(i, i), []).extend(members)
    return sorted((tuple(sorted(m)) for m in unions.values()), key=lambda m: m[0])


async def merge(deps: TurnDeps, clusters: list[tuple[int, ...]], prepared: Prepared,
                min_freq: int) -> tuple[list[tuple[int, ...]], int]:
    """(clusters after merging, how many groups were merged)."""
    if not needed(clusters, min_freq):
        return clusters, 0
    shown = sorted((i for i, c in enumerate(clusters) if len(c) >= 2), key=lambda i: (-len(clusters[i]), i))
    shown = shown[:MERGE_MAX_SHOWN]
    ids = {f"c{n}": i for n, i in enumerate(shown, start=1)}
    labels = []
    for cid, i in ids.items():
        center = clustering.medoid(clusters[i], prepared.docs)
        labels.append((cid, prepared.replies[prepared.doc_reply[center]].text[:LABEL_CHARS]))
    try:
        answer = await complete_json(deps, prompts.merge_messages(labels), prompts.MERGE_CONTRACT, _validate(set(ids)))
    except LlmError:
        return clusters, 0
    groups = [[ids[cid] for cid in group] for group in answer["groups"]]
    accepted = accept(groups, clusters, prepared)
    return (apply(clusters, accepted), len(accepted)) if accepted else (clusters, 0)
