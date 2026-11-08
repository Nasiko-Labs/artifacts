"""Pure: file scores and buckets. The model's vote is one of four signals; it never scores.

    score = 0.30·bm25_norm + 0.25·search_norm + 0.25·model_vote + 0.20·mention
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from candidates import Candidate

W_BM25, W_SEARCH, W_VOTE, W_MENTION = 0.30, 0.25, 0.25, 0.20
HIGH, MEDIUM = 0.70, 0.40
MAX_RANKED_VOTES = 8
HITS_FOR_FULL_SEARCH = 3
MAX_SUSPECTS = 8


@dataclass(frozen=True)
class Scored:
    path: str
    score: float
    bucket: str
    signals: tuple[str, ...]
    model_rank: int


def model_vote(rank: int) -> float:
    return (MAX_RANKED_VOTES + 1 - rank) / MAX_RANKED_VOTES if 1 <= rank <= MAX_RANKED_VOTES else 0.0


def bucket(score: float) -> str:
    return "high" if score >= HIGH else "medium" if score >= MEDIUM else "low"


def score_files(candidates: Sequence[Candidate], model_ranks: Mapping[str, int]) -> tuple[Scored, ...]:
    """Every candidate scored, highest first (ties: model rank, then path)."""
    top_bm25 = max((c.bm25 for c in candidates), default=0.0)
    scored = [_score(c, top_bm25, model_ranks.get(c.path, 0)) for c in candidates]
    return tuple(sorted(scored, key=lambda s: (-s.score, s.model_rank or 99, s.path)))


def suspects(candidates: Sequence[Candidate], model_ranks: Mapping[str, int]) -> tuple[Scored, ...]:
    """Up to 8 files: those with any signal beyond zero, highest score first."""
    return tuple(s for s in score_files(candidates, model_ranks) if s.score > 0)[:MAX_SUSPECTS]


def _score(c: Candidate, top_bm25: float, rank: int) -> Scored:
    bm25_norm = c.bm25 / top_bm25 if top_bm25 > 0 else 0.0
    search_norm = min(1.0, c.hits / HITS_FOR_FULL_SEARCH)
    vote = model_vote(rank)
    mention = 1.0 if c.mentioned else 0.0
    value = round(W_BM25 * bm25_norm + W_SEARCH * search_norm + W_VOTE * vote + W_MENTION * mention, 2)
    signals = _signals(c, bm25_norm)
    label = bucket(value)
    if vote > 0 and not signals:
        label = "low"  # a model-only pick no other signal supports
    return Scored(c.path, value, label, signals, rank)


def _signals(c: Candidate, bm25_norm: float) -> tuple[str, ...]:
    out = []
    if c.mentioned:
        out.append("named in the issue")
    if c.hits:
        out.append(f"{c.hits} code-search hit" + ("s" if c.hits != 1 else ""))
    if bm25_norm > 0:
        out.append("path match")
    return tuple(out)
