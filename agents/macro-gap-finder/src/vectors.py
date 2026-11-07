"""Pure: TF-IDF vectors (word unigrams + bigrams, smoothed idf over the batch, L2-normalised), cosine
similarity and 5-word shingles. Sparse dicts, no third-party library, deterministic."""

from __future__ import annotations

import itertools
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

Vector = dict[str, float]
SHINGLE_WORDS = 5


def features(tokens: Sequence[str]) -> list[str]:
    return [*tokens, *(f"{a} {b}" for a, b in itertools.pairwise(tokens))]


def fit_idf(docs: Iterable[Sequence[str]]) -> tuple[dict[str, float], float]:
    """(idf per feature, the idf of a feature no document has): log((1 + N) / (1 + df)) + 1."""
    df: Counter[str] = Counter()
    n = 0
    for doc in docs:
        n += 1
        df.update(set(doc))
    idf = {term: math.log((1 + n) / (1 + count)) + 1 for term, count in df.items()}
    return idf, math.log(1 + n) + 1


def vectorize(feats: Sequence[str], idf: Mapping[str, float], unseen_idf: float) -> Vector:
    weights = {term: count * idf.get(term, unseen_idf) for term, count in Counter(feats).items()}
    return normalise(weights)


def normalise(vector: Mapping[str, float]) -> Vector:
    length = math.sqrt(sum(w * w for w in vector.values()))
    return {t: w / length for t, w in vector.items()} if length else {}


def dot(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return sum(w * big.get(t, 0.0) for t, w in small.items())


def add_into(total: dict[str, float], vector: Mapping[str, float]) -> dict[str, float]:
    """A new sum vector (the input is not changed)."""
    merged = dict(total)
    for term, weight in vector.items():
        merged[term] = merged.get(term, 0.0) + weight
    return merged


def centroid(vectors: Iterable[Mapping[str, float]]) -> Vector:
    total: dict[str, float] = {}
    for vector in vectors:
        for term, weight in vector.items():
            total[term] = total.get(term, 0.0) + weight
    return normalise(total)


def shingles(tokens: Sequence[str], size: int = SHINGLE_WORDS) -> frozenset[tuple[str, ...]]:
    if len(tokens) < size:
        return frozenset({tuple(tokens)}) if tokens else frozenset()
    return frozenset(tuple(tokens[i:i + size]) for i in range(len(tokens) - size + 1))


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
