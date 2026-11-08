"""Pure: Okapi BM25 over short documents (here: the tokens of each file path)."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence

K1 = 1.5
B = 0.75


def rank(docs: Mapping[str, Sequence[str]], query: Sequence[str]) -> list[tuple[str, float]]:
    """(doc id, score) for every doc, best first; ties keep the docs' order."""
    if not docs:
        return []
    n = len(docs)
    avg_len = sum(len(t) for t in docs.values()) / n or 1.0
    df = Counter(term for toks in docs.values() for term in set(toks))
    terms = list(dict.fromkeys(query))
    scored = [(doc_id, _score(toks, terms, df, n, avg_len)) for doc_id, toks in docs.items()]
    order = {doc_id: i for i, doc_id in enumerate(docs)}
    return sorted(scored, key=lambda item: (-item[1], order[item[0]]))


def _score(toks: Sequence[str], terms: Sequence[str], df: Counter, n: int, avg_len: float) -> float:
    tf = Counter(toks)
    length = len(toks) or 1
    total = 0.0
    for term in terms:
        freq = tf.get(term, 0)
        if not freq:
            continue
        idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
        total += idf * freq * (K1 + 1) / (freq + K1 * (1 - B + B * length / avg_len))
    return total
