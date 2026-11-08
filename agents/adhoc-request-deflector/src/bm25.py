"""Pure: BM25 (k1 1.2, b 0.75) over the index rows' tokens, scored for the request's metric and
dimension terms. The shortlist is the top 5 rows with a score above 0, best first, ties in index order."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

from index import tokens
from models import Row

K1 = 1.2
B = 0.75
SHORTLIST = 5


def scores(rows: Sequence[Row], terms: Sequence[str]) -> list[float]:
    docs = [tokens(r.text) for r in rows]
    if not docs:
        return []
    avg = sum(len(d) for d in docs) / len(docs) or 1.0
    query = list(dict.fromkeys(t for term in terms for t in tokens(term)))
    df = Counter(t for d in docs for t in set(d))
    n = len(docs)
    out = []
    for doc in docs:
        tf = Counter(doc)
        total = 0.0
        for term in query:
            if not tf[term]:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            total += idf * tf[term] * (K1 + 1) / (tf[term] + K1 * (1 - B + B * len(doc) / avg))
        out.append(total)
    return out


def shortlist(rows: Sequence[Row], terms: Sequence[str]) -> tuple[tuple[Row, float], ...]:
    ranked = sorted(zip(rows, scores(rows, terms), strict=True), key=lambda p: -p[1])
    return tuple((row, score) for row, score in ranked if score > 0)[:SHORTLIST]
