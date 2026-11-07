"""Pure: which hypothesis types the computed evidence supports, each with code's score, the evidence ids it
rests on and code's own wording. A model hypothesis of a type with no candidate is dropped; the final order
is by these scores, never the model's.

Scores: segment_concentrated = top-1 share; mix_shift = mix share (eligible from 30% or on a Simpson flag);
rate_change_within_segment = rate share (from 30%); broad_based = 1 − top-3 coverage; event_aligned =
0.5 + 0.5 × the matching segment's top-1 share (or the broad score for an event naming no segment);
new_or_vanished_segment = its share of gross movement; measurement_artifact = 0.3, or 0.6 when a top mover has
n < min_n or the change is within noise (mandatory when any data-quality trigger fires)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import analysis as an
import events as ev
import htypes
from decompose import Breakdown, Seg
from evidence import Evidence

MIN_SPLIT_SHARE = 0.3
MIN_TOP2 = 0.5


@dataclass(frozen=True)
class Candidate:
    type: str
    score: float
    evidence_ids: tuple[str, ...]
    dimension: str = ""
    segment: str = ""
    event_id: str = ""
    triggered: bool = False      # measurement_artifact only: a data-quality trigger fired
    text: str = ""
    how: str = ""


class _Index:
    def __init__(self, evidence: Sequence[Evidence]):
        self._by = {(e.kind, e.dimension, e.segment): e.id for e in evidence}

    def get(self, kind: str, dim: str = "", seg: str = "") -> str | None:
        return self._by.get((kind, dim, seg))


def build(result: an.Analysis, evidence: Sequence[Evidence], events: Sequence[ev.Event]) -> list[Candidate]:
    index = _Index(evidence)
    out: list[Candidate] = []
    for b in result.dims:
        out += _for_dimension(b, index)
    span = ev.window(result.frame.labels)
    out += [c for e in events if ev.in_window(e, span) for c in _event(result, e, index)]
    out.append(_artifact(result, evidence, index))
    return out


def _make(kind: str, score: float, ids: Sequence[str | None], b: Breakdown | None = None, seg: Seg | None = None,
          **extra) -> Candidate | None:
    clean = tuple(i for i in ids if i)
    if not clean:
        return None
    values = {"dimension": b.dim if b else "", "segment": seg.name if seg else "",
              "appeared": "appeared" if seg and seg.new else "disappeared", **{k: str(v) for k, v in extra.items()}}
    return Candidate(kind, round(score, 4), clean, values["dimension"], values["segment"],
                     text=htypes.fill(htypes.DEFAULT_TEXT[kind], **values),
                     how=htypes.fill(htypes.LIBRARY[kind], **values),
                     **{k: v for k, v in extra.items() if k in ("event_id", "triggered")})


def _for_dimension(b: Breakdown, index: _Index) -> list[Candidate]:
    found: list[Candidate | None] = []
    conc = index.get("concentration", b.dim)
    if b.segs and b.conc.gross and b.conc.top2 >= MIN_TOP2:
        top = b.segs[0]
        found.append(_make("segment_concentrated", b.conc.top1, [conc, index.get("segment", b.dim, top.name)], b, top))
    if b.kind == "rate" and (b.mix_share >= MIN_SPLIT_SHARE or b.simpson):
        seg = max(b.segs, key=lambda s: abs(s.mix))
        ids = [index.get("split", b.dim), index.get("simpson", b.dim), index.get("segment", b.dim, seg.name)]
        found.append(_make("mix_shift", b.mix_share, ids, b, seg))
    if b.kind == "rate" and b.rate_share >= MIN_SPLIT_SHARE:
        seg = max(b.segs, key=lambda s: abs(s.rate))
        ids = [index.get("split", b.dim), index.get("segment", b.dim, seg.name)]
        found.append(_make("rate_change_within_segment", b.rate_share, ids, b, seg))
    if b.conc.label == "broad":
        found.append(_make("broad_based", 1 - b.conc.top3, [conc], b))
    for seg in (s for s in b.segs if s.new or s.vanished):
        seg_id = index.get("segment", b.dim, seg.name) or index.get("new", b.dim, seg.name)
        share = abs(seg.effect) / b.conc.gross if b.conc.gross else 0.0
        found.append(_make("new_or_vanished_segment", share, [seg_id], b, seg))
    return [c for c in found if c is not None]


def _event(result: an.Analysis, event: ev.Event, index: _Index) -> list[Candidate]:
    named = set(ev.named_segments(event, result.frame.segment_names()))
    extra = {"event_id": event.id, "event": event.text, "event_date": event.date_text or event.when or "the event"}
    if not named:
        top = result.top
        broad = 1 - top.conc.top3
        made = _make("event_aligned", 0.5 + 0.5 * broad, ["e1", index.get("concentration", top.dim)], top, **extra)
        return [made] if made else []
    for b in result.dims:
        moved = [s for s in b.segs[:3] if s.name in named]
        if moved:
            ids = [index.get("concentration", b.dim), index.get("segment", b.dim, moved[0].name)]
            made = _make("event_aligned", 0.5 + 0.5 * b.conc.top1, ids, b, moved[0], **extra)
            return [made] if made else []
    return []


def _artifact(result: an.Analysis, evidence: Sequence[Evidence], index: _Index) -> Candidate:
    small = result.small_movers()
    newish = result.new_in_top3()
    noise = result.significance == "within noise"
    triggered = bool(small or newish or noise or an.is_mismatch(result))
    top_small = bool(small) and small[0][1].name == result.top.segs[0].name
    ids = [e.id for e in evidence if e.kind == "quality"]
    ids += [index.get("segment", result.top.dim, s.name) for s in newish]
    if noise or not ids:
        ids.insert(0, "e1")
    made = _make("measurement_artifact", 0.6 if (top_small or noise) else 0.3, ids, triggered=triggered)
    return made
