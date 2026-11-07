"""Pure: replies -> scrubbed bodies, ignored boilerplate, TF-IDF docs and clusters. Zero model calls.

PII is scrubbed first, so nothing after this step (the model, the output, the stored state) ever holds an
original email, phone number, long number or greeted name."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import cluster as clustering
import scrub
from lexicon import content_tokens
from parse_replies import Reply
from vectors import Vector, centroid, dot, features, fit_idf, shingles, vectorize

MIN_TOKENS = 6
COVERED_SIMILARITY = 0.60


@dataclass(frozen=True)
class Prepared:
    replies: tuple[Reply, ...]                    # text = the scrubbed body
    names: tuple[str, ...]                        # greeted customer names (to scrub model output)
    boilerplate: tuple[tuple[str, float], ...]    # (sentence, share)
    docs: tuple[clustering.Doc, ...]              # one per clusterable reply
    doc_reply: tuple[int, ...]                    # doc index -> reply index
    too_short: int
    duplicates: int
    macro_vectors: tuple[Vector, ...]


def scrub_all(replies: Sequence[Reply]) -> tuple[tuple[Reply, ...], tuple[str, ...], tuple[tuple[str, float], ...]]:
    cleaned, names = [], []
    for reply in replies:
        text, found = scrub.scrub_pii(reply.text)
        names += found
        cleaned.append(replace(reply, text=scrub.strip_quoted_and_signature(text)))
    boilerplate = scrub.find_boilerplate([r.text for r in cleaned])
    shown = [s for s, _ in boilerplate]
    bodies = tuple(replace(r, text=scrub.strip_boilerplate(r.text, shown)) for r in cleaned)
    return bodies, tuple(dict.fromkeys(names)), boilerplate


def prepare(replies: Sequence[Reply], macros: Sequence[str] = ()) -> Prepared:
    bodies, names, boilerplate = scrub_all(replies)
    tokens = [content_tokens(r.text) for r in bodies]
    keep = [i for i, t in enumerate(tokens) if len(t) >= MIN_TOKENS]
    feats = {i: features(tokens[i]) for i in keep}
    idf, unseen = fit_idf(feats.values())
    docs = tuple(clustering.Doc(vectorize(feats[i], idf, unseen), shingles(tokens[i]), len(tokens[i])) for i in keep)
    macro_vectors = tuple(vectorize(features(content_tokens(scrub.scrub_pii(m)[0])), idf, unseen) for m in macros)
    seen: set[str] = set()
    duplicates = 0
    for reply in bodies:
        key = " ".join(reply.text.casefold().split())
        duplicates += key in seen
        seen.add(key)
    return Prepared(bodies, names, boilerplate, docs, tuple(keep), len(bodies) - len(keep), duplicates, macro_vectors)


def cluster_replies(prepared: Prepared) -> list[tuple[int, ...]]:
    """Clusters as tuples of DOC indexes."""
    return clustering.cluster(prepared.docs)


def covered_by(prepared: Prepared, doc_members: Sequence[int]) -> int | None:
    """The index of the existing macro this cluster's centroid matches (cosine >= 0.60), else None."""
    if not prepared.macro_vectors:
        return None
    center = centroid(prepared.docs[i].vector for i in doc_members)
    scores = [dot(center, m) for m in prepared.macro_vectors]
    best = max(range(len(scores)), key=lambda i: (scores[i], -i))
    return best if scores[best] >= COVERED_SIMILARITY else None
