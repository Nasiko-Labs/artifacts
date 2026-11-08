"""Pure: deterministic single-pass leader clustering with one refinement pass.

1. Replies sorted by token count (desc), then input order.
2. Each reply joins the cluster whose centroid is most similar if cosine >= SIMILARITY (0.55), or, failing
   that, a cluster whose leader it near-duplicates (5-word shingle Jaccard >= NEAR_DUPLICATE); else it starts
   a new cluster.
3. One refinement pass: a reply moves to another centroid when that is >= SIMILARITY and better than its own
   by more than REFINE_MARGIN; centroids are recomputed once.
4. One cluster-merge pass: a cluster of 2+ replies joins an earlier (larger) one whose centroid has cosine
   >= SIMILARITY with its own (wording variants of one answer split by the single pass); stop.
Centroids are kept as sum vectors with an inverted index, so a reply is compared only with clusters that
share a feature with it (O(N x K) worst case, far less in practice)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from vectors import Vector, centroid, dot, jaccard

SIMILARITY = 0.55
NEAR_DUPLICATE = 0.7
REFINE_MARGIN = 0.05


@dataclass(frozen=True)
class Doc:
    vector: Vector
    shingles: frozenset
    token_count: int


class _Centroids:
    """Sum vectors per cluster plus an inverted index term -> clusters. Local to one clustering run."""

    def __init__(self) -> None:
        self.sums: list[dict[str, float]] = []
        self.square: list[float] = []
        self.index: dict[str, set[int]] = {}

    def dots(self, vector: Mapping[str, float]) -> dict[int, float]:
        found: dict[int, float] = {}
        for term, weight in vector.items():
            for c in self.index.get(term, ()):
                found[c] = found.get(c, 0.0) + weight * self.sums[c][term]
        return found

    def cosine(self, c: int, dot_value: float) -> float:
        return dot_value / math.sqrt(self.square[c]) if self.square[c] > 0 else 0.0

    def add(self, c: int, vector: Mapping[str, float], dot_value: float) -> None:
        if c == len(self.sums):
            self.sums.append({})
            self.square.append(0.0)
        total = self.sums[c]
        for term, weight in vector.items():
            total[term] = total.get(term, 0.0) + weight
            self.index.setdefault(term, set()).add(c)
        self.square[c] += 2 * dot_value + dot(vector, vector)


def _best(centroids: _Centroids, dots: Mapping[int, float], skip: int = -1) -> tuple[int, float]:
    best, best_cos = -1, -1.0
    for c in sorted(dots):
        if c == skip:
            continue
        cos = centroids.cosine(c, dots[c])
        if cos > best_cos + 1e-12:
            best, best_cos = c, cos
    return best, best_cos


def _first_pass(docs: Sequence[Doc]) -> list[int]:
    order = sorted(range(len(docs)), key=lambda i: (-docs[i].token_count, i))
    centroids, leaders = _Centroids(), []
    assign = [-1] * len(docs)
    for i in order:
        dots = centroids.dots(docs[i].vector)
        target, cos = _best(centroids, dots)
        if cos < SIMILARITY:
            target = next((c for c in sorted(dots)
                           if jaccard(docs[i].shingles, docs[leaders[c]].shingles) >= NEAR_DUPLICATE), -1)
        if target < 0:
            target = len(leaders)
            leaders.append(i)
        centroids.add(target, docs[i].vector, dots.get(target, 0.0))
        assign[i] = target
    return assign


def _centroids_of(docs: Sequence[Doc], assign: Sequence[int]) -> _Centroids:
    centroids = _Centroids()
    for c in range(max(assign, default=-1) + 1):
        centroids.add(c, {}, 0.0)
    for i, c in enumerate(assign):
        centroids.add(c, docs[i].vector, dot(docs[i].vector, centroids.sums[c]))
    return centroids


def _refine(docs: Sequence[Doc], assign: Sequence[int]) -> list[int]:
    centroids = _centroids_of(docs, assign)
    moved = list(assign)
    for i, current in enumerate(assign):
        dots = centroids.dots(docs[i].vector)
        own = centroids.cosine(current, dots.get(current, 0.0))
        other, cos = _best(centroids, dots, skip=current)
        if other >= 0 and cos >= SIMILARITY and cos > own + REFINE_MARGIN:
            moved[i] = other
    return moved


def _merge_similar(groups: list[list[int]], docs: Sequence[Doc]) -> list[list[int]]:
    order = sorted(range(len(groups)), key=lambda g: (-len(groups[g]), groups[g][0]))
    kept: list[list[int]] = []
    centers: list[Vector] = []
    for g in order:
        members = groups[g]
        center = centroid(docs[i].vector for i in members)
        scores = [dot(center, c) for c in centers] if len(members) >= 2 else []
        best = max(range(len(scores)), key=lambda k: (scores[k], -k)) if scores else -1
        if best >= 0 and scores[best] >= SIMILARITY:
            kept[best] = kept[best] + members
            centers[best] = centroid(docs[i].vector for i in kept[best])
        else:
            kept.append(list(members))
            centers.append(center)
    return kept


def cluster(docs: Sequence[Doc]) -> list[tuple[int, ...]]:
    """Clusters as tuples of doc indexes (ascending), ordered by their first member."""
    if not docs:
        return []
    assign = _refine(docs, _first_pass(docs))
    groups: dict[int, list[int]] = {}
    for i, c in enumerate(assign):
        groups.setdefault(c, []).append(i)
    merged = _merge_similar(list(groups.values()), docs)
    return sorted((tuple(sorted(members)) for members in merged), key=lambda m: m[0])


def medoid(members: Sequence[int], docs: Sequence[Doc]) -> int:
    """The member with the highest mean cosine to the others (v . sum - 1 for unit vectors); ties: earliest."""
    total: dict[str, float] = {}
    for i in members:
        for term, weight in docs[i].vector.items():
            total[term] = total.get(term, 0.0) + weight
    return max(sorted(members), key=lambda i: (round(dot(docs[i].vector, total), 9), -i))


def nearest(members: Sequence[int], center: int, docs: Sequence[Doc], k: int) -> list[int]:
    """The `k` members most similar to `center` (excluding it), ties by index."""
    others = [i for i in members if i != center]
    return sorted(others, key=lambda i: (-round(dot(docs[i].vector, docs[center].vector), 9), i))[:k]
